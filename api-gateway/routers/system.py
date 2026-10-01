"""
System 路由模块 — 健康检查与监控.

提供系统级端点：
- GET /v1/health          : 聚合所有下游服务的健康状态
- GET /v1/metrics         : 获取监控统计数据
- GET /v1/alerts          : 获取当前告警列表
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# 下游服务地址
# ---------------------------------------------------------------------------
REWRITE_SERVICE_URL = os.environ.get("REWRITE_SERVICE_URL", "http://rewrite-service:8101")
RETRIEVAL_SERVICE_URL = os.environ.get("RETRIEVAL_SERVICE_URL", "http://retrieval-service:8200")
GENERATION_SERVICE_URL = os.environ.get("GENERATION_SERVICE_URL", "http://generation-service:8100")
CACHE_SERVICE_URL = os.environ.get("CACHE_SERVICE_URL", "http://cache-service:8300")
MONITORING_SERVICE_URL = os.environ.get("MONITORING_SERVICE_URL", "http://monitoring-service:8400")

# 单个服务健康检查超时（秒）
HEALTH_CHECK_TIMEOUT_S = 3.0


# ---------------------------------------------------------------------------
# 响应模型
# ---------------------------------------------------------------------------
class ServiceHealth(BaseModel):
    """单个服务的健康状态。"""

    name: str
    url: str
    status: str = "unknown"  # "healthy" / "unhealthy" / "unknown"
    latency_ms: float = 0.0
    detail: str | None = None


class HealthResponse(BaseModel):
    """聚合健康检查响应。"""

    gateway: str = "healthy"
    overall: str = "healthy"  # "healthy" / "degraded" / "unhealthy"
    services: list[ServiceHealth] = Field(default_factory=list)
    uptime_s: float = 0.0


class MetricsResponse(BaseModel):
    """监控统计响应。"""

    success: bool = True
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class AlertItem(BaseModel):
    """单条告警。"""

    name: str = ""
    severity: str = "warning"
    message: str = ""
    timestamp: float = 0.0


class AlertsResponse(BaseModel):
    """告警列表响应。"""

    success: bool = True
    alerts: list[AlertItem] = Field(default_factory=list)
    total: int = 0


# ---------------------------------------------------------------------------
# 全局启动时间（用于计算 uptime）
# ---------------------------------------------------------------------------
_START_TIME = time.monotonic()

# 服务定义：名称 -> 健康检查 URL
_SERVICES: dict[str, str] = {
    "rewrite-service": REWRITE_SERVICE_URL,
    "retrieval-service": RETRIEVAL_SERVICE_URL,
    "generation-service": GENERATION_SERVICE_URL,
    "cache-service": CACHE_SERVICE_URL,
    "monitoring-service": MONITORING_SERVICE_URL,
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
async def _check_service_health(
    client: httpx.AsyncClient,
    name: str,
    base_url: str,
) -> ServiceHealth:
    """
    检查单个下游服务的健康状态.

    尝试访问服务的 /health 或 / 端点，根据响应判断服务状态。
    """
    health = ServiceHealth(name=name, url=base_url)

    # 尝试多个可能的健康检查端点
    health_paths = ["/health", "/api/health", "/"]

    for path in health_paths:
        start = time.monotonic()
        try:
            resp = await client.get(
                f"{base_url}{path}",
                timeout=HEALTH_CHECK_TIMEOUT_S,
            )
            elapsed_ms = (time.monotonic() - start) * 1000
            health.latency_ms = elapsed_ms

            if resp.status_code == 200:
                health.status = "healthy"
                return health
            else:
                health.status = "unhealthy"
                health.detail = f"HTTP {resp.status_code}"
                return health

        except httpx.TimeoutException:
            health.status = "unhealthy"
            health.detail = f"健康检查超时 ({HEALTH_CHECK_TIMEOUT_S}s)"
            continue

        except httpx.ConnectError:
            health.status = "unhealthy"
            health.detail = "无法连接"
            continue

        except Exception as exc:
            health.status = "unhealthy"
            health.detail = str(exc)
            continue

    # 所有端点都失败
    if health.status == "unknown":
        health.status = "unhealthy"
        health.detail = "所有健康检查端点均不可达"

    return health


# ===========================================================================
# GET /v1/health — 聚合健康检查
# ===========================================================================
@router.get("/health", response_model=HealthResponse)
async def health_check():
    """
    聚合所有下游服务的健康状态.

    - 所有服务 healthy -> overall = "healthy"
    - 部分服务 unhealthy -> overall = "degraded"
    - 所有服务 unhealthy -> overall = "unhealthy"
    """
    services: list[ServiceHealth] = []

    async with httpx.AsyncClient() as client:
        # 并发检查所有下游服务
        tasks = [_check_service_health(client, name, url) for name, url in _SERVICES.items()]
        # 使用 gather 并发执行（任何一个超时不影响其他）
        import asyncio

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                services.append(
                    ServiceHealth(
                        name="unknown",
                        url="unknown",
                        status="unhealthy",
                        detail=str(result),
                    )
                )
            else:
                services.append(result)

    # 汇总整体状态
    healthy_count = sum(1 for s in services if s.status == "healthy")
    total_count = len(services)

    if healthy_count == total_count:
        overall = "healthy"
    elif healthy_count > 0:
        overall = "degraded"
    else:
        overall = "unhealthy"

    uptime_s = time.monotonic() - _START_TIME

    logger.debug(
        "健康检查: %d/%d 服务正常, 整体状态: %s",
        healthy_count,
        total_count,
        overall,
    )

    return HealthResponse(
        gateway="healthy",
        overall=overall,
        services=services,
        uptime_s=uptime_s,
    )


# ===========================================================================
# GET /v1/metrics — 监控统计
# ===========================================================================
@router.get("/metrics", response_model=MetricsResponse)
async def metrics():
    """
    获取系统监控统计数据.

    透传至 monitoring-service (端口 8400)，包含：
    - 请求量统计
    - 延迟分布
    - GPU 利用率
    - KV Cache 压力
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{MONITORING_SERVICE_URL}/api/metrics/stats",
                timeout=5.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return MetricsResponse(success=True, data=data)

    except httpx.TimeoutException:
        logger.error("监控服务超时")
        return MetricsResponse(
            success=False,
            error="监控服务超时",
        )

    except httpx.ConnectError:
        logger.error("监控服务不可用: %s", MONITORING_SERVICE_URL)
        return MetricsResponse(
            success=False,
            error="监控服务不可用",
        )

    except httpx.HTTPStatusError as exc:
        logger.error("监控服务返回错误: %d", exc.response.status_code)
        return MetricsResponse(
            success=False,
            error=f"监控服务错误: HTTP {exc.response.status_code}",
        )

    except Exception as exc:
        logger.exception("获取监控数据失败: %s", exc)
        return MetricsResponse(
            success=False,
            error=f"获取监控数据失败: {exc}",
        )


