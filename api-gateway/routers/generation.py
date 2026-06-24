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

import hashlib
import logging
import os
import sys
import time
import uuid
from typing import Any

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 确保 api-gateway 目录在 sys.path 中，以便导入同级 middleware 模块
_GATEWAY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _GATEWAY_DIR not in sys.path:
    sys.path.insert(0, _GATEWAY_DIR)

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from middleware.auth_middleware import get_current_user, require_current_user
from middleware.rate_limiter import rate_limit_dependency
from pydantic import BaseModel, Field

from common.models import (
    EvidenceGateResult,
    GenerationResult,
    QueryRewriteResult,
    UserIdentity,
)
from common.config import get_config_dict

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
COMPLEXITY_TIMEOUT_S = float(os.environ.get("COMPLEXITY_TIMEOUT_S", "1"))
CACHE_LOOKUP_TIMEOUT_S = float(os.environ.get("CACHE_LOOKUP_TIMEOUT_S", "2"))
ADMISSION_CHECK_TIMEOUT_S = float(os.environ.get("ADMISSION_CHECK_TIMEOUT_S", "5"))
REWRITE_TIMEOUT_S = float(os.environ.get("REWRITE_TIMEOUT_S", "2.0"))  # 2s（LLM推理需要足够时间）
RECALL_TIMEOUT_S = float(os.environ.get("RECALL_TIMEOUT_S", "10"))
RERANK_TIMEOUT_S = float(os.environ.get("RERANK_TIMEOUT_S", "15"))
EVIDENCE_GATE_TIMEOUT_S = float(os.environ.get("EVIDENCE_GATE_TIMEOUT_S", "5"))
GENERATE_TIMEOUT_S = float(os.environ.get("GENERATE_TIMEOUT_S", "30"))
ADMISSION_RELEASE_TIMEOUT_S = float(os.environ.get("ADMISSION_RELEASE_TIMEOUT_S", "3"))
CACHE_WRITE_TIMEOUT_S = float(os.environ.get("CACHE_WRITE_TIMEOUT_S", "2"))

# ── PRD §4.7: 按 business_type 的最大输出 token 数 ───────────────────────
# 从 config.json 读取映射表；若加载失败使用默认值
_DEFAULT_MAX_OUTPUT_TOKENS = {
    "regulation": 1024,
    "development": 768,
    "ingredient": 512,
    "product": 512,
    "general": 512,
    "short": 256,
}
_MAX_OUTPUT_TOKENS_MAP: dict[str, int] = _DEFAULT_MAX_OUTPUT_TOKENS.copy()
try:
    _CFG = get_config_dict()
    _MAX_OUTPUT_TOKENS_MAP = _CFG.get("gpu0", {}).get("models", {}).get(
        "gen_14b", {}
    ).get("max_output_tokens", _DEFAULT_MAX_OUTPUT_TOKENS)
except Exception:
    pass


def _get_max_output_tokens(business_type: str) -> int:
    """PRD §4.7: 根据 business_type 返回对应的 max_output_tokens。"""
    return _MAX_OUTPUT_TOKENS_MAP.get(business_type, _DEFAULT_MAX_OUTPUT_TOKENS["general"])


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------
class GenerateRequest(BaseModel):
    """主生成请求体。"""
    query: str = Field(..., description="用户查询")
    session_id: str | None = Field(default=None, description="会话 ID（用于多轮对话）")
    recent_dialogs: list[str] = Field(default_factory=list, description="近期对话历史")
    context: dict[str, Any] = Field(default_factory=dict, description="附加上下文")


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
    session_id: str | None = None
    answer_outline: list[str] = Field(default_factory=list)
    from_cache: bool = False
    latency_ms: float = 0.0
    error: str | None = None


class ContinuationRequest(BaseModel):
    """长文续写请求体。"""
    session_id: str = Field(..., description="会话 ID")
    outline_index: int = Field(default=0, description="续写大纲索引")


class MediaResponse(BaseModel):
    """媒体访问响应体。"""
    doc_id: str
    media_type: str = ""
    url: str = ""
    error: str | None = None


# ── Frontend-facing API models (map to /api/query and /api/chat) ─────


class QueryRequest(BaseModel):
    """单轮查询请求（前端 → 网关）."""
    query: str = Field(..., min_length=1, max_length=2000, description="用户查询文本")
    session_id: str | None = Field(None, max_length=64, description="会话 ID（可选）")
    user_id: str | None = Field(None, max_length=64, description="用户 ID（可选）")
    image_path: str | None = Field(None, max_length=512, description="已弃用")


