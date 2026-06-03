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
import threading
import time
from typing import Any, Dict, Optional

# Ensure project root is on sys.path for common.* imports
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from metrics_collector import MetricsCollector
from alerting import AlertingManager

logger = logging.getLogger(__name__)

app = FastAPI(title="Monitoring Service", version="1.0.0")

# -- singleton instances ---------------------------------------------------
metrics = MetricsCollector()
alerting = AlertingManager(metrics)

# -- request / response schemas --------------------------------------------


class RecordMetricsRequest(BaseModel):
    """POST /api/metrics/record input."""

    request_id: str = ""
    cache_hit_level: Optional[str] = None
    rewrite_result: Optional[Dict[str, Any]] = None
    evidence_result: Optional[Dict[str, Any]] = None
    degraded: bool = False
    kv_pressure_at_entry: float = 0.0
    stage_timings: Dict[str, float] = Field(default_factory=dict)


class AlertClearResponse(BaseModel):
    status: str
    rule_name: str


# -- endpoints --------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok", "service": "monitoring-service"}


@app.post("/api/metrics/record")
async def record_metrics(req: RecordMetricsRequest):
    """Record request metrics."""
    try:
        metrics.record_request(req.model_dump())
        return {"status": "recorded", "request_id": req.request_id}
    except Exception as e:
        logger.error(f"Failed to record metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/metrics/stats")
async def metrics_stats():
    """Get aggregated metrics."""
    return metrics.get_stats()


@app.get("/api/alerts/check")
async def alerts_check():
    """Check alert conditions and return active alerts."""
    active = alerting.check_alerts()
    return {
        "checked_at": time.time(),
        "active_count": len(active),
        "alerts": active,
    }


@app.get("/api/alerts")
async def alerts_list():
    """List currently active alerts."""
    active = alerting.get_active_alerts()
    return {
        "active_count": len(active),
        "alerts": active,
    }


@app.post("/api/alerts/{rule_name}/clear")
async def alert_clear(rule_name: str):
    """Clear a named alert."""
    alerting.clear_alert(rule_name)
    return AlertClearResponse(status="cleared", rule_name=rule_name)


# -- main -------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8400)