# ===========================================================================
# GET /v1/alerts — 告警列表
# ===========================================================================
@router.get("/alerts", response_model=AlertsResponse)
async def alerts():
    """
    获取当前活跃告警列表.

    透传至 monitoring-service (端口 8400)。
    告警规则包括：
    - KV Cache 压力临界值
    - KV Cache 使用率过高
    - 重排批处理延迟
    - 查询改写降级率
    - 降级突增
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{MONITORING_SERVICE_URL}/api/alerts",
                timeout=5.0,
            )
            resp.raise_for_status()
            data = resp.json()

            # 解析告警列表
            raw_alerts = data.get("alerts", [])
            alert_items = []
            for alert in raw_alerts:
                alert_items.append(
                    AlertItem(
                        name=alert.get("name", ""),
                        severity=alert.get("severity", "warning"),
                        message=alert.get("message", ""),
                        timestamp=alert.get("timestamp", 0.0),
                    )
                )

            return AlertsResponse(
                success=True,
                alerts=alert_items,
                total=len(alert_items),
            )

    except httpx.TimeoutException:
        logger.error("告警服务超时")
        return AlertsResponse(
            success=False,
            error="告警服务超时",
        )

    except httpx.ConnectError:
        logger.error("告警服务不可用: %s", MONITORING_SERVICE_URL)
        return AlertsResponse(
            success=False,
            error="告警服务不可用",
        )

    except httpx.HTTPStatusError as exc:
        logger.error("告警服务返回错误: %d", exc.response.status_code)
        return AlertsResponse(
            success=False,
            error=f"告警服务错误: HTTP {exc.response.status_code}",
        )

    except Exception as exc:
        logger.exception("获取告警数据失败: %s", exc)
        return AlertsResponse(
            success=False,
            error=f"获取告警数据失败: {exc}",
        )
