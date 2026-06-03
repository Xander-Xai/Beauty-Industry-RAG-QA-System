"""
RedisCache -- migrated to standalone cache-service.

Two-layer cache:
  L1  In-memory dict with TTL (public docs only, role_mask=0 & dept_mask=0)
  L2  Redis with permission-keyed access

Graceful fallback: if redis-py is missing or the Redis server is unreachable,
the service operates in L1-only (degraded) mode.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
from typing import Any, Dict, Optional

# Ensure project root is on path for config.json resolution
sys.path.insert(0, "/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge")

with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

logger = logging.getLogger("cache-service.redis_cache")


class RedisCache:
    """
    L1/L2 dual-layer cache.

    L1: process-local dict + TTL -- only for fully public documents
        (role_mask=0 and dept_mask=0).
    L2: Redis -- serves all permission combinations; the permission
        fingerprint is embedded in the cache key.

    Usage::

        cache = RedisCache()
        val = cache.get(key, user_role_mask, user_dept_mask)
        if val is None:
            val = expensive_compute()
            cache.set(key, val, user_role_mask, user_dept_mask)
    """

    def __init__(self) -> None:
        # L1 in-memory cache
        self._l1: Dict[str, tuple] = {}  # key -> (value, expiry_epoch)
        cache_cfg = config.get("cache_config", {})
        self._l1_max: int = cache_cfg.get("l1_max_entries", 1000)
        self._l1_ttl: int = cache_cfg.get("l1_ttl_seconds", 300)

        # L2 Redis
        self.redis_client = None
        self.enabled: bool = False
        self._degraded: bool = False
        self._degraded_since: float = 0.0

        self._try_connect()

    # ── Connection ──────────────────────────────────────────────────

    def _try_connect(self) -> None:
        """Attempt to connect to Redis; enter degraded mode on failure."""
        try:
            import redis as redis_lib

            rc = config["redis"]["cache"]
            self.redis_client = redis_lib.Redis(
                host=rc["host"],
                port=rc["port"],
                db=rc.get("db", 0),
                decode_responses=True,
                socket_timeout=2,
                socket_connect_timeout=2,
            )
            self.redis_client.ping()
            self.enabled = True
            self._degraded = False
            logger.info("Redis cache connected: %s:%s", rc["host"], rc["port"])
        except ImportError:
            logger.info("redis-py not installed -- L1-only mode")
        except Exception as exc:
            logger.warning("Redis connection failed, entering degraded mode: %s", exc)
            self._degraded = True
            self._degraded_since = time.time()

    # ── Key computation ─────────────────────────────────────────────

    @staticmethod
    def compute_cache_key(
        normalized_query: str,
        embedding_version: str = "",
        knowledge_version_epoch: str = "",
        prompt_version: str = "",
        schema_version: str = "",
        role_mask: int = 0,
        dept_mask: int = 0,
    ) -> str:
        """
        Canonical cache-key construction.

        Permission masks are included in the hash so the same query
        produces distinct cache partitions per user permission level.
        """
        key_data = {
            "q": normalized_query,
            "ev": embedding_version,
            "ke": knowledge_version_epoch,
            "pv": prompt_version,
            "sv": schema_version,
            "rm": role_mask,
            "dm": dept_mask,
        }
        return hashlib.sha256(
            json.dumps(key_data, sort_keys=True).encode()
        ).hexdigest()

    # ── Get / Set ──────────────────────────────────────────────────

    def get(
        self,
        key: str,
        role_mask: int = 0,
        dept_mask: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """
        Look up a cached value.

        L1 hit only for public docs (role_mask=0, dept_mask=0).
        L2 hit for all permission combinations.
        """
        # L1 query (public docs only)
        if role_mask == 0 and dept_mask == 0:
            if key in self._l1:
                val, exp = self._l1[key]
                if time.time() < exp:
                    return val
                del self._l1[key]  # expired -- evict

        # L2 query (Redis)
        if self.enabled and self.redis_client:
            try:
                raw = self.redis_client.get(f"rag:l2:{key}")
                if raw:
                    return json.loads(raw)
            except Exception as exc:
                logger.warning("Redis L2 read error: %s", exc)
                self._maybe_enter_degraded()

        return None

    def set(
        self,
        key: str,
        val: Any,
        role_mask: int = 0,
        dept_mask: int = 0,
        ttl: Optional[int] = None,
    ) -> None:
        """
        Write a value to the cache.

        L1 write is restricted to public docs.
        L2 write covers all permission combinations.
        """
        # L1 write (public docs)
        if role_mask == 0 and dept_mask == 0:
            if len(self._l1) >= self._l1_max:
                oldest_key = next(iter(self._l1))
                del self._l1[oldest_key]
            self._l1[key] = (val, time.time() + self._l1_ttl)

        # L2 write (Redis)
        if self.enabled and self.redis_client:
            try:
                l2_ttl = ttl or config.get("cache_config", {}).get(
                    "l2_ttl_seconds", 3600
                )
                self.redis_client.setex(
                    f"rag:l2:{key}",
                    l2_ttl,
                    json.dumps(val, ensure_ascii=False),
                )
            except Exception as exc:
                logger.warning("Redis L2 write error: %s", exc)
                self._maybe_enter_degraded()

    # ── Invalidation ────────────────────────────────────────────────

    def invalidate_by_epoch(self, new_epoch: str) -> int:
        """
        Version-roll cache invalidation.

        New requests use the new epoch to compute cache keys so old L2
        entries naturally expire via TTL / lazy GC.  L1 must be cleared
        explicitly because it is a process-local dict.

        Returns the number of L1 entries cleared.
        """
        cleared = len(self._l1)
        self._l1.clear()
        logger.info(
            "Cache invalidated (epoch=%s): L1 cleared %d entries", new_epoch, cleared
        )
        return cleared

    # ── Degraded mode ───────────────────────────────────────────────

    def _maybe_enter_degraded(self) -> None:
        """Transition to degraded (L1-only) mode on Redis errors."""
        if not self._degraded:
            self._degraded = True
            self._degraded_since = time.time()
            logger.warning("Redis entering degraded mode (L1 memory only)")

    # ── Statistics ──────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Return cache-layer metrics."""
        degraded_duration = 0.0
        if self._degraded:
            degraded_duration = round(time.time() - self._degraded_since, 1)

        return {
            "l1_size": len(self._l1),
            "l1_max": self._l1_max,
            "l2_enabled": self.enabled,
            "l2_degraded": self._degraded,
            "l2_degraded_duration_s": degraded_duration if self._degraded else 0,
        }
