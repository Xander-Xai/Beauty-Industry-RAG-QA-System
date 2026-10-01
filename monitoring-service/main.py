"""
Monitoring Service -- FastAPI 微服务

提供：
- 请求指标记录与统计
- 告警规则引擎
- 健康检查
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any

# Ensure project root is on sys.path for common.* imports
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alerting import AlertingManager
from fastapi import Depends, FastAPI, HTTPException, Response
from metrics_collector import MetricsCollector
from pydantic import BaseModel, Field

from common.service_auth import verify_service_token

logger = logging.getLogger(__name__)

app = FastAPI(title="Monitoring Service", version="1.0.0")

# -- singleton instances ---------------------------------------------------
metrics = MetricsCollector()
alerting = AlertingManager(metrics)

# -- request / response schemas --------------------------------------------


class RecordMetricsRequest(BaseModel):
    """POST /api/metrics/record input."""

    request_id: str = ""
    cache_hit_level: str | None = None
    rewrite_result: dict[str, Any] | None = None
    evidence_result: dict[str, Any] | None = None
    degraded: bool = False
    kv_pressure_at_entry: float = 0.0
    stage_timings: dict[str, float] = Field(default_factory=dict)


class AlertClearResponse(BaseModel):
    status: str
    rule_name: str


# -- endpoints --------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok", "service": "monitoring-service"}


@app.post("/api/metrics/record")
async def record_metrics(req: RecordMetricsRequest, _auth: None = Depends(verify_service_token)):
    """Record request metrics."""
    try:
        metrics.record_request(req.model_dump())
        return {"status": "recorded", "request_id": req.request_id}
    except Exception as e:
        logger.error(f"Failed to record metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/metrics/stats")
async def metrics_stats(_auth: None = Depends(verify_service_token)):
    """Get aggregated metrics."""
    return metrics.get_stats()


@app.get("/api/alerts/check")
async def alerts_check(_auth: None = Depends(verify_service_token)):
    """Check alert conditions and return active alerts."""
    active = alerting.check_alerts()
    return {
        "checked_at": time.time(),
        "active_count": len(active),
        "alerts": active,
    }


@app.get("/api/alerts")
async def alerts_list(_auth: None = Depends(verify_service_token)):
    """List currently active alerts."""
    active = alerting.get_active_alerts()
    return {
        "active_count": len(active),
        "alerts": active,
    }


@app.post("/api/alerts/{rule_name}/clear")
async def alert_clear(rule_name: str, _auth: None = Depends(verify_service_token)):
    """Clear a named alert."""
    alerting.clear_alert(rule_name)
    return AlertClearResponse(status="cleared", rule_name=rule_name)


@app.get("/metrics")
async def prometheus_metrics():
    """
    Prometheus 文本格式指标端点（PRD §12）

    供 Prometheus Server 拉取，无需认证（标准行为）。
    """
    stats = metrics.get_stats()
    lines = []

    # Counters
    for name, value in stats.get("counters", {}).items():
        safe_name = name.replace(".", "_").replace("-", "_")
        lines.append(f"# TYPE rag_{safe_name} counter")
        lines.append(f"rag_{safe_name} {value}")

    # Gauges
    for name, value in stats.get("gauges", {}).items():
        safe_name = name.replace(".", "_").replace("-", "_")
        lines.append(f"# TYPE rag_{safe_name} gauge")
        lines.append(f"rag_{safe_name} {value}")

    # Cache hit rates as gauges
    cache_rates = stats.get("cache_hit_rate", {})
    for level, rate in cache_rates.items():
        lines.append(f"# TYPE rag_cache_hit_rate_{level} gauge")
        lines.append(f"rag_cache_hit_rate_{level} {rate:.4f}")

    # Latency percentiles as summaries
    for name, pcts in stats.get("latency_percentiles", {}).items():
        safe_name = name.replace(".", "_").replace("-", "_")
        lines.append(f"# TYPE rag_latency_{safe_name}_seconds summary")
        for quantile, value in [
            ("0.5", pcts.get("p50", 0)),
            ("0.95", pcts.get("p95", 0)),
            ("0.99", pcts.get("p99", 0)),
        ]:
            lines.append(f'rag_latency_{safe_name}_seconds{{quantile="{quantile}"}} {value / 1000:.6f}')
        lines.append(f"rag_latency_{safe_name}_seconds_count {pcts.get('count', 0)}")

    # Uptime
    lines.append("# TYPE rag_uptime_seconds gauge")
    lines.append(f"rag_uptime_seconds {stats.get('uptime_seconds', 0)}")

    return Response(content="\n".join(lines) + "\n", media_type="text/plain; charset=utf-8")


# -- main -------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8400)  # noqa: S104 -- container service intentionally binds all interfaces
