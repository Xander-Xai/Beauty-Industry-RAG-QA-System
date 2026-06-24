"""
API Gateway 主入口模块.

FastAPI 应用程序，作为 RAG QA 系统的统一入口网关。
负责路由分发、鉴权、限流和请求编排。
"""

from __future__ import annotations

import logging
import os
import sys
import time

# 确保项目根目录在 sys.path 中，以便导入 common 包
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 确保 api-gateway 目录自身在 sys.path 中，以便同级模块互相导入
_GATEWAY_DIR = os.path.dirname(os.path.abspath(__file__))
if _GATEWAY_DIR not in sys.path:
    sys.path.insert(0, _GATEWAY_DIR)

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from routers import auth, generation, retrieval, rewrite, system  # noqa: F401

from common.config import get_config

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("api-gateway")

# ---------------------------------------------------------------------------
# 应用实例
# ---------------------------------------------------------------------------
app = FastAPI(
    title=os.environ.get("API_GATEWAY_TITLE", f"{get_config().system.name} API Gateway"),
    description=(
        "统一 API 网关，聚合 rewrite、retrieval、generation、cache、monitoring 等微服务。"
    ),
    version=get_config().system.version,
)

# ---------------------------------------------------------------------------
# CORS 中间件 — 生产环境通过 CORS_ORIGINS 环境变量限制来源
# ---------------------------------------------------------------------------
_cors_origins_str = os.environ.get("CORS_ORIGINS", "")
if _cors_origins_str:
    CORS_ORIGINS = [o.strip() for o in _cors_origins_str.split(",") if o.strip()]
else:
    # H-10: 生产模式下 CORS_ORIGINS 必须显式设置
    try:
        from common.config import is_production_mode
        if is_production_mode():
            raise RuntimeError("生产模式下必须通过 CORS_ORIGINS 环境变量设置允许的来源")
    except (ImportError, RuntimeError):
        pass
    CORS_ORIGINS = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=CORS_ORIGINS != ["*"],  # 通配符时不启用 credentials
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-User-ID", "X-Role-Mask", "X-Dept-Mask"],
)

# ---------------------------------------------------------------------------
# 全局异常处理器
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """捕获所有未处理异常，返回统一 JSON 错误响应（不泄露内部详情）。"""
    logger.exception("未处理异常: %s %s -> %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "网关内部错误",
            "detail": "服务暂时不可用，请稍后重试",
        },
    )

# ---------------------------------------------------------------------------
# 请求日志中间件
# ---------------------------------------------------------------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """记录每个请求的方法、路径和耗时。"""
    start = time.monotonic()
    response = await call_next(request)
    elapsed_ms = (time.monotonic() - start) * 1000
    logger.info(
        "%s %s -> %d (%.1fms)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response

# ---------------------------------------------------------------------------
# 安全响应头中间件
# ---------------------------------------------------------------------------
@app.middleware("http")
async def security_headers(request: Request, call_next):
    """为所有响应添加安全头。"""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# ---------------------------------------------------------------------------
# 挂载路由
# ---------------------------------------------------------------------------
# 所有 API 路由统一挂载在 /v1 前缀下
app.include_router(rewrite.router, prefix="/v1", tags=["rewrite"])
app.include_router(retrieval.router, prefix="/v1", tags=["retrieval"])
app.include_router(generation.router, prefix="/v1", tags=["generation"])
app.include_router(system.router, prefix="/v1", tags=["system"])
# auth 路由已有 /api/auth 前缀，无需额外 prefix
app.include_router(auth.router, tags=["auth"])

# 已挂载完毕：/v1/rewrite, /v1/recall, /v1/rerank, /v1/evidence-gate,
# /v1/generate, /api/continuation, /api/dialog_history, /api/media/{doc_id},
# /v1/health, /v1/metrics, /v1/alerts

# ---------------------------------------------------------------------------
# 启动事件
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    """应用启动时打印各下游服务地址，便于运维排查。"""
    cfg = get_config()
    logger.info("=" * 60)
    logger.info("%s API Gateway 启动中...", cfg.system.name)
    logger.info("系统名称: %s", cfg.system.name)
    logger.info("系统版本: %s", cfg.system.version)
    logger.info("知识版本: %s", cfg.knowledge_version_epoch)
    logger.info("=" * 60)

    # 通过环境变量或默认值展示下游服务地址
    service_urls = {
        "rewrite-service": os.environ.get("REWRITE_SERVICE_URL", "http://rewrite-service:8101"),
        "retrieval-service": os.environ.get("RETRIEVAL_SERVICE_URL", "http://retrieval-service:8200"),
        "generation-service": os.environ.get("GENERATION_SERVICE_URL", "http://generation-service:8100"),
        "cache-service": os.environ.get("CACHE_SERVICE_URL", "http://cache-service:8300"),
        "monitoring-service": os.environ.get("MONITORING_SERVICE_URL", "http://monitoring-service:8400"),
    }
    for name, url in service_urls.items():
        logger.info("  %s -> %s", name, url)
    logger.info("=" * 60)

# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "api-gateway.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("API_GATEWAY_PORT", 8000)),
        reload=True,
        log_level="info",
    )
