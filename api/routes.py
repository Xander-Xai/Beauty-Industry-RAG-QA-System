"""
FastAPI 路由处理器

POST /query  - 单轮 RAG 查询
POST /chat   - 多轮对话（带会话管理）
GET  /health - 健康检查（Redis/Qdrant/ES 连通性）
GET  /stats  - 系统指标
GET  /media/{doc_id} - 文档媒体预签名 URL（权限二次校验）
GET  /metrics - Prometheus 文本格式指标
"""

from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from qdrant_client import QdrantClient
from qdrant_client.http.models import FieldCondition, Filter, IsEmptyCondition, MatchValue, PayloadField

from api.models import (
    CacheHitRate,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ContinuationRequest,
    ErrorResponse,
    HealthResponse,
    LatencyPercentiles,
    QueryRequest,
    QueryResponse,
    StatsResponse,
)
from common.audit import audit_media_denied
from common.auth import is_document_authorized, require_identity, validate_doc_id
from common.config import get_config_dict
from common.models import UserIdentity
from core.pipeline import OnlineRAGPipeline
from core.pipeline_context import RequestContext, SessionState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["rag"])

# ─── 全局组件（延迟初始化，由 app.py 的 startup 事件注入）──

_pipeline: OnlineRAGPipeline | None = None
_metrics = None
_active_requests = 0
_active_requests_lock = threading.Lock()

# 配置（与原有行为保持一致）
_config = get_config_dict()


def get_pipeline() -> OnlineRAGPipeline:
    """获取全局 pipeline 单例"""
    global _pipeline
    if _pipeline is None:
        _pipeline = OnlineRAGPipeline()
    return _pipeline


def get_metrics():
    """获取全局 MetricsCollector 单例"""
    global _metrics
    if _metrics is None:
        from monitoring.otel_tracer import MetricsCollector

        _metrics = MetricsCollector()
    return _metrics


def _increment_active():
    global _active_requests
    with _active_requests_lock:
        _active_requests += 1


def _decrement_active():
    global _active_requests
    with _active_requests_lock:
        _active_requests -= 1


# ─── POST /api/query ────────────────────────────────────────


@router.post(
    "/query",
    response_model=QueryResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="单轮 RAG 查询",
)
def query_handler(
    req: QueryRequest,
    identity: UserIdentity = Depends(require_identity),
):
    """
    单轮 RAG 查询入口。

    接收用户查询，经过完整 RAG 管线（Rewrite -> Recall -> Rerank ->
    Evidence Gate -> Generate -> Answer Gate）返回结果。
    """
    # 用请求体中的 user_id 覆盖（Header 优先级更高，这里仅补充）
    user_id = req.user_id or identity.user_id

    ctx = RequestContext(
        user_input=req.query,
        image_path=req.image_path,
        session_id=req.session_id,
        user_id=user_id,
        user_role_mask=identity.user_role_mask,
        user_dept_mask=identity.user_dept_mask,
    )

    pipeline = get_pipeline()
    metrics = get_metrics()

    _increment_active()
    try:
        pipeline.process(ctx)
        metrics.record_request(ctx)
    finally:
        _decrement_active()

    cache_hit = ctx.cache_hit_level in ("L1", "L2")

    return QueryResponse(
        answer=ctx.final_response,
        session_id=ctx.session_id,
        business_type=(ctx.rewrite_result.business_type if ctx.rewrite_result else None),
        intent=ctx.rewrite_result.intent if ctx.rewrite_result else None,
        evidence_doc_ids=ctx.evidence_locked_doc_ids,
        latency_ms=round(ctx.get_total_latency_ms(), 2),
        cache_hit=cache_hit,
    )


# ─── POST /api/chat ─────────────────────────────────────────


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="多轮对话",
)
def chat_handler(
    req: ChatRequest,
    identity: UserIdentity = Depends(require_identity),
):
    """
    多轮对话入口。

    维护会话状态（最近 6 轮对话历史），支持证据锁定续写。
    """
    session_state = SessionState.get_or_create(req.session_id)

    ctx = RequestContext(
        user_input=req.message,
        session_id=req.session_id,
        user_id=identity.user_id,
        user_role_mask=identity.user_role_mask,
        user_dept_mask=identity.user_dept_mask,
    )

    pipeline = get_pipeline()
    metrics = get_metrics()

    _increment_active()
    try:
        pipeline.process(ctx)
        metrics.record_request(ctx)
    finally:
        _decrement_active()

    # 构建完整对话历史
    history: list[ChatMessage] = []
    for round_data in session_state.dialog_rounds:
        history.append(ChatMessage(role="user", content=round_data["user_input"]))
        history.append(ChatMessage(role="assistant", content=round_data["response"]))

    cache_hit = ctx.cache_hit_level in ("L1", "L2")

    return ChatResponse(
        answer=ctx.final_response,
        session_id=req.session_id,
        history=history,
        business_type=(ctx.rewrite_result.business_type if ctx.rewrite_result else None),
        intent=ctx.rewrite_result.intent if ctx.rewrite_result else None,
        evidence_doc_ids=ctx.evidence_locked_doc_ids or [],
        latency_ms=round(ctx.get_total_latency_ms(), 2),
        cache_hit=cache_hit,
    )


