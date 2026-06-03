"""
Flask Web 应用入口（重构版）

API 拆分（readme 13 节）：
- POST /api/rewrite - Query Rewrite（P99≤45ms）
- POST /api/generate - 完整 RAG 流程生成答案（P99≤3.0s）
- GET  /api/media/{doc_id} - 媒体资源访问（MinIO 临时签名 URL）
- POST /api/send_message - 兼容旧前端
- GET  /api/dialog_history - 对话历史
- GET  /api/health - 健康检查
- GET  /api/metrics - 系统指标
- GET  /api/alerts - 告警状态
"""

from __future__ import annotations

import json
import logging
import time
from flask import Flask, request, jsonify, render_template

from core.pipeline import OnlineRAGPipeline
from core.pipeline_context import RequestContext
from auth.user_identity import UserIdentity
from monitoring.otel_tracer import MetricsCollector, AlertingManager

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# 加载配置（处理转义引号）
with open("config.json", encoding="utf-8", errors="replace") as f:
    raw = f.read()
# config.json 中存在 \" 转义引号，需要先还原
raw = raw.replace('\\"', '"')
config = json.loads(raw)

# 初始化 Flask
app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)

# 初始化全局组件
pipeline = OnlineRAGPipeline()
user_identity = UserIdentity()
metrics = MetricsCollector()
alerting = AlertingManager(metrics)


# ─── 前端路由 ──────────────────────────────────────────

@app.route("/")
def index():
    """主页 - 聊天界面"""
    return render_template("index.html")


# ─── API 路由 ──────────────────────────────────────────

@app.route("/api/rewrite", methods=["POST"])
def api_rewrite():
    """Query Rewrite API（readme 13 节）"""
    data = request.get_json()
    query = data.get("query", "")
    if not query:
        return jsonify({"error": "query is required"}), 400

    try:
        rewrite_result = pipeline.query_rewriter.rewrite(
            query,
            recent_dialogs=data.get("recent_dialogs", []),
        )
        return jsonify({
            "rewritten_query": rewrite_result.rewritten_query,
            "business_type": rewrite_result.business_type,
            "intent": rewrite_result.intent,
            "requires_context": rewrite_result.requires_context,
            "standardized_entities": rewrite_result.standardized_entities,
            "confidence": rewrite_result.confidence,
            "fallback": rewrite_result.fallback,
        })
    except Exception as e:
        logger.error(f"Rewrite API 异常: {e}")
        return jsonify({
            "rewritten_query": query,
            "business_type": "general",
            "intent": "general",
            "requires_context": True,
            "confidence": 0.3,
            "fallback": True,
        })


@app.route("/api/generate", methods=["POST"])
def api_generate():
    """完整 RAG 生成 API（readme 13 节）"""
    data = request.get_json()
    user_input = data.get("query", data.get("user_input", ""))
    if not user_input:
        return jsonify({"error": "query is required"}), 400

    identity = user_identity.parse_from_request(request)
    session_id = data.get("session_id", "default")

    ctx = RequestContext(
        user_input=user_input,
        image_path=data.get("image_path"),
        session_id=session_id,
        user_id=identity["user_id"],
        user_role_mask=identity["user_role_mask"],
        user_dept_mask=identity["user_dept_mask"],
    )

    try:
        response = pipeline.process(ctx)
        metrics.record_request(ctx)

        return jsonify({
            "response": response,
            "request_id": ctx.request_id,
            "business_type": ctx.rewrite_result.business_type if ctx.rewrite_result else "general",
            "model_used": ctx.generation_result.model_used if ctx.generation_result else "unknown",
            "latency_ms": ctx.get_total_latency_ms(),
            "cached": ctx.cache_hit_level is not None and ctx.cache_hit_level != "MISS",
            "degraded": ctx.degraded,
            "evidence_score": ctx.evidence_result.evidence_score if ctx.evidence_result else 0,
            "doc_ids": ctx.evidence_locked_doc_ids,
        })
    except Exception as e:
        logger.error(f"Generate API 异常: {e}", exc_info=True)
        return jsonify({
            "response": "系统处理出现异常，请稍后重试。",
            "error": str(e),
        }), 500


@app.route("/api/send_message", methods=["POST"])
def api_send_message():
    """兼容旧前端的消息发送 API"""
    data = request.get_json()
    user_input = data.get("user_input", "")
    if not user_input:
        return jsonify({"error": "user_input is required"}), 400

    identity = user_identity.parse_from_request(request)

    ctx = RequestContext(
        user_input=user_input,
        user_id=identity["user_id"],
        user_role_mask=identity["user_role_mask"],
        user_dept_mask=identity["user_dept_mask"],
    )

    try:
        response = pipeline.process(ctx)
        metrics.record_request(ctx)
        return jsonify({"response": response})
    except Exception as e:
        logger.error(f"Send message 异常: {e}", exc_info=True)
        return jsonify({"response": "系统处理出现异常，请稍后重试。"}), 500


