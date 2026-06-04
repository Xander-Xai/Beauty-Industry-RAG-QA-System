"""
Rewrite microservice -- wraps QueryRewriter as a standalone HTTP service.

Port: 8101 (configured in config.json)
"""

from __future__ import annotations

import logging
import sys
import time
from typing import List

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

# Ensure project root is importable for common models
sys.path.insert(0, "/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge")

from common.models import QueryRewriteResult
from common.service_auth import verify_service_token

from rewriter import QueryRewriter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("rewrite-service")

app = FastAPI(title="Rewrite Service", version="1.0.0")

rewriter = QueryRewriter()


# ── Request / Response schemas ─────────────────────────────────────────


class RewriteRequest(BaseModel):
    query: str
    recent_dialogs: List[str] = Field(default_factory=list)


class VariantsRequest(BaseModel):
    rewritten_query: str


class VariantsResponse(BaseModel):
    variants: List[str] = Field(default_factory=list)


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
    try:
        result = rewriter.rewrite(req.query, req.recent_dialogs or [])
        latency_ms = round((time.time() - t0) * 1000, 1)
        logger.info(
            "rewrite OK: business_type=%s intent=%s latency_ms=%s",
            result["business_type"],
            result["intent"],
            latency_ms,
        )
        return QueryRewriteResult(**result)
    except Exception as exc:
        logger.error("rewrite failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/variants", response_model=VariantsResponse)
async def variants(req: VariantsRequest, _auth: None = Depends(verify_service_token)):
    """
    Generate 2-3 synonymous variant queries for expanded recall.
    """
    try:
        variant_list = rewriter.generate_variants(req.rewritten_query)
        return VariantsResponse(variants=variant_list)
    except Exception as exc:
        logger.error("variants failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Entrypoint ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8101)
