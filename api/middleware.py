"""
FastAPI 中间件

- CORS 中间件（可通过 CORS_ORIGINS 环境变量配置允许来源）
- 请求日志中间件（记录 method, path, status, elapsed, X-Request-ID）
"""

from __future__ import annotations

import logging
import os
import time

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from common.audit import new_request_id, reset_request_id, set_request_id
from common.config import get_config, is_production_mode

logger = logging.getLogger(__name__)

#: In-flight request count, published so `rag_http_active_requests` reflects real
#: saturation rather than a default zero.
_active_requests = 0


def _record_http_metrics(status_code: int, elapsed_ms: float) -> None:
    """Publish the HTTP observation on the canonical metrics collector.

    Imported lazily and defensively: metrics must never be the reason a request
    fails, so any problem here is swallowed after being logged at debug level.
    """
    global _active_requests
    try:
        from api.routes import get_metrics

        metrics = get_metrics()
        metrics.record_http_request(status_code, elapsed_ms)
        metrics.set_active_requests(_active_requests)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("HTTP metric recording failed: %s", exc)


def note_request_started() -> None:
    global _active_requests
    _active_requests += 1


def note_request_finished() -> None:
    global _active_requests
    _active_requests = max(0, _active_requests - 1)


# CORS 来源白名单：通过环境变量配置，多个来源用逗号分隔
# 生产环境应设置为具体前端域名，如 "https://internal.example.com"
_cors_origins_str = os.environ.get("CORS_ORIGINS", "")
if _cors_origins_str:
    CORS_ORIGINS = [o.strip() for o in _cors_origins_str.split(",") if o.strip()]
else:
    # H-10: 生产模式下 CORS_ORIGINS 必须显式设置
    cfg = get_config()
    if cfg.deployment_mode == "production" or is_production_mode():
        raise RuntimeError("生产模式下必须通过 CORS_ORIGINS 环境变量设置允许的来源（不可使用通配符 *）")
    CORS_ORIGINS = ["*"]
    logger.warning("CORS_ORIGINS 未设置，使用通配符 *（仅限开发模式）")

# 当使用通配符时禁用 credentials（浏览器安全要求）
CORS_ALLOW_CREDENTIALS = CORS_ORIGINS != ["*"]


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

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 获取或生成 request-id。Incoming header wins so a gateway-supplied id
        # survives end to end.
        request_id = request.headers.get("X-Request-ID") or new_request_id()

        # Publish the id for the duration of the request so audit events, the
        # access log line and any trace span all reference the same value
        # without it being threaded through every call site.
        token = set_request_id(request_id)
        note_request_started()
        t_start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # An unhandled exception becomes a 5xx. Record it here, because the
            # route never returned and this middleware is the only place that
            # still sees the failure.
            note_request_finished()
            _record_http_metrics(500, (time.perf_counter() - t_start) * 1000)
            raise
        try:
            elapsed_ms = (time.perf_counter() - t_start) * 1000

            # 将 request-id 注入响应头
            response.headers["X-Request-ID"] = request_id

            _record_http_metrics(response.status_code, elapsed_ms)

            logger.info(
                "%s %s -> %d (%.1fms) [req=%s]",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
                request_id,
            )

            return response
        finally:
            note_request_finished()
            reset_request_id(token)


def setup_middleware(app: FastAPI) -> None:
    """
    为 FastAPI 应用配置全局中间件。

    注意：中间件按注册的 **逆序** 执行（先注册的后执行）。
    这里先添加 CORS（最外层），再添加请求日志。
    """
    # CORS — 生产环境通过 CORS_ORIGINS 环境变量限制来源
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=CORS_ALLOW_CREDENTIALS,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-User-ID", "X-Role-Mask", "X-Dept-Mask"],
    )

    # 请求日志
    app.add_middleware(RequestLoggingMiddleware)

    # 审计日志（最内层，离 app 最近）
    from auth.audit_log import AuditLogMiddleware

    app.add_middleware(AuditLogMiddleware)
