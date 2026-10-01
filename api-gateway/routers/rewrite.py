"""
Query Rewrite 路由模块.

将查询改写请求转发至 rewrite-service (端口 8101)，
处理超时和降级情况，返回空查询结果。
"""

from __future__ import annotations

import logging
import os
import sys
import time

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from common.models import QueryRewriteResult

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# 下游服务地址（可通过环境变量覆盖）
# ---------------------------------------------------------------------------
REWRITE_SERVICE_URL = os.environ.get("REWRITE_SERVICE_URL", "http://rewrite-service:8101")

# 转发超时（毫秒）—— rewrite-service 需要足够时间完成 LLM 推理（P99≈45ms 为 spec 目标，实际预留 2000ms）
REWRITE_TIMEOUT_MS = int(os.environ.get("REWRITE_TIMEOUT_MS", "2000"))


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------
class RewriteRequest(BaseModel):
    """查询改写请求体。"""

    query: str = Field(..., description="用户原始查询")
    recent_dialogs: list[str] = Field(default_factory=list, description="近期对话历史")


class RewriteResponse(BaseModel):
    """查询改写响应体，透传下游 QueryRewriteResult。"""

    rewritten_query: str = ""
    business_type: str = "general"
    intent: str = ""
    requires_context: bool = False
    standardized_entities: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    fallback: bool = False


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@router.post("/rewrite", response_model=RewriteResponse)
async def rewrite_query(body: RewriteRequest):
    """
    转发查询改写请求至 rewrite-service.

    超时降级策略：
    - 45ms 内未响应时，返回原始查询（fallback=True）
    - 下游服务不可用时，同样返回原始查询
    """
    start_time = time.monotonic()
    timeout_s = REWRITE_TIMEOUT_MS / 1000.0

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{REWRITE_SERVICE_URL}/api/rewrite",
                json={
                    "query": body.query,
                    "recent_dialogs": body.recent_dialogs,
                },
                timeout=timeout_s,
            )
            resp.raise_for_status()

            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.info(
                "查询改写完成: %.1fms (查询: %s)",
                elapsed_ms,
                body.query[:50],
            )

            # 将下游响应转为标准模型
            data = resp.json()
            result = QueryRewriteResult(**data)
            return RewriteResponse(
                rewritten_query=result.rewritten_query,
                business_type=result.business_type,
                intent=result.intent,
                requires_context=result.requires_context,
                standardized_entities=result.standardized_entities,
                confidence=result.confidence,
                fallback=result.fallback,
            )

    except httpx.TimeoutException:
        # 超时降级：返回原始查询，标记为 fallback
        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.warning(
            "查询改写超时 (%.1fms > %dms)，降级为原始查询: %s",
            elapsed_ms,
            REWRITE_TIMEOUT_MS,
            body.query[:50],
        )
        return RewriteResponse(
            rewritten_query=body.query,
            business_type="general",
            intent="",
            requires_context=False,
            standardized_entities=[],
            confidence=0.0,
            fallback=True,
        )

    except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
        # 下游服务不可用降级
        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.warning(
            "查询改写服务不可用 (%.1fms): %s，降级为原始查询",
            elapsed_ms,
            exc,
        )
        return RewriteResponse(
            rewritten_query=body.query,
            business_type="general",
            intent="",
            requires_context=False,
            standardized_entities=[],
            confidence=0.0,
            fallback=True,
        )
