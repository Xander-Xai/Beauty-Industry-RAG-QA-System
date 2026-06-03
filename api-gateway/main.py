"""
API Gateway 主入口模块.

FastAPI 应用程序，作为汽车知识智能问答系统的统一入口网关。
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

from common.config import get_config
from routers import rewrite, retrieval, generation, system  # noqa: F401

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
    title="汽车知识智能问答系统 API Gateway",
    description=(
        "统一 API 网关，聚合 rewrite、retrieval、generation、cache、monitoring 等微服务，"
        "对外提供汽车知识问答能力。"
    ),
    version="2.0.0",
)

# ---------------------------------------------------------------------------
# CORS 中间件 — 允许前端跨域访问
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],           # 生产环境应限制为前端域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# 全局异常处理器
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """捕获所有未处理异常，返回统一 JSON 错误响应。"""
    logger.exception("未处理异常: %s %s -> %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "网关内部错误",
            "detail": str(exc),
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
# 挂载路由
# ---------------------------------------------------------------------------
# 所有 API 路由统一挂载在 /v1 前缀下
app.include_router(rewrite.router, prefix="/v1", tags=["rewrite"])
app.include_router(retrieval.router, prefix="/v1", tags=["retrieval"])
app.include_router(generation.router, prefix="/v1", tags=["generation"])
app.include_router(system.router, prefix="/v1", tags=["system"])

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
    logger.info("汽车知识智能问答系统 API Gateway 启动中...")
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
        port=8000,
        reload=True,
        log_level="info",
    )