@app.route("/api/dialog_history", methods=["GET"])
def api_dialog_history():
    """获取对话历史"""
    session_id = request.args.get("session_id", "default")
    from core.pipeline_context import SessionState
    session = SessionState.get_or_create(session_id)
    return jsonify(session.dialog_rounds)


@app.route("/api/media/<doc_id>", methods=["GET"])
def api_media(doc_id: str):
    """
    媒体资源访问 API（readme 6 节）

    流程：
    1. 从 Milvus 查询文档的权限信息
    2. 执行权限重校验
    3. 生成 MinIO 预签名 URL（60s 有效期）
    """
    identity = user_identity.parse_from_request(request)

    # ① 权限重校验
    from auth.bitmask_rbac import is_allowed

    doc_role_mask = 0
    doc_dept_mask = 0
    doc_uri = ""

    # 从 Milvus 查询文档权限信息
    try:
        from models.embedding_service import EmbeddingService
        es = EmbeddingService()
        hits = es.search_milvus_text(
            query_embedding=None,
            collection_name="rag_image_512",
            top_k=1,
            filter_expr=f'doc_id == "{doc_id}"',
        )
        if hits:
            doc_role_mask = hits[0].get("metadata", {}).get("role_mask", 0)
            doc_dept_mask = hits[0].get("metadata", {}).get("dept_mask", 0)
            doc_uri = hits[0].get("image_uri", "")
    except Exception as e:
        logger.warning(f"Milvus 文档查询失败: {e}")

    allowed = is_allowed(
        doc_role_mask, identity["user_role_mask"],
        doc_dept_mask, identity["user_dept_mask"],
    )

    if not allowed:
        return jsonify({"error": "权限不足，无法访问该资源"}), 403

    # ② 生成 MinIO 预签名 URL
    presigned_url = doc_uri  # 降级：返回原始 URI
    try:
        import os
        if os.environ.get("MINIO_ENDPOINT"):
            from minio import Minio
            mc = Minio(
                os.environ["MINIO_ENDPOINT"],
                access_key=os.environ.get("MINIO_ACCESS_KEY", ""),
                secret_key=os.environ.get("MINIO_SECRET_KEY", ""),
                secure=False,
            )
            bucket = os.environ.get("MINIO_BUCKET", "media")
            presigned_url = mc.presigned_get_object(bucket, doc_id, expires=60)
    except ImportError:
        logger.debug("MinIO 未安装，使用原始 URI")
    except Exception as e:
        logger.warning(f"MinIO 预签名 URL 生成失败: {e}")

    return jsonify({
        "doc_id": doc_id,
        "url": presigned_url,
        "expires_in": 60,
    })


@app.route("/api/health", methods=["GET"])
def api_health():
    """健康检查"""
    # 检查各依赖服务
    checks = {
        "redis": False,
        "milvus": False,
        "elasticsearch": False,
        "vllm_rewrite": False,
    }

    try:
        from cache.redis_cache import RedisCache
        rc = RedisCache()
        checks["redis"] = rc.enabled
    except Exception:
        pass

    try:
        from router.stateless_router import StatelessRouter
        router = StatelessRouter()
        checks["vllm_rewrite"] = router.check_any_endpoint_alive()
    except Exception:
        pass

    try:
        from models.embedding_service import EmbeddingService
        es = EmbeddingService()
        checks["milvus"] = True
    except Exception:
        pass

    try:
        from elasticsearch import Elasticsearch
        es = Elasticsearch([config["elasticsearch"]["host"]])
        es.ping()
        checks["elasticsearch"] = True
    except Exception:
        pass

    overall = "healthy" if all(checks.values()) else "degraded"
    return jsonify({
        "status": overall,
        "version": config.get("system", {}).get("version", "2.0.0"),
        "dual_gpu": config.get("system", {}).get("dual_gpu", False),
        "services": checks,
    })


@app.route("/api/metrics", methods=["GET"])
def api_metrics():
    """系统指标"""
    return jsonify(metrics.get_stats())


@app.route("/api/alerts", methods=["GET"])
def api_alerts():
    """告警状态"""
    alerting.check_alerts()
    return jsonify({
        "active_alerts": alerting.get_active_alerts(),
        "alert_count": len(alerting.get_active_alerts()),
    })


# ─── 启动 ──────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("启动化妆品企业级多模态 RAG 智能问答系统...")
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True,
    )
