"""
FastAPI 应用入口（完全重写，替换 Flask）

功能：
- create_app() 工厂函数
- 全局异常处理器
- 启动/关闭生命周期事件
- uvicorn.run 入口

API 端点（由 api.routes 提供）：
- POST /api/query   - 单轮 RAG 查询
- POST /api/chat    - 多轮对话
- GET  /api/health  - 健康检查
- GET  /api/stats   - 系统统计指标
"""

from __future__ import annotations

import json
import logging
import os
import time
import sys

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.routes import router, get_pipeline, get_metrics
from api.middleware import setup_middleware
# H-7: 显式导入 SessionState，避免延迟导入导致清理任务 NameError
from core.pipeline_context import SessionState
from common.config import get_config as _get_sys_config

# ─── 日志配置 ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─── 加载配置 ──────────────────────────────────────────────

with open("config.json", encoding="utf-8", errors="replace") as f:
    config = json.load(f)

_APP_VERSION = config.get("system", {}).get("version", "2.0.0")


def create_app() -> FastAPI:
    """
    FastAPI 应用工厂。

    Returns:
        配置完成的 FastAPI 实例。
    """
    app = FastAPI(
        title="化妆品企业级多模态 RAG 智能问答系统",
        description="基于双 GPU、多模态检索增强生成（RAG）的企业级知识问答 API",
        version=_APP_VERSION,
        # H-9: 生产模式下禁用 Swagger/ReDoc，避免暴露 API schema
        docs_url="/docs" if _get_sys_config().deployment_mode != "production" else None,
        redoc_url="/redoc" if _get_sys_config().deployment_mode != "production" else None,
    )

    # ── 中间件（CORS + 请求日志）──────────────────────────
    setup_middleware(app)

    # ── 静态文件 & 首页 ───────────────────────────────────
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def root():
        from fastapi.responses import FileResponse
        index_path = os.path.join(static_dir, "index.html")
        if os.path.isfile(index_path):
            return FileResponse(index_path)
        return {"message": "化妆品 RAG 系统 API", "docs": "/docs"}

    # ── 路由注册 ─────────────────────────────────────────
    app.include_router(router)

    from api.routes_auth import router as auth_router
    app.include_router(auth_router)

    # ── 全局异常处理器 ───────────────────────────────────

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error("未捕获异常: %s %s -> %s", request.method, request.url.path, exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "detail": "服务暂时不可用，请稍后重试",
                "code": 500,
            },
        )

    # ── 生命周期事件 ─────────────────────────────────────

    @app.on_event("startup")
    async def on_startup():
        logger.info("系统启动中... 版本=%s", _APP_VERSION)

        # 预初始化 pipeline 和 metrics（触发懒加载以尽早发现配置问题）
        try:
            pipeline = get_pipeline()
            logger.info("OnlineRAGPipeline 初始化完成")
        except Exception as e:
            logger.error("OnlineRAGPipeline 初始化失败: %s", e)

        try:
            metrics = get_metrics()
            logger.info("MetricsCollector 初始化完成")
        except Exception as e:
            logger.error("MetricsCollector 初始化失败: %s", e)

        # 启动 SessionState 定期清理（每 5 分钟清理过期会话，防止内存泄漏）
        import asyncio

        async def _cleanup_sessions():
            while True:
                await asyncio.sleep(300)  # 每 5 分钟
                try:
                    SessionState.cleanup_expired()
                except Exception as e:
                    logger.debug("会话定期清理跳过: %s", e)

        asyncio.create_task(_cleanup_sessions())
        logger.info("SessionState 定期清理任务已启动（间隔 5 分钟）")

        logger.info("系统启动完成，监听端口: %s", "8000")

    @app.on_event("startup")
    async def generate_jwt_keys():
        """Generate JWT key pair if not exists and JWT is configured."""
        import os
        from auth.jwt_auth import generate_keypair, get_jwt_config
        config = get_jwt_config()
        if config.enabled and config.private_key_path:
            if not os.path.isfile(config.private_key_path):
                key_dir = os.path.dirname(config.private_key_path)
                if key_dir:
                    os.makedirs(key_dir, exist_ok=True)
                    generate_keypair(key_dir)

    @app.on_event("shutdown")
    async def on_shutdown():
        logger.info("系统关闭中...")

        # 清理过期会话
        try:
            from core.pipeline_context import SessionState
            SessionState.cleanup_expired()
        except Exception as e:
            logger.warning("会话清理失败: %s", e)

        logger.info("系统已关闭")

    return app


# ─── 入口 ──────────────────────────────────────────────────

app = create_app()

if __name__ == "__main__":
    import uvicorn

    logger.info("启动化妆品企业级多模态 RAG 智能问答系统 (FastAPI)...")

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
