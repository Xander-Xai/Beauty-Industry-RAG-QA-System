"""
Generation 路由模块 — 主编排路由器.

负责完整的 RAG 管线编排：
1. 身份认证
2. 缓存查询
3. 准入控制
4. 查询改写
5. 多路召回
6. 重排
7. 证据门控
8. 答案生成
9. 准入释放
10. 写入缓存
11. 返回完整响应

同时包含旧版 /api/* 路由：续写、对话历史、媒体访问。
"""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 确保 api-gateway 目录在 sys.path 中，以便导入同级 middleware 模块
_GATEWAY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _GATEWAY_DIR not in sys.path:
    sys.path.insert(0, _GATEWAY_DIR)

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from common.models import (
    UserIdentity,
    QueryRewriteResult,
    EvidenceGateResult,
    GenerationResult,
    AnswerGateResult,
)
from middleware.auth_middleware import get_current_user, require_current_user
from middleware.rate_limiter import rate_limit_dependency

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# 下游服务地址（可通过环境变量覆盖）
# ---------------------------------------------------------------------------
REWRITE_SERVICE_URL = os.environ.get("REWRITE_SERVICE_URL", "http://rewrite-service:8101")
RETRIEVAL_SERVICE_URL = os.environ.get("RETRIEVAL_SERVICE_URL", "http://retrieval-service:8200")
GENERATION_SERVICE_URL = os.environ.get("GENERATION_SERVICE_URL", "http://generation-service:8100")
CACHE_SERVICE_URL = os.environ.get("CACHE_SERVICE_URL", "http://cache-service:8300")

# 超时设置（秒）
CACHE_LOOKUP_TIMEOUT_S = float(os.environ.get("CACHE_LOOKUP_TIMEOUT_S", "2"))
ADMISSION_CHECK_TIMEOUT_S = float(os.environ.get("ADMISSION_CHECK_TIMEOUT_S", "5"))
REWRITE_TIMEOUT_S = float(os.environ.get("REWRITE_TIMEOUT_S", "0.045"))  # 45ms
RECALL_TIMEOUT_S = float(os.environ.get("RECALL_TIMEOUT_S", "10"))
RERANK_TIMEOUT_S = float(os.environ.get("RERANK_TIMEOUT_S", "15"))
EVIDENCE_GATE_TIMEOUT_S = float(os.environ.get("EVIDENCE_GATE_TIMEOUT_S", "5"))
GENERATE_TIMEOUT_S = float(os.environ.get("GENERATE_TIMEOUT_S", "30"))
ADMISSION_RELEASE_TIMEOUT_S = float(os.environ.get("ADMISSION_RELEASE_TIMEOUT_S", "3"))
CACHE_WRITE_TIMEOUT_S = float(os.environ.get("CACHE_WRITE_TIMEOUT_S", "2"))


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------
class GenerateRequest(BaseModel):
    """主生成请求体。"""
    query: str = Field(..., description="用户查询")
    session_id: Optional[str] = Field(default=None, description="会话 ID（用于多轮对话）")
    recent_dialogs: List[str] = Field(default_factory=list, description="近期对话历史")
    context: Dict[str, Any] = Field(default_factory=dict, description="附加上下文")


class GenerateResponse(BaseModel):
    """主生成响应体。"""
    request_id: str = ""
    success: bool = True
    answer: str = ""
    rewritten_query: str = ""
    business_type: str = "general"
    confidence: float = 0.5
    evidence_decision: str = "high_confidence"
    model_used: str = ""
    has_more: bool = False
    session_id: Optional[str] = None
    answer_outline: List[str] = Field(default_factory=list)
    from_cache: bool = False
    latency_ms: float = 0.0
    error: Optional[str] = None


class ContinuationRequest(BaseModel):
    """长文续写请求体。"""
    session_id: str = Field(..., description="会话 ID")
    outline_index: int = Field(default=0, description="续写大纲索引")


