"""
Cache microservice -- L1/L2 dual-layer cache exposed as a standalone HTTP API.

Port: 8300
"""

from __future__ import annotations

import logging
import os
import sys
import time

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

# Ensure project root is importable for common models
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.models import CacheEntry
from common.service_auth import verify_service_token

from redis_cache import RedisCache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("cache-service")

app = FastAPI(title="Cache Service", version="1.0.0")

cache = RedisCache()


# ── Request / Response schemas ─────────────────────────────────────────


class CacheWriteRequest(BaseModel):
    key: str
    value: dict = Field(default_factory=dict)
    role_mask: int = 0
    dept_mask: int = 0
    ttl: int = 3600


class CacheWriteResponse(BaseModel):
    stored: bool = True
    l1_hit: bool = False
    l2_hit: bool = False


class CacheStatsResponse(BaseModel):
    l1_size: int = 0
    l1_max: int = 0
    l2_enabled: bool = False
    l2_degraded: bool = False
    l2_degraded_duration_s: float = 0.0


# ── Endpoints ──────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok", "service": "cache-service"}


@app.get("/api/cache")
async def cache_get(
    key: str = Query(..., description="Cache key (SHA256 hash)"),
    role_mask: int = Query(0, description="Caller role bitmask"),
    dept_mask: int = Query(0, description="Caller department bitmask"),
    _auth: None = Depends(verify_service_token),
):
    """
    Look up a cache entry.

    Returns the cached value if found, or 404 if not present.
    """
    t0 = time.time()
    val = cache.get(key, role_mask=role_mask, dept_mask=dept_mask)
    latency_ms = round((time.time() - t0) * 1000, 2)

    if val is None:
        raise HTTPException(status_code=404, detail="Cache miss")

    logger.info("cache_get HIT key=%s latency_ms=%s", key[:12], latency_ms)
    return {"hit": True, "value": val, "latency_ms": latency_ms}


@app.post("/api/cache", response_model=CacheWriteResponse)
async def cache_set(req: CacheWriteRequest, _auth: None = Depends(verify_service_token)):
    """
    Write a value into the cache.
    """
    cache.set(
        req.key,
        req.value,
        role_mask=req.role_mask,
        dept_mask=req.dept_mask,
        ttl=req.ttl,
    )
    logger.info("cache_set OK key=%s role_mask=%d dept_mask=%d", req.key[:12], req.role_mask, req.dept_mask)
    return CacheWriteResponse(stored=True)


@app.delete("/api/cache")
async def cache_invalidate(
    epoch: str = Query(..., description="New knowledge version epoch"),
    _auth: None = Depends(verify_service_token),
):
    """
    Invalidate cache entries by rolling to a new epoch.

    L1 is cleared entirely; L2 entries naturally expire via TTL / lazy GC.
    """
    cache.invalidate_by_epoch(epoch)
    logger.info("cache_invalidate epoch=%s", epoch)
    return {"invalidated": True, "epoch": epoch}


@app.get("/api/cache/stats", response_model=CacheStatsResponse)
async def cache_stats(_auth: None = Depends(verify_service_token)):
    """
    Return cache-layer statistics.
    """
    stats = cache.get_stats()
    return CacheStatsResponse(**stats)


# ── Entrypoint ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8300)