# ─── POST /api/continuation ──────────────────────────────────


@router.post(
    "/continuation",
    summary="长文续写（单块模式简化版）",
)
def continuation_handler(
    req: ContinuationRequest,
    identity: UserIdentity = Depends(require_identity),
):
    """
    长文续写入口（单块模式简化版）。

    完整续写逻辑需要微服务架构支持，此处返回空桩响应。
    """
    SessionState.get_or_create(req.session_id)
    return {
        "answer": "",
        "has_more": False,
        "session_id": req.session_id,
        "outline": req.outline or [],
    }


# ─── GET /api/dialog_history ─────────────────────────────────


@router.get(
    "/dialog_history",
    summary="对话历史查询（单块模式简化版）",
)
def dialog_history_handler(
    session_id: str = Query(...),
    identity: UserIdentity = Depends(require_identity),
):
    """
    对话历史查询入口（单块模式简化版）。
    """
    session = SessionState.get_or_create(session_id)
    return {
        "session_id": session_id,
        "rounds": session.dialog_rounds,
        "locked_doc_ids": session.locked_doc_ids,
    }


# ─── GET /api/health ────────────────────────────────────────


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="健康检查",
)
def health_handler():
    """
    健康检查端点。

    检查 Redis、Qdrant、Elasticsearch 连通性。
    任何依赖连接失败时整体状态为 "degraded"，但端点本身不会抛出异常。
    """
    checks: dict[str, bool] = {
        "redis": False,
        "qdrant": False,
        "elasticsearch": False,
    }

    # Redis
    try:
        from cache.redis_cache import RedisCache

        rc = RedisCache()
        checks["redis"] = rc.enabled
    except Exception as e:
        logger.debug(f"Redis health check failed: {e}")

    # Qdrant
    try:
        from common.config import get_config_dict

        _cfg = get_config_dict()
        _qc = QdrantClient(
            host=_cfg["qdrant"]["host"],
            port=_cfg["qdrant"]["port"],
        )
        checks["qdrant"] = _qc.healthcheck()
    except Exception as e:
        logger.debug(f"Qdrant health check failed: {e}")

    # Elasticsearch
    try:
        from elasticsearch import Elasticsearch

        es = Elasticsearch([_config["elasticsearch"]["host"]])
        checks["elasticsearch"] = es.ping()
    except Exception as e:
        logger.debug(f"Elasticsearch health check failed: {e}")

    overall = "healthy" if all(checks.values()) else "degraded"

    return HealthResponse(
        status=overall,
        version=_config.get("system", {}).get("version", "2.0.0"),
        dependencies=checks,
    )


# ─── GET /api/stats ─────────────────────────────────────────


@router.get(
    "/stats",
    response_model=StatsResponse,
    summary="系统统计指标",
)
def stats_handler(
    identity: UserIdentity = Depends(require_identity),
):
    """
    系统运行指标端点。

    返回 MetricsCollector 收集的缓存命中率、Rewrite 降级率、
    各阶段延迟百分位数、KV 压力值等。
    """
    metrics = get_metrics()
    raw = metrics.get_stats()

    # 解析 CacheHitRate
    cache_hit = CacheHitRate(
        L1=raw.get("cache_hit_rate", {}).get("L1", 0.0),
        L2=raw.get("cache_hit_rate", {}).get("L2", 0.0),
    )

    # 解析 LatencyPercentiles
    latency_pct: dict[str, LatencyPercentiles] = {}
    for stage, pct_data in raw.get("latency_percentiles", {}).items():
        latency_pct[stage] = LatencyPercentiles(
            p50=pct_data.get("p50", 0.0),
            p95=pct_data.get("p95", 0.0),
            p99=pct_data.get("p99", 0.0),
            count=pct_data.get("count", 0),
        )

    kv_pressure = raw.get("gauges", {}).get("kv_pressure", 0.0)

    return StatsResponse(
        uptime_seconds=raw.get("uptime_seconds", 0.0),
        cache_hit_rate=cache_hit,
        rewrite_fallback_rate=raw.get("rewrite_fallback_rate", 0.0),
        latency_percentiles=latency_pct,
        kv_pressure=kv_pressure,
        active_requests=_active_requests,
    )