class MediaResponse(BaseModel):
    """媒体访问响应体。"""
    doc_id: str
    media_type: str = ""
    url: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
async def _call_service(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    json_body: Any = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 5.0,
) -> httpx.Response:
    """
    统一的服务间调用封装，处理超时和连接错误。

    Args:
        client: httpx 异步客户端
        method: HTTP 方法
        url: 完整的请求 URL
        json_body: JSON 请求体
        params: 查询参数
        timeout: 超时秒数

    Returns:
        httpx.Response

    Raises:
        HTTPException: 502 下游服务错误
    """
    try:
        if method.upper() == "GET":
            resp = await client.get(url, params=params, timeout=timeout)
        else:
            resp = await client.post(url, json=json_body, timeout=timeout)
        return resp

    except httpx.TimeoutException:
        logger.error("服务调用超时: %s (超时: %.1fs)", url, timeout)
        raise HTTPException(status_code=504, detail=f"下游服务超时: {url}")

    except httpx.ConnectError:
        logger.error("无法连接下游服务: %s", url)
        raise HTTPException(status_code=502, detail=f"下游服务不可达: {url}")


# ===========================================================================
# POST /v1/generate — 主编排端点
# ===========================================================================
@router.post("/generate", response_model=GenerateResponse)
async def generate(
    body: GenerateRequest,
    user: UserIdentity = Depends(get_current_user),
    _rate_limit: None = Depends(rate_limit_dependency),
):
    """
    完整的 RAG 管线编排端点.

    按顺序调用各下游服务，任何非关键步骤失败时执行降级处理。
    关键步骤（生成）失败时返回 5xx 错误。
    """
    request_id = str(uuid.uuid4())
    pipeline_start = time.monotonic()
    logger.info("[%s] 开始 RAG 管线, 用户: %s, 查询: %s", request_id, user.user_id, body.query[:80])

    async with httpx.AsyncClient() as client:

        # ── 步骤 1: 缓存查询 ────────────────────────────────────────────
        cache_result = None
        try:
            resp = await _call_service(
                client, "GET",
                f"{CACHE_SERVICE_URL}/api/cache",
                params={"query": body.query, "user_id": user.user_id},
                timeout=CACHE_LOOKUP_TIMEOUT_S,
            )
            if resp.status_code == 200:
                cache_data = resp.json()
                if cache_data.get("hit"):
                    cache_result = cache_data.get("value")
                    elapsed_ms = (time.monotonic() - pipeline_start) * 1000
                    logger.info("[%s] 缓存命中: %.1fms", request_id, elapsed_ms)
                    return GenerateResponse(
                        request_id=request_id,
                        success=True,
                        answer=cache_result.get("answer", ""),
                        rewritten_query=body.query,
                        business_type=cache_result.get("business_type", "general"),
                        from_cache=True,
                        latency_ms=elapsed_ms,
                    )
        except HTTPException:
            # 缓存查询失败不阻断流程，继续后续步骤
            logger.warning("[%s] 缓存查询失败，继续管线", request_id)

        # ── 步骤 2: 准入控制检查 ─────────────────────────────────────────
        admission_token: Optional[str] = None
        try:
            resp = await _call_service(
                client, "POST",
                f"{GENERATION_SERVICE_URL}/api/admission-check",
                json_body={"user_id": user.user_id, "query": body.query},
                timeout=ADMISSION_CHECK_TIMEOUT_S,
            )
            if resp.status_code == 200:
                admission_data = resp.json()
                admission_token = admission_data.get("token")
                if not admission_data.get("admitted", True):
                    elapsed_ms = (time.monotonic() - pipeline_start) * 1000
                    logger.warning("[%s] 准入控制拒绝: %.1fms", request_id, elapsed_ms)
                    return GenerateResponse(
                        request_id=request_id,
                        success=False,
                        answer="系统当前负载较高，请稍后重试",
                        error="准入控制拒绝",
                        latency_ms=elapsed_ms,
                    )
        except HTTPException:
            logger.warning("[%s] 准入控制检查失败，继续管线", request_id)

        # ── 步骤 3: 查询改写 ─────────────────────────────────────────────
        rewrite_result = QueryRewriteResult(rewritten_query=body.query)
        try:
            resp = await _call_service(
                client, "POST",
                f"{REWRITE_SERVICE_URL}/api/rewrite",
                json_body={"query": body.query, "recent_dialogs": body.recent_dialogs},
                timeout=REWRITE_TIMEOUT_S,
            )
            if resp.status_code == 200:
                data = resp.json()
                rewrite_result = QueryRewriteResult(**data)
        except HTTPException:
            # 改写超时/失败时使用原始查询
            logger.warning("[%s] 查询改写失败，使用原始查询", request_id)

        effective_query = rewrite_result.rewritten_query or body.query
        business_type = rewrite_result.business_type or "general"

        # ── 步骤 4: 多路召回 ─────────────────────────────────────────────
        recall_candidates: List[Dict[str, Any]] = []
        try:
            resp = await _call_service(
                client, "POST",
                f"{RETRIEVAL_SERVICE_URL}/api/recall",
                json_body={
                    "query": effective_query,
                    "rewritten_query": effective_query,
                    "user_role_mask": user.user_role_mask,
                    "user_dept_mask": user.user_dept_mask,
                    "business_type": business_type,
                    "context": body.context,
                },
                timeout=RECALL_TIMEOUT_S,
            )
            if resp.status_code == 200:
                recall_data = resp.json()
                recall_candidates = recall_data.get("candidates", [])
        except HTTPException:
            logger.warning("[%s] 召回失败，将使用空候选集", request_id)

        if not recall_candidates:
            elapsed_ms = (time.monotonic() - pipeline_start) * 1000
            logger.warning("[%s] 召回无结果: %.1fms", request_id, elapsed_ms)
            # 尝试无检索生成（仅用 LLM 知识）
            return await _generate_without_context(
                client, request_id, body, user, pipeline_start,
                rewrite_result, admission_token,
            )

        # ── 步骤 5: 重排 ────────────────────────────────────────────────
        reranked_candidates: List[Dict[str, Any]] = recall_candidates
        try:
            resp = await _call_service(
                client, "POST",
                f"{RETRIEVAL_SERVICE_URL}/api/rerank",
                json_body={
                    "query": effective_query,
                    "candidates": recall_candidates,
                    "top_k": 10,
                    "context": body.context,
                },
                timeout=RERANK_TIMEOUT_S,
            )
            if resp.status_code == 200:
                rerank_data = resp.json()
                reranked_candidates = rerank_data.get("candidates", recall_candidates)
        except HTTPException:
            logger.warning("[%s] 重排失败，使用召回原始排序", request_id)

        # ── 步骤 6: 证据门控 ─────────────────────────────────────────────
        evidence_result = EvidenceGateResult(decision="high_confidence")
        try:
            resp = await _call_service(
                client, "POST",
                f"{RETRIEVAL_SERVICE_URL}/api/evidence-gate",
                json_body={
                    "query": effective_query,
                    "candidates": reranked_candidates,
                    "business_type": business_type,
                    "context": body.context,
                },
                timeout=EVIDENCE_GATE_TIMEOUT_S,
            )
            if resp.status_code == 200:
                data = resp.json()
                evidence_result = EvidenceGateResult(**data)
        except HTTPException:
            logger.warning("[%s] 证据门控失败，默认放行", request_id)

        # 如果证据门控拒绝，不进行生成
        if evidence_result.decision == "reject":
            elapsed_ms = (time.monotonic() - pipeline_start) * 1000
            logger.info("[%s] 证据门控拒绝: %.1fms", request_id, elapsed_ms)
            return GenerateResponse(
                request_id=request_id,
                success=True,
                answer="抱歉，未能找到与您问题相关的可靠信息。请尝试换一种方式提问。",
                rewritten_query=effective_query,
                business_type=business_type,
                confidence=evidence_result.evidence_score,
                evidence_decision=evidence_result.decision,
                latency_ms=elapsed_ms,
            )

        # ── 步骤 7: 答案生成 ─────────────────────────────────────────────
        generation_result = GenerationResult()
        try:
            resp = await _call_service(
                client, "POST",
                f"{GENERATION_SERVICE_URL}/api/generate",
                json_body={
                    "query": effective_query,
                    "original_query": body.query,
                    "session_id": body.session_id,
                    "candidates": reranked_candidates,
                    "business_type": business_type,
                    "evidence_decision": evidence_result.decision,
                    "user_role_mask": user.user_role_mask,
                    "user_dept_mask": user.user_dept_mask,
                    "recent_dialogs": body.recent_dialogs,
                    "context": body.context,
                },
                timeout=GENERATE_TIMEOUT_S,
            )
            resp.raise_for_status()
            data = resp.json()
            generation_result = GenerationResult(**data)
        except httpx.HTTPStatusError as exc:
            logger.error("[%s] 生成失败: HTTP %d", request_id, exc.response.status_code)
            raise HTTPException(
                status_code=500,
                detail="答案生成服务返回错误",
            )

        # ── 步骤 8: 准入释放 ─────────────────────────────────────────────
        if admission_token:
            try:
                await _call_service(
                    client, "POST",
                    f"{GENERATION_SERVICE_URL}/api/admission-release",
                    json_body={"token": admission_token},
                    timeout=ADMISSION_RELEASE_TIMEOUT_S,
                )
            except HTTPException:
                logger.warning("[%s] 准入释放失败（不影响响应）", request_id)

        # ── 步骤 9: 写入缓存 ─────────────────────────────────────────────
        try:
            await _call_service(
                client, "POST",
                f"{CACHE_SERVICE_URL}/api/cache",
                json_body={
                    "query": body.query,
                    "value": {
                        "answer": generation_result.answer,
                        "business_type": business_type,
                    },
                    "user_id": user.user_id,
                    "role_mask": user.user_role_mask,
                    "dept_mask": user.user_dept_mask,
                },
                timeout=CACHE_WRITE_TIMEOUT_S,
            )
        except HTTPException:
            logger.warning("[%s] 缓存写入失败（不影响响应）", request_id)

        # ── 步骤 10: 返回响应 ────────────────────────────────────────────
        elapsed_ms = (time.monotonic() - pipeline_start) * 1000
        logger.info(
            "[%s] RAG 管线完成: %.1fms, 模型: %s",
            request_id, elapsed_ms, generation_result.model_used,
        )

        return GenerateResponse(
            request_id=request_id,
            success=True,
            answer=generation_result.answer,
            rewritten_query=effective_query,
            business_type=business_type,
            confidence=evidence_result.evidence_score,
            evidence_decision=evidence_result.decision,
            model_used=generation_result.model_used,
            has_more=generation_result.has_more,
            session_id=generation_result.session_id or body.session_id,
            answer_outline=generation_result.answer_outline,
            from_cache=False,
            latency_ms=elapsed_ms,
        )


