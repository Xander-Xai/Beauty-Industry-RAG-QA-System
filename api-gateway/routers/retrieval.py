"""
Retrieval 路由模块.

将召回、重排、证据门控请求转发至 retrieval-service (端口 8200)。
提供三个端点：
- POST /v1/recall       -> /api/recall
- POST /v1/rerank       -> /api/rerank
- POST /v1/evidence-gate -> /api/evidence-gate
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from common.models import RecallResult, RerankResult, EvidenceGateResult

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# 下游服务地址
# ---------------------------------------------------------------------------
RETRIEVAL_SERVICE_URL = os.environ.get("RETRIEVAL_SERVICE_URL", "http://retrieval-service:8200")

# 转发超时（秒）—— 检索类操作允许较长超时
RECALL_TIMEOUT_S = float(os.environ.get("RECALL_TIMEOUT_S", "10"))
RERANK_TIMEOUT_S = float(os.environ.get("RERANK_TIMEOUT_S", "15"))
EVIDENCE_GATE_TIMEOUT_S = float(os.environ.get("EVIDENCE_GATE_TIMEOUT_S", "5"))


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class RecallRequest(BaseModel):
    """召回请求体。"""
    query: str = Field(..., description="检索查询文本")
    rewritten_query: str = Field(default="", description="改写后的查询文本")
    top_k: int = Field(default=50, description="每个召回路径的最大返回数")
    user_role_mask: int = Field(default=0, description="用户角色位掩码")
    user_dept_mask: int = Field(default=0, description="用户部门位掩码")
    business_type: str = Field(default="general", description="业务类型")
    context: Dict[str, Any] = Field(default_factory=dict, description="附加上下文")


class RerankRequest(BaseModel):
    """重排请求体。"""
    query: str = Field(..., description="检索查询文本")
    candidates: List[Dict[str, Any]] = Field(default_factory=list, description="待重排的候选文档列表")
    top_k: int = Field(default=10, description="最终保留的文档数")
    context: Dict[str, Any] = Field(default_factory=dict, description="附加上下文")


class EvidenceGateRequest(BaseModel):
    """证据门控请求体。"""
    query: str = Field(..., description="检索查询文本")
    candidates: List[Dict[str, Any]] = Field(default_factory=list, description="重排后的候选文档")
    business_type: str = Field(default="general", description="业务类型")
    context: Dict[str, Any] = Field(default_factory=dict, description="附加上下文")


# ---------------------------------------------------------------------------
# 路由：召回
# ---------------------------------------------------------------------------
@router.post("/recall")
async def recall(body: RecallRequest):
    """
    转发召回请求至 retrieval-service.

    执行多路召回（稠密检索、BM25、CLIP 等），返回候选文档列表。
    """
    start_time = time.monotonic()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{RETRIEVAL_SERVICE_URL}/api/recall",
                json=body.dict(),
                timeout=RECALL_TIMEOUT_S,
            )
            resp.raise_for_status()
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.info("召回完成: %.1fms, 查询: %s", elapsed_ms, body.query[:50])
            return resp.json()

    except httpx.TimeoutException:
        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.error("召回超时 (%.1fms): %s", elapsed_ms, body.query[:50])
        raise HTTPException(
            status_code=504,
            detail=f"召回服务超时 ({elapsed_ms:.0f}ms)",
        )

    except httpx.ConnectError:
        logger.error("召回服务不可用: %s", RETRIEVAL_SERVICE_URL)
        raise HTTPException(
            status_code=503,
            detail="召回服务不可用",
        )

    except httpx.HTTPStatusError as exc:
        logger.error("召回服务返回错误: %d %s", exc.response.status_code, exc.response.text)
        raise HTTPException(
            status_code=exc.response.status_code,
            detail="召回服务暂时不可用，请稍后重试",
        )


# ---------------------------------------------------------------------------
# 路由：重排
# ---------------------------------------------------------------------------
@router.post("/rerank")
async def rerank(body: RerankRequest):
    """
    转发重排请求至 retrieval-service.

    使用 Cross-Encoder + Bi-Encoder 集成模型对候选文档进行精排。
    """
    start_time = time.monotonic()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{RETRIEVAL_SERVICE_URL}/api/rerank",
                json=body.dict(),
                timeout=RERANK_TIMEOUT_S,
            )
            resp.raise_for_status()
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.info("重排完成: %.1fms, 候选数: %d", elapsed_ms, len(body.candidates))
            return resp.json()

    except httpx.TimeoutException:
        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.error("重排超时 (%.1fms), 候选数: %d", elapsed_ms, len(body.candidates))
        raise HTTPException(
            status_code=504,
            detail=f"重排服务超时 ({elapsed_ms:.0f}ms)",
        )

    except httpx.ConnectError:
        logger.error("重排服务不可用: %s", RETRIEVAL_SERVICE_URL)
        raise HTTPException(
            status_code=503,
            detail="重排服务不可用",
        )

    except httpx.HTTPStatusError as exc:
        logger.error("重排服务返回错误: %d %s", exc.response.status_code, exc.response.text)
        raise HTTPException(
            status_code=exc.response.status_code,
            detail="重排服务暂时不可用，请稍后重试",
        )


# ---------------------------------------------------------------------------
# 路由：证据门控
# ---------------------------------------------------------------------------
@router.post("/evidence-gate")
async def evidence_gate(body: EvidenceGateRequest):
    """
    转发证据门控请求至 retrieval-service.

    基于多维评分指标判断检索结果质量，决定是否进入生成阶段。
    决策结果：high_confidence / low_confidence / reject
    """
    start_time = time.monotonic()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{RETRIEVAL_SERVICE_URL}/api/evidence-gate",
                json=body.dict(),
                timeout=EVIDENCE_GATE_TIMEOUT_S,
            )
            resp.raise_for_status()
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.info("证据门控完成: %.1fms", elapsed_ms)
            return resp.json()

    except httpx.TimeoutException:
        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.error("证据门控超时 (%.1fms)", elapsed_ms)
        raise HTTPException(
            status_code=504,
            detail=f"证据门控服务超时 ({elapsed_ms:.0f}ms)",
        )

    except httpx.ConnectError:
        logger.error("证据门控服务不可用: %s", RETRIEVAL_SERVICE_URL)
        raise HTTPException(
            status_code=503,
            detail="证据门控服务不可用",
        )

    except httpx.HTTPStatusError as exc:
        logger.error("证据门控服务返回错误: %d %s", exc.response.status_code, exc.response.text)
        raise HTTPException(
            status_code=exc.response.status_code,
            detail="证据门控服务暂时不可用，请稍后重试",
        )
