"""
令牌桶限流中间件 — FastAPI 依赖项（PRD §13）.

基于用户身份的业务类型进行差异化限流：
- 法规类（regulation）: 60 QPS（高优先级，合规查询时效性强）
- 研发类（research）: 30 QPS
- 通用类（general）: 10 QPS（含其他所有类型）

优先使用 Redis Lua 脚本令牌桶（多实例部署），
Redis 不可用时自动降级为内存级令牌桶（单实例部署）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass, field

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 确保 api-gateway 目录在 sys.path 中
_GATEWAY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _GATEWAY_DIR not in sys.path:
    sys.path.insert(0, _GATEWAY_DIR)

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 业务类型 -> QPS 上限映射（PRD §13: Regulation 60%, R&D 30%, Chat 10%）
# ---------------------------------------------------------------------------
BUSINESS_TYPE_LIMITS: dict[str, float] = {
    "regulation": 60.0,  # 法规类：最高优先级
    "research": 30.0,  # 研发类：中等优先级
    "general": 10.0,  # 通用类：低优先级
}

DEFAULT_LIMIT: float = 10.0
BURST_MULTIPLIER: float = 2.0

# ---------------------------------------------------------------------------
# Redis 令牌桶 Lua 脚本（PRD §13: Redis-State 限流）
# ---------------------------------------------------------------------------
_BUCKET_LUA_SOURCE = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

local data = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(data[1]) or capacity
local last_refill = tonumber(data[2]) or now

-- 补充令牌
local elapsed = math.max(0, now - last_refill)
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed = 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
end

redis.call('HMSET', key, 'tokens', tostring(tokens), 'last_refill', tostring(now))
redis.call('EXPIRE', key, 300)
return allowed
"""


@dataclass
class TokenBucket:
    """内存级令牌桶（Redis 不可用时的降级方案）。"""

    capacity: float
    tokens: float
    refill_rate: float
    last_refill: float = field(default_factory=time.monotonic)

    def consume(self, now: float | None = None) -> bool:
        if now is None:
            now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class RateLimiter:
    """
    令牌桶限流器（PRD §13）.

    优先 Redis-backed（多实例一致性），不可用时降级为内存级。
    按 user_id + business_type 维度限流。
    """

    def __init__(
        self,
        limits: dict[str, float] | None = None,
        burst_multiplier: float = BURST_MULTIPLIER,
        cleanup_interval_s: float = 60.0,
    ) -> None:
        self._limits = limits or BUSINESS_TYPE_LIMITS
        self._burst_multiplier = burst_multiplier
        self._cleanup_interval = cleanup_interval_s
        self._buckets: dict[str, TokenBucket] = {}
        self._last_cleanup = time.monotonic()
        self._lock = asyncio.Lock()
        # Redis 连接（延迟初始化）
        self._redis = None
        self._redis_available = False
        self._lua_sha = None
        self._try_init_redis()

    def _try_init_redis(self):
        """尝试初始化 Redis 连接（用于 Redis-State 限流）"""
        try:
            import redis

            redis_cfg = {
                "host": os.environ.get("REDIS_HOST", "redis"),
                "port": int(os.environ.get("REDIS_PORT", "6379")),
                "db": int(os.environ.get("REDIS_RATE_LIMIT_DB", "2")),
                "decode_responses": True,
                "socket_connect_timeout": 2,
                "socket_timeout": 2,
            }
            self._redis = redis.Redis(**redis_cfg)
            self._redis.ping()
            self._lua_sha = self._redis.script_load(_BUCKET_LUA_SOURCE)
            self._redis_available = True
            logger.info("Redis 限流后端初始化完成")
        except Exception as e:
            logger.info(f"Redis 限流后端不可用，降级为内存级: {e}")
            self._redis_available = False

    async def check_rate_limit(
        self,
        user_id: str,
        business_type: str,
    ) -> None:
        """
        检查并消费一个令牌，超限时抛出 HTTPException.

        Redis 可用时使用 Lua 脚本原子操作，不可用时降级为内存级。
        """
        if self._redis_available and self._redis is not None:
            await self._check_rate_limit_redis(user_id, business_type)
        else:
            async with self._lock:
                await self._cleanup_stale_buckets()
                bucket = self._get_or_create_bucket(user_id, business_type)
                if not bucket.consume():
                    raise self._rate_limit_error(business_type)

    async def _check_rate_limit_redis(self, user_id: str, business_type: str) -> None:
        """Redis-backed 令牌桶（原子 Lua 脚本）"""
        try:
            rate = self._limits.get(business_type, DEFAULT_LIMIT)
            capacity = rate * self._burst_multiplier
            key = f"rl:{user_id}:{business_type}"
            now = time.time()
            allowed = self._redis.evalsha(
                self._lua_sha,
                1,
                key,
                capacity,
                rate,
                now,
            )
            if not allowed:
                raise self._rate_limit_error(business_type)
        except HTTPException:
            raise
        except Exception as e:
            # Redis 操作失败，降级为内存级
            logger.warning(f"Redis 限流失败，降级为内存级: {e}")
            async with self._lock:
                bucket = self._get_or_create_bucket(user_id, business_type)
                if not bucket.consume():
                    raise self._rate_limit_error(business_type) from e

    @staticmethod
    def _rate_limit_error(business_type: str) -> HTTPException:
        limit = BUSINESS_TYPE_LIMITS.get(business_type, DEFAULT_LIMIT)
        logger.warning("业务类型 [%s] 触发限流 (QPS 上限: %.0f)", business_type, limit)
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"请求过于频繁，{business_type} 类型限流上限为 {limit:.0f} QPS",
            headers={"Retry-After": "1"},
        )

    def _get_or_create_bucket(self, user_id: str, business_type: str) -> TokenBucket:
        key = f"{user_id}:{business_type}"
        bucket = self._buckets.get(key)
        if bucket is None:
            rate = self._limits.get(business_type, DEFAULT_LIMIT)
            capacity = rate * self._burst_multiplier
            bucket = TokenBucket(capacity=capacity, tokens=capacity, refill_rate=rate)
            self._buckets[key] = bucket
        return bucket

    async def _cleanup_stale_buckets(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        stale_keys = [k for k, b in self._buckets.items() if now - b.last_refill > 300.0]
        for key in stale_keys:
            del self._buckets[key]
        if stale_keys:
            logger.info("限流器清理了 %d 个过期令牌桶", len(stale_keys))

    @property
    def stats(self) -> dict[str, int]:
        return {"active_buckets": len(self._buckets), "redis_available": self._redis_available}


# ---------------------------------------------------------------------------
# 全局限流器实例（单例）
# ---------------------------------------------------------------------------
_rate_limiter_instance: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter_instance
    if _rate_limiter_instance is None:
        _rate_limiter_instance = RateLimiter()
    return _rate_limiter_instance


async def rate_limit_dependency(request: Request) -> None:
    """
    FastAPI 依赖项：基于用户身份和业务类型执行限流检查（PRD §13）.

    优先从请求头 X-Business-Type 获取业务类型，否则按路径推断。
    """
    from middleware.auth_middleware import get_current_user

    identity = await get_current_user(request)

    business_type = request.headers.get("X-Business-Type", "")
    if not business_type:
        path = request.url.path.lower()
        if "regulation" in path or "compliance" in path:
            business_type = "regulation"
        elif "research" in path or "rd" in path:
            business_type = "research"
        else:
            business_type = "general"

    limiter = get_rate_limiter()
    await limiter.check_rate_limit(identity.user_id, business_type)