async def _generate_without_context(
    client: httpx.AsyncClient,
    request_id: str,
    body: GenerateRequest,
    user: UserIdentity,
    pipeline_start: float,
    rewrite_result: QueryRewriteResult,
    admission_token: Optional[str],
) -> GenerateResponse:
    """
    无检索结果时的降级生成：仅基于 LLM 内部知识回答.

    当召回阶段未返回任何候选文档时调用此方法。
    """
    logger.info("[%s] 无检索结果，尝试无上下文生成", request_id)

    try:
        resp = await _call_service(
            client, "POST",
            f"{GENERATION_SERVICE_URL}/api/generate",
            json_body={
                "query": body.query,
                "original_query": body.query,
                "session_id": body.session_id,
                "candidates": [],
                "business_type": "general",
                "evidence_decision": "reject",
                "user_role_mask": user.user_role_mask,
                "user_dept_mask": user.user_dept_mask,
                "recent_dialogs": body.recent_dialogs,
                "context": body.context,
            },
            timeout=GENERATE_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
        gen_result = GenerationResult(**data)
    except (httpx.HTTPStatusError, HTTPException):
        elapsed_ms = (time.monotonic() - pipeline_start) * 1000
        return GenerateResponse(
            request_id=request_id,
            success=True,
            answer="抱歉，未能找到相关知识信息。建议您咨询专业人员获取准确答案。",
            rewritten_query=rewrite_result.rewritten_query or body.query,
            business_type="general",
            confidence=0.0,
            evidence_decision="reject",
            latency_ms=elapsed_ms,
        )

    # 释放准入令牌
    if admission_token:
        try:
            await _call_service(
                client, "POST",
                f"{GENERATION_SERVICE_URL}/api/admission-release",
                json_body={"token": admission_token},
                timeout=ADMISSION_RELEASE_TIMEOUT_S,
            )
        except HTTPException:
            pass

    elapsed_ms = (time.monotonic() - pipeline_start) * 1000
    return GenerateResponse(
        request_id=request_id,
        success=True,
        answer=gen_result.answer,
        rewritten_query=rewrite_result.rewritten_query or body.query,
        business_type="general",
        confidence=0.0,
        evidence_decision="reject",
        model_used=gen_result.model_used,
        has_more=gen_result.has_more,
        session_id=gen_result.session_id or body.session_id,
        answer_outline=gen_result.answer_outline,
        latency_ms=elapsed_ms,
    )


# ===========================================================================
# POST /api/continuation — 长文续写
# ===========================================================================
@router.post("/api/continuation")
async def continuation(
    body: ContinuationRequest,
    user: UserIdentity = Depends(require_current_user),
):
    """
    长文续写端点.

    根据已有大纲和会话上下文，生成下一段内容。
    透传至 generation-service。
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await _call_service(
                client, "POST",
                f"{GENERATION_SERVICE_URL}/api/continuation",
                json_body={
                    "session_id": body.session_id,
                    "outline_index": body.outline_index,
                    "user_id": user.user_id,
                },
                timeout=GENERATE_TIMEOUT_S,
            )
            resp.raise_for_status()
            return resp.json()

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("续写请求失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"续写失败: {exc}")


# ===========================================================================
# GET /api/dialog_history — 对话历史查询
# ===========================================================================
@router.get("/api/dialog_history")
async def dialog_history(
    session_id: str = Query(..., description="会话 ID"),
    user: UserIdentity = Depends(get_current_user),
):
    """
    查询指定会话的对话历史.

    透传至 generation-service 获取历史记录。
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await _call_service(
                client, "GET",
                f"{GENERATION_SERVICE_URL}/api/dialog_history",
                params={"session_id": session_id, "user_id": user.user_id},
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("对话历史查询失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"对话历史查询失败: {exc}")


# ===========================================================================
# GET /api/media/{doc_id} — 媒体文件访问
# ===========================================================================
@router.get("/api/media/{doc_id}")
async def media_access(
    doc_id: str,
    user: UserIdentity = Depends(require_current_user),
):
    """
    媒体文件访问端点.

    根据文档 ID 返回媒体资源信息，并执行 RBAC 权限检查。
    无权限时返回 403。
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await _call_service(
                client, "GET",
                f"{GENERATION_SERVICE_URL}/api/media/{doc_id}",
                params={"user_role_mask": user.user_role_mask, "user_dept_mask": user.user_dept_mask},
                timeout=10.0,
            )

            # 处理权限拒绝
            if resp.status_code == 403:
                return JSONResponse(
                    status_code=403,
                    content={"error": "无权访问该媒体文件", "doc_id": doc_id},
                )

            resp.raise_for_status()
            return resp.json()

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("媒体访问失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"媒体访问失败: {exc}")
