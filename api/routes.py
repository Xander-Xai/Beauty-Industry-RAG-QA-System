"""
FastAPI 路由处理器

POST /query  - 单轮 RAG 查询
POST /chat   - 多轮对话（带会话管理）
GET  /health - 健康检查（诊断语义，依赖降级仍返回 200）
GET  /ready  - 就绪检查（流量准入语义，不满足服务能力返回 503）
GET  /stats - 系统指标
GET  /media/{doc_id} - 文档媒体预签名 URL（权限二次校验）
GET  /metrics - Prometheus 文本格式指标
"""

from __future__ import annotations

import logging
import os
import threading

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse
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
    ReadinessResponse,
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


def _run_audit(ctx) -> dict | None:
    """Build the per-request run audit, when the operator asked for it.

    Off by default so the response shape is unchanged for existing clients. The
    report is what makes a silent degradation visible: with the CrossEncoder
    weights absent the run refuses at the Evidence Gate, and nothing in the
    answer or the status code says the reranker was dead.

    Never allowed to fail a request — an audit that breaks the endpoint would be
    worse than no audit.
    """
    if os.environ.get("RAG_AUDIT_REPORT", "").strip().lower() not in ("1", "true", "yes"):
        return None
    try:
        from core.run_report import build_run_report

        return build_run_report(ctx).as_dict()
    except Exception as exc:  # noqa: BLE001 - audit must never break the response
        logger.warning("run audit unavailable: %s: %s", type(exc).__name__, exc)
        return {"error": f"{type(exc).__name__}: {exc}"}


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

    cache_hit = ctx.cache_hit_level in ("L1", "L2", "L2_SESSION")

    # A pipeline-level HTTP contract (e.g. P2 overload → 503) is returned here
    # rather than through `QueryResponse`, whose `answer: str` cannot carry a
    # status code. Without this the 503 branch fails response validation and the
    # client receives a 500.
    if ctx.http_status_override is not None:
        status_code, content = ctx.http_status_override
        return JSONResponse(status_code=status_code, content=content)

    return QueryResponse(
        answer=ctx.final_response,
        session_id=ctx.session_id,
        business_type=(ctx.rewrite_result.business_type if ctx.rewrite_result else None),
        intent=ctx.rewrite_result.intent if ctx.rewrite_result else None,
        evidence_doc_ids=ctx.evidence_locked_doc_ids,
        latency_ms=round(ctx.get_total_latency_ms(), 2),
        cache_hit=cache_hit,
        audit=_run_audit(ctx),
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
    session_state = SessionState.get_or_create(req.session_id, owner_id=identity.user_id)

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

    cache_hit = ctx.cache_hit_level in ("L1", "L2", "L2_SESSION")

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
    SessionState.get_or_create(req.session_id, owner_id=identity.user_id)
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
    session = SessionState.get_or_create(session_id, owner_id=identity.user_id)
    return {
        "session_id": session_id,
        "rounds": session.dialog_rounds,
        "locked_doc_ids": session.locked_doc_ids,
    }


# ─── GET /api/health ────────────────────────────────────────


def _elasticsearch_client_kwargs(es_cfg: dict) -> dict:
    """构造 Elasticsearch 客户端 kwargs，凭据契约与 BM25Retriever 保持一致。

    canonical Compose 启用了 ``xpack.security.enabled=true``，因此健康检查必须
    和真实运行时客户端用同一套凭据，否则一个完全可用的认证 ES 会被误报为 degraded。

    契约（与 ``retrieval/bm25_retriever.py`` 的 ``es_client`` 一致）：
    - 环境变量优先，``config.json`` 回退；
    - username **与** password 均非空时才设置 ``basic_auth``（缺一即匿名）；
    - 凭据缺失时保持匿名调用，兼容未开启 security 的部署。

    Args:
        es_cfg: ``config.json`` 中的 ``elasticsearch`` 段。

    Returns:
        传给 ``Elasticsearch(...)`` 的 kwargs。
    """
    kwargs: dict = {"hosts": [es_cfg.get("host", "http://localhost:9200")]}
    username = os.environ.get("ELASTICSEARCH_USERNAME") or es_cfg.get("username", "")
    password = os.environ.get("ELASTICSEARCH_PASSWORD") or es_cfg.get("password", "")
    if username and password:
        kwargs["basic_auth"] = (username, password)
    return kwargs


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

        es = Elasticsearch(**_elasticsearch_client_kwargs(_config.get("elasticsearch", {})))
        checks["elasticsearch"] = bool(es.ping())
    except Exception as e:
        logger.debug(f"Elasticsearch health check failed: {e}")

    overall = "healthy" if all(checks.values()) else "degraded"

    return HealthResponse(
        status=overall,
        version=_config.get("system", {}).get("version", "2.0.0"),
        dependencies=checks,
    )


# ─── GET /api/ready ─────────────────────────────────────────


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="就绪检查（流量准入）",
)
def ready_handler(response: Response):
    """
    就绪检查端点 —— Kubernetes readinessProbe / 流量准入使用。

    与 ``GET /api/health`` 的语义区别（两者不可互相替代）：

    - ``/api/health`` 是**诊断**端点。任何依赖连接失败时它仍然返回 HTTP 200，
      用 ``status: degraded`` 表达「我看到了什么」。它的契约在本 PR 中不变。
    - ``/api/ready`` 是**准入**端点。它回答的是「现在把请求路由到这个 Pod，
      它能不能服务」。不具备最低服务能力时返回 HTTP 503，让 Kubernetes 把它
      从 Endpoints 摘除。

    判定不是 ``all(dependencies)``。Redis 与 MinIO 存在真实降级路径
    （L1 进程内缓存 / 内存限流；仅影响 media 路由），因此单独故障只记入
    ``degraded``，不阻止流量。检索满足 **OR** 语义：Qdrant 与 Elasticsearch
    任一可用即可服务。生成端点则按**当前部署自己的路由配置**判定 —— 生产模式
    下 complex tier 指向 gen_14b 且代码没有运行期回退，因此 gen_14b 不可用必须
    not_ready。

    该端点不要求用户 JWT：Kubernetes 探针无法携带凭据，而把探针指向需要认证的
    端点会让探针永远 401。响应只包含依赖名与布尔值，不含地址、DSN、token 或
    任何内部凭据。

    依赖探测本身失败不会抛成 500：探测异常会被归一化为该依赖不可用。
    """
    from api.readiness import evaluate_readiness

    try:
        report = evaluate_readiness()
    except Exception as e:
        # 评估器整体不可用时必须 fail closed（not_ready），而不是因为探针本身
        # 出错就返回一个看起来健康的 200。异常文本不进入响应体。
        logger.warning(f"Readiness evaluation failed: {e}")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="not_ready",
            dependencies={},
            degraded=[],
            blockers=["readiness_evaluator"],
        )

    if not report.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(**report.to_payload())


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
