"""
Rewrite microservice -- wraps QueryRewriter as a standalone HTTP service.

Port: 8101 (configured in config.json)
"""

from __future__ import annotations

import logging
import os
import sys
import time

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

# Ensure project root is importable for common models
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metrics_collector import MetricsCollector
from rewriter import QueryRewriter

from common.models import QueryRewriteResult
from common.service_auth import verify_service_token

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("rewrite-service")

app = FastAPI(title="Rewrite Service", version="1.0.0")

rewriter = QueryRewriter()
metrics = MetricsCollector()


# ── Request / Response schemas ─────────────────────────────────────────


class RewriteRequest(BaseModel):
    query: str
    recent_dialogs: list[str] = Field(default_factory=list)


class VariantsRequest(BaseModel):
    rewritten_query: str


class VariantsResponse(BaseModel):
    variants: list[str] = Field(default_factory=list)


# ── Endpoints ──────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok", "service": "rewrite-service"}


@app.post("/api/rewrite", response_model=QueryRewriteResult)
async def rewrite(req: RewriteRequest, _auth: None = Depends(verify_service_token)):
    """
    Rewrite a user query.

    Returns structured QueryRewriteResult with rewritten_query, business_type,
    intent, requires_context, standardized_entities, confidence, and fallback.
    """
    t0 = time.time()
    metrics.increment("rewrite.requests")
    try:
        result = rewriter.rewrite(req.query, req.recent_dialogs or [])
        latency_ms = round((time.time() - t0) * 1000, 1)
        metrics.observe_histogram("rewrite.latency_ms", latency_ms)
        metrics.increment("rewrite.success")
        logger.info(
            "rewrite OK: business_type=%s intent=%s latency_ms=%s",
            result["business_type"],
            result["intent"],
            latency_ms,
        )
        return QueryRewriteResult(**result)
    except Exception as exc:
        metrics.increment("rewrite.errors")
        logger.error("rewrite failed: %s", exc)
        raise HTTPException(status_code=500, detail="查询改写失败，请稍后重试")


@app.post("/api/variants", response_model=VariantsResponse)
async def variants(req: VariantsRequest, _auth: None = Depends(verify_service_token)):
    """
    Generate 2-3 synonymous variant queries for expanded recall.
    """
    metrics.increment("rewrite.variants.requests")
    t0 = time.time()
    try:
        variant_list = rewriter.generate_variants(req.rewritten_query)
        latency_ms = round((time.time() - t0) * 1000, 1)
        metrics.observe_histogram("rewrite.variants.latency_ms", latency_ms)
        return VariantsResponse(variants=variant_list)
    except Exception as exc:
        metrics.increment("rewrite.variants.errors")
        logger.error("variants failed: %s", exc)
        raise HTTPException(status_code=500, detail="内部服务错误，请稍后重试")


@app.get("/metrics")
async def get_metrics():
    """Prometheus 格式指标端点 (PRD §12)"""
    return metrics.get_prometheus_metrics()


# ── Entrypoint ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8101)
