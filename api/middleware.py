"""
FastAPI 中间件

- CORS 中间件（允许所有来源）
- 请求日志中间件（记录 method, path, status, elapsed, X-Request-ID）
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    请求日志中间件

    记录每个请求的：
    - HTTP 方法
    - 路径
    - 响应状态码
    - 处理耗时（ms）
    - X-Request-ID（不存在时自动生成）
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # 获取或生成 request-id
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())[:12]

        t_start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - t_start) * 1000

        # 将 request-id 注入响应头
        response.headers["X-Request-ID"] = request_id

        logger.info(
            "%s %s -> %d (%.1fms) [req=%s]",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )

        return response


def setup_middleware(app: FastAPI) -> None:
    """
    为 FastAPI 应用配置全局中间件。

    注意：中间件按注册的 **逆序** 执行（先注册的后执行）。
    这里先添加 CORS（最外层），再添加请求日志。
    """
    # CORS - 允许所有来源（开发阶段）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 请求日志
    app.add_middleware(RequestLoggingMiddleware)