# ─── GET /api/media/{doc_id} ──────────────────────────────


@router.get(
    "/media/{doc_id}",
    summary="文档媒体预签名 URL（权限二次校验）",
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def media_handler(
    doc_id: str,
    identity: UserIdentity = Depends(require_identity),
):
    """
    PRD §10: 资源访问安全

    流程：
    1. 通过 Qdrant 查询当前 active epoch 中 doc_id 的元数据
    2. 使用 common/auth.is_allowed 进行权限二次校验
    3. 通过 common/minio_client 生成 60s 有效 presigned URL
    4. 返回 {doc_id, url, expires_in_seconds}
    """
    try:
        doc_id = validate_doc_id(doc_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_doc_id", "detail": str(exc)},
        ) from exc

    # ── 1. 查询 Qdrant 获取文档元数据 ──
    metadata: dict | None = None
    doc_status = "active"
    found = False

    try:
        qdrant_cfg = _config.get("qdrant", {})

        client = QdrantClient(
            host=qdrant_cfg.get("host", "localhost"),
            port=qdrant_cfg.get("port", 6333),
        )
        collection_name = _config.get("embedding", {}).get("text", {}).get("collection", "rag_text_768")
        active_epoch = _config.get("knowledge_version_epoch", "default")

        # Scope metadata authorization to the current version before checking masks.
        epoch_conditions = [FieldCondition(key="doc_version_epoch", match=MatchValue(value=active_epoch))]
        if active_epoch == "default":
            # Legacy text points without this field belong to the default epoch.
            epoch_conditions.append(IsEmptyCondition(is_empty=PayloadField(key="doc_version_epoch")))
        records, _ = client.scroll(
            collection_name=collection_name,
            scroll_filter=Filter(
                must=[
                    FieldCondition(key="doc_id", match=MatchValue(value=doc_id)),
                    FieldCondition(key="status", match=MatchValue(value="active")),
                ],
                should=epoch_conditions,
            ),
            limit=1,
            with_payload=["role_mask", "dept_mask", "status", "doc_version_epoch"],
        )

        if records:
            found = True
            metadata = records[0].payload or {}
            doc_status = metadata.get("status", "active")

    except Exception as e:
        logger.warning(f"Qdrant 查询 doc_id={doc_id} 失败: {e}")

    # ── 2. 检查文档是否存在 / 是否已归档 ──
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "not_found",
                "detail": f"文档 {doc_id} 不存在",
            },
        )

    if doc_status == "archived":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "archived",
                "detail": f"文档 {doc_id} 已归档",
            },
        )

    # ── 3. 权限二次校验（统一 fail-closed helper）──
    # is_document_authorized denies when role_mask/dept_mask are missing,
    # malformed, negative or outside uint32, instead of treating them as public.
    if not is_document_authorized(
        metadata,
        identity.user_role_mask,
        identity.user_dept_mask,
    ):
        # A refused read of protected media is a security-relevant event, not
        # merely a 4xx: it is the signal for investigating a probe or a
        # mis-scoped role.
        audit_media_denied(
            identity.user_id,
            doc_id,
            reason="role/dept mask mismatch or missing document metadata",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "permission_denied",
                "detail": f"无权访问文档 {doc_id}",
            },
        )

    # ── 4. 生成 MinIO presigned URL ──
    from common.minio_client import MINIO_URL_TTL, get_minio_client

    minio = get_minio_client()

    if not minio.is_available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "storage_unavailable",
                "detail": "文件存储服务暂不可用",
            },
        )

    presigned_url = minio.get_presigned_url(doc_id)

    if not presigned_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "url_generation_failed",
                "detail": f"无法为文档 {doc_id} 生成访问链接",
            },
        )

    return {
        "doc_id": doc_id,
        "url": presigned_url,
        "expires_in_seconds": MINIO_URL_TTL,
    }


# ─── GET /api/metrics ──────────────────────────────────────


@router.get(
    "/metrics",
    summary="Prometheus 文本格式指标",
    response_class=PlainTextResponse,
)
def metrics_handler(
    identity: UserIdentity = Depends(require_identity),
):
    """
    PRD §12: 暴露 Prometheus 抓取端点。

    需要身份认证。Prometheus 可通过 bearer_token 配置抓取。
    """
    metrics = get_metrics()
    return PlainTextResponse(content=metrics.to_prometheus_text())