class QueryResponse(BaseModel):
    """单轮查询响应（网关 → 前端）."""
    answer: str = ""
    session_id: str | None = None
    business_type: str | None = None
    intent: str | None = None
    evidence_doc_ids: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0
    cache_hit: bool = False


class ChatRequest(BaseModel):
    """多轮对话请求（前端 → 网关）."""
    message: str = Field(..., min_length=1, max_length=2000, description="用户消息")
    session_id: str = Field(..., min_length=1, max_length=64, description="会话 ID")


class ChatMessage(BaseModel):
    """对话历史中的单条消息."""
    role: str = Field(..., description="user 或 assistant")
    content: str = Field(..., description="消息内容")


class ChatResponse(BaseModel):
    """多轮对话响应（网关 → 前端）."""
    answer: str = ""
    session_id: str = ""
    history: list[ChatMessage] = Field(default_factory=list)
    business_type: str | None = None
    intent: str | None = None
    evidence_doc_ids: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0
    cache_hit: bool = False


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
async def _call_service(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    json_body: Any = None,
    params: dict[str, Any] | None = None,
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
        raise HTTPException(status_code=504, detail="下游服务超时")

    except httpx.ConnectError:
        logger.error("无法连接下游服务: %s", url)
        raise HTTPException(status_code=502, detail="下游服务不可达")


# ---------------------------------------------------------------------------
# Cache key computation (mirrors cache/redis_cache.py RedisCache.compute_cache_key)
# ---------------------------------------------------------------------------
# Version defaults -- can be overridden via env vars
_EMBEDDING_VERSION = os.environ.get("EMBEDDING_VERSION", "")
_KNOWLEDGE_VERSION_EPOCH = os.environ.get("KNOWLEDGE_VERSION_EPOCH", "default")
_PROMPT_VERSION = os.environ.get("PROMPT_VERSION", "")
_SCHEMA_VERSION = os.environ.get("SCHEMA_VERSION", "1.0")


def _compute_cache_key(
    normalized_query: str,
    role_mask: int = 0,
    dept_mask: int = 0,
    session_id: str = None,
) -> str:
    """
    Compute a SHA-256 cache key matching RedisCache.compute_cache_key.

    GAP-18: PRD §10.6 — 当 session_id 提供时，将其加入 Key 计算，
    确保 requires_context=true 的缓存仅限同一 session 复用。
    """
    key_data = {
        "q": normalized_query,
        "ev": _EMBEDDING_VERSION,
        "ke": _KNOWLEDGE_VERSION_EPOCH,
        "pv": _PROMPT_VERSION,
        "sv": _SCHEMA_VERSION,
        "rm": role_mask,
        "dm": dept_mask,
    }
    if session_id:
        key_data["sid"] = session_id
    return hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()


def _estimate_tokens(text: str) -> int:
    """Rough token count estimate (approx 1 token per 2 CJK chars or 4 ASCII chars)."""
    cjk_count = sum(1 for ch in text if '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿')
    ascii_count = len(text) - cjk_count
    return max(1, cjk_count + ascii_count // 4)


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
            cache_key = _compute_cache_key(
                body.query,
                role_mask=user.user_role_mask,
                dept_mask=user.user_dept_mask,
            )
            resp = await _call_service(
                client, "GET",
                f"{CACHE_SERVICE_URL}/api/cache",
                params={
                    "key": cache_key,
                    "role_mask": user.user_role_mask,
                    "dept_mask": user.user_dept_mask,
                },
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
                        business_type=cache_data.get("business_type", cache_result.get("business_type", "general")),
                        from_cache=True,
                        latency_ms=elapsed_ms,
                    )
        except HTTPException:
            # 缓存查询失败不阻断流程，继续后续步骤
            logger.warning("[%s] 缓存查询失败，继续管线", request_id)

        # ── 步骤 3: 查询改写（提前至准入控制之前）─────────────────────────
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

        # ── 步骤 3.5: PRD §4.3 BERT 复杂度评估 → 模型路由 ────────────────
        target_model = "qwen3-4b"  # 默认简单模型
        try:
            resp = await _call_service(
                client, "POST",
                f"{GENERATION_SERVICE_URL}/api/complexity",
                json_body={"query": effective_query},
                timeout=COMPLEXITY_TIMEOUT_S,
            )
            if resp.status_code == 200:
                complexity_data = resp.json()
                is_complex = complexity_data.get("is_complex", False)
                target_model = "qwen3-14b" if is_complex else "qwen3-4b"
                logger.info("[%s] 复杂度评估: is_complex=%s, target_model=%s",
                            request_id, is_complex, target_model)
        except HTTPException:
            logger.warning("[%s] 复杂度评估失败，使用默认模型 4B", request_id)

        # ── PRD §4.7: 根据 business_type 确定输出长度 ─────────────────────
        max_output_tokens = _get_max_output_tokens(business_type)

        # ── 步骤 2: 准入控制检查（在改写和复杂度评估之后，确保变量已赋值）──
        admission_request_id: str | None = None
        try:
            input_tokens = _estimate_tokens(body.query)
            resp = await _call_service(
                client, "POST",
                f"{GENERATION_SERVICE_URL}/api/admission-check",
                json_body={
                    "request_id": request_id,
                    "input_tokens": input_tokens,
                    "output_tokens": max_output_tokens,
                    "business_type": business_type,
                },
                timeout=ADMISSION_CHECK_TIMEOUT_S,
            )
            if resp.status_code == 200:
                admission_data = resp.json()
                admission_request_id = request_id
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

        logger.info("[%s] business_type=%s, max_output_tokens=%d, target_model=%s",
                    request_id, business_type, max_output_tokens, target_model)

        # GAP-18: PRD §10.6 — requires_context=true 的缓存仅限同 session 复用
        # 改写完成后，若 requires_context=true，用 session-scoped key 重新查缓存
        requires_context = getattr(rewrite_result, 'requires_context', False) or False
        if requires_context and body.session_id:
            session_scoped_key = _compute_cache_key(
                effective_query,
                role_mask=user.user_role_mask,
                dept_mask=user.user_dept_mask,
                session_id=body.session_id,
            )
            try:
                resp = await _call_service(
                    client, "GET",
                    f"{CACHE_SERVICE_URL}/api/cache",
                    params={
                        "key": session_scoped_key,
                        "role_mask": user.user_role_mask,
                        "dept_mask": user.user_dept_mask,
                    },
                    timeout=CACHE_LOOKUP_TIMEOUT_S,
                )
                if resp.status_code == 200:
                    cache_data = resp.json()
                    if cache_data.get("hit"):
                        cache_result = cache_data.get("value")
                        elapsed_ms = (time.monotonic() - pipeline_start) * 1000
                        logger.info("[%s] Session-scoped 缓存命中: %.1fms", request_id, elapsed_ms)
                        return GenerateResponse(
                            request_id=request_id,
                            success=True,
                            answer=cache_result.get("answer", ""),
                            rewritten_query=effective_query,
                            business_type=business_type,
                            from_cache=True,
                            latency_ms=elapsed_ms,
                        )
            except HTTPException:
                logger.debug("[%s] Session-scoped 缓存未命中", request_id)

        # ── 步骤 4: 多路召回 ─────────────────────────────────────────────
        recall_candidates: list[dict[str, Any]] = []
        try:
            resp = await _call_service(
                client, "POST",
                f"{RETRIEVAL_SERVICE_URL}/api/recall",
                json_body={
                    "query": effective_query,
                    "user_role_mask": user.user_role_mask,
                    "user_dept_mask": user.user_dept_mask,
                },
                timeout=RECALL_TIMEOUT_S,
            )
            if resp.status_code == 200:
                recall_data = resp.json()
                recall_candidates = recall_data.get("results", [])
        except HTTPException:
            logger.warning("[%s] 召回失败，将使用空候选集", request_id)

        if not recall_candidates:
            elapsed_ms = (time.monotonic() - pipeline_start) * 1000
            logger.warning("[%s] 召回无结果: %.1fms", request_id, elapsed_ms)
            # 尝试无检索生成（仅用 LLM 知识）
            return await _generate_without_context(
                client, request_id, body, user, pipeline_start,
                rewrite_result, admission_request_id,
                target_model=target_model,
                max_output_tokens=max_output_tokens,
                business_type=business_type,
            )

        # ── 步骤 5: 重排 ────────────────────────────────────────────────
        reranked_candidates: list[dict[str, Any]] = recall_candidates
        try:
            resp = await _call_service(
                client, "POST",
                f"{RETRIEVAL_SERVICE_URL}/api/rerank",
                json_body={
                    "query": effective_query,
                    "candidates": recall_candidates,
                    "top_k": 10,
                },
                timeout=RERANK_TIMEOUT_S,
            )
            if resp.status_code == 200:
                rerank_data = resp.json()
                reranked_candidates = rerank_data.get("results", recall_candidates)
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
                    "rerank_results": reranked_candidates,
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
                    "ctx": {
                        "user_input": effective_query,
                        "rewrite_result": rewrite_result.model_dump(),
                        "rerank_results": reranked_candidates,
                        "evidence_result": evidence_result.model_dump(),
                        "session_id": body.session_id,
                        "max_output_tokens": max_output_tokens,
                        "target_model": target_model,
                    },
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
        if admission_request_id:
            try:
                await _call_service(
                    client, "POST",
                    f"{GENERATION_SERVICE_URL}/api/admission-release",
                    json_body={"request_id": admission_request_id},
                    timeout=ADMISSION_RELEASE_TIMEOUT_S,
                )
            except HTTPException:
                logger.warning("[%s] 准入释放失败（不影响响应）", request_id)

        # ── 步骤 9: 写入缓存 ─────────────────────────────────────────────
        try:
            # GAP-18: PRD §10.6 — requires_context=true 使用 session-scoped key
            if requires_context and body.session_id:
                cache_key_write = _compute_cache_key(
                    effective_query,
                    role_mask=user.user_role_mask,
                    dept_mask=user.user_dept_mask,
                    session_id=body.session_id,
                )
            else:
                cache_key_write = _compute_cache_key(
                    body.query,
                    role_mask=user.user_role_mask,
                    dept_mask=user.user_dept_mask,
                )
            await _call_service(
                client, "POST",
                f"{CACHE_SERVICE_URL}/api/cache",
                json_body={
                    "key": cache_key_write,
                    "value": {
                        "answer": generation_result.answer,
                        "business_type": business_type,
                    },
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
    admission_request_id: str | None,
    target_model: str = "qwen3-4b",
    max_output_tokens: int = 512,
    business_type: str = "general",
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
                "ctx": {
                    "user_input": body.query,
                    "rewrite_result": rewrite_result.model_dump(),
                    "rerank_results": [],
                    "evidence_result": EvidenceGateResult(
                        decision="reject",
                    ).model_dump(),
                    "session_id": body.session_id,
                    "max_output_tokens": max_output_tokens,
                    "target_model": target_model,
                },
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
            business_type=business_type,
            confidence=0.0,
            evidence_decision="reject",
            latency_ms=elapsed_ms,
        )

    # 释放准入令牌
    if admission_request_id:
        try:
            await _call_service(
                client, "POST",
                f"{GENERATION_SERVICE_URL}/api/admission-release",
                json_body={"request_id": admission_request_id},
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
        business_type=business_type,
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
        raise HTTPException(status_code=500, detail="内部服务错误，请稍后重试")


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
        raise HTTPException(status_code=500, detail="内部服务错误，请稍后重试")


# ===========================================================================
# GET /api/media/{doc_id} — 媒体文件访问
# ===========================================================================
@router.get("/api/media/{doc_id}")
async def media_access(
    doc_id: str,
    user: UserIdentity = Depends(require_current_user),
):
    """
    媒体文件访问端点（PRD §10 资源访问安全）.

    1. Qdrant 文档权限二次校验（查文档 role_mask/dept_mask，与用户权限比对）
    2. RBAC 权限校验（通过 generation-service 代理）
    3. 生成 MinIO 临时签名 URL（60s TTL）
    """
    try:
        # SEC-3: 校验 doc_id 格式，防止查询注入
        from common.auth import validate_doc_id
        validate_doc_id(doc_id)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "invalid_doc_id", "detail": "doc_id 格式不合法"})

    try:
        # ① Qdrant 文档权限二次校验（PRD §6 / §10）
        try:
            from auth.bitmask_rbac import is_allowed
            from qdrant_client import QdrantClient
            from qdrant_client.http.models import Filter, FieldCondition, MatchValue
            from common.config import get_config_dict
            _cfg = get_config_dict()

            _qc = QdrantClient(
                host=_cfg["qdrant"]["host"],
                port=_cfg["qdrant"]["port"],
            )

            records, _ = _qc.scroll(
                collection_name="rag_text_768",
                scroll_filter=Filter(
                    must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
                ),
                limit=1,
                with_payload=["role_mask", "dept_mask", "status"],
            )
            if records:
                payload = records[0].payload or {}
                doc_status = payload.get("status", "active")
                if doc_status != "archived":
                    doc_role = payload.get("role_mask", 0)
                    doc_dept = payload.get("dept_mask", 0)
                    if not is_allowed(doc_role, user.user_role_mask, doc_dept, user.user_dept_mask):
                        return JSONResponse(
                            status_code=403,
                            content={"error": "无权访问该媒体文件（权限校验失败）", "doc_id": doc_id},
                        )
        except Exception as e:
            logger.debug(f"Qdrant 权限校验跳过（降级到 generation-service 校验）: {e}")

        # ② RBAC 权限校验（通过 generation-service 代理）
        async with httpx.AsyncClient() as client:
            resp = await _call_service(
                client, "GET",
                f"{GENERATION_SERVICE_URL}/api/media/{doc_id}",
                params={"user_role_mask": user.user_role_mask, "user_dept_mask": user.user_dept_mask},
                timeout=10.0,
            )
            if resp.status_code == 403:
                return JSONResponse(
                    status_code=403,
                    content={"error": "无权访问该媒体文件", "doc_id": doc_id},
                )
            resp.raise_for_status()
            media_info = resp.json()

        # ② MinIO 临时签名 URL（PRD §10: 60s TTL）
        try:
            from common.minio_client import get_minio_client
            minio = get_minio_client()
            presigned_url = minio.get_presigned_url(doc_id)
            if presigned_url:
                media_info["url"] = presigned_url
                media_info["expires_in_seconds"] = 60
        except ImportError:
            logger.debug("MinIO 客户端不可用，使用代理路径")

        return media_info

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("媒体访问失败: %s", exc)
        raise HTTPException(status_code=500, detail="内部服务错误，请稍后重试")


# ===========================================================================
# POST /api/query — 单轮 RAG 查询（前端接口）
# ===========================================================================
@router.post("/api/query", response_model=QueryResponse)
async def query_handler(
    body: QueryRequest,
    user: UserIdentity = Depends(get_current_user),
):
    """
    单轮 RAG 查询（前端兼容接口）。

    将 QueryRequest 映射为 GenerateRequest，调用内部 /v1/generate 管线，
    返回前端期望的 QueryResponse 格式。
    """
    from httpx import ASGITransport, AsyncClient
    from api_gateway.main import app as gateway_app

    transport = ASGITransport(app=gateway_app)
    async with AsyncClient(transport=transport, base_url="http://gateway") as client:
        resp = await client.post(
            "/v1/generate",
            json={
                "query": body.query,
                "session_id": body.session_id or "",
                "recent_dialogs": [],
            },
            headers={
                "Authorization": f"Bearer {user.user_id}",
                "X-User-ID": user.user_id,
                "X-Role-Mask": str(user.user_role_mask),
                "X-Dept-Mask": str(user.user_dept_mask),
            },
            timeout=120.0,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="Pipeline processing failed")
        data = resp.json()

    cache_hit = data.get("from_cache", False)
    return QueryResponse(
        answer=data.get("answer", ""),
        session_id=data.get("session_id") or body.session_id,
        business_type=data.get("business_type"),
        intent=None,
        latency_ms=data.get("latency_ms", 0.0),
        cache_hit=cache_hit,
    )


# ===========================================================================
# POST /api/chat — 多轮对话（前端接口）
# ===========================================================================
@router.post("/api/chat", response_model=ChatResponse)
async def chat_handler(
    body: ChatRequest,
    user: UserIdentity = Depends(get_current_user),
):
    """
    多轮对话（前端兼容接口）。

    将 ChatRequest 映射为 GenerateRequest，调用内部 /v1/generate 管线，
    返回前端期望的 ChatResponse 格式。
    """
    from httpx import ASGITransport, AsyncClient
    from api_gateway.main import app as gateway_app

    transport = ASGITransport(app=gateway_app)
    async with AsyncClient(transport=transport, base_url="http://gateway") as client:
        resp = await client.post(
            "/v1/generate",
            json={
                "query": body.message,
                "session_id": body.session_id,
                "recent_dialogs": [],
            },
            headers={
                "Authorization": f"Bearer {user.user_id}",
                "X-User-ID": user.user_id,
                "X-Role-Mask": str(user.user_role_mask),
                "X-Dept-Mask": str(user.user_dept_mask),
            },
            timeout=120.0,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="Pipeline processing failed")
        data = resp.json()

    cache_hit = data.get("from_cache", False)
    return ChatResponse(
        answer=data.get("answer", ""),
        session_id=body.session_id,
        history=[],
        business_type=data.get("business_type"),
        intent=None,
        latency_ms=data.get("latency_ms", 0.0),
        cache_hit=cache_hit,
    )
