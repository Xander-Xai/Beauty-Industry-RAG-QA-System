"""
令牌桶限流中间件 — FastAPI 依赖项.

基于用户身份的业务类型进行差异化限流：
- 法规类（regulation）: 60 QPS（高优先级，合规查询时效性强）
- 研发类（research）: 30 QPS
- 通用类（general）: 10 QPS（含其他所有类型）

使用内存级令牌桶算法，无外部依赖，适合单实例部署。
多实例部署时应替换为 Redis 限流方案。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Optional

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 确保 api-gateway 目录在 sys.path 中
_GATEWAY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _GATEWAY_DIR not in sys.path:
    sys.path.insert(0, _GATEWAY_DIR)

from fastapi import Request, HTTPException, status

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 业务类型 -> QPS 上限映射
# ---------------------------------------------------------------------------
BUSINESS_TYPE_LIMITS: Dict[str, float] = {
    "regulation": 60.0,   # 法规类：最高优先级
    "research": 30.0,     # 研发类：中等优先级
    "general": 10.0,      # 通用类：低优先级
}

# 默认限流上限（未知业务类型）
DEFAULT_LIMIT: float = 10.0

# 令牌桶突发容量倍数（允许短时突发流量）
BURST_MULTIPLIER: float = 2.0


@dataclass
class TokenBucket:
    """
    单个用户的令牌桶.

    Attributes:
        capacity: 桶的最大容量（允许的突发量）
        tokens: 当前可用令牌数
        refill_rate: 令牌补充速率（个/秒）
        last_refill: 上次补充令牌的时间戳
    """
    capacity: float
    tokens: float
    refill_rate: float  # 每秒补充令牌数
    last_refill: float = field(default_factory=time.monotonic)

    def consume(self, now: Optional[float] = None) -> bool:
        """
        尝试消费一个令牌.

        Args:
            now: 当前时间戳（可传入以避免重复调用 time.monotonic）

        Returns:
            True 表示消费成功，False 表示已被限流
        """
        if now is None:
            now = time.monotonic()

        # 计算自上次补充以来应添加的令牌数
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

        # 尝试消费一个令牌
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class RateLimiter:
    """
    内存级令牌桶限流器.

    按 user_id + business_type 维度进行限流，
    每个组合维护独立的令牌桶。
    """

    def __init__(
        self,
        limits: Optional[Dict[str, float]] = None,
        burst_multiplier: float = BURST_MULTIPLIER,
        cleanup_interval_s: float = 60.0,
    ) -> None:
        """
        初始化限流器.

        Args:
            limits: 业务类型 -> QPS 上限映射，默认使用 BUSINESS_TYPE_LIMITS
            burst_multiplier: 突发容量倍数
            cleanup_interval_s: 过期令牌桶清理间隔（秒）
        """
        self._limits = limits or BUSINESS_TYPE_LIMITS
        self._burst_multiplier = burst_multiplier
        self._cleanup_interval = cleanup_interval_s
        # key = f"{user_id}:{business_type}" -> TokenBucket
        self._buckets: Dict[str, TokenBucket] = {}
        self._last_cleanup = time.monotonic()
        # 使用锁保护令牌桶的并发访问
        self._lock = asyncio.Lock()

    def _get_or_create_bucket(self, user_id: str, business_type: str) -> TokenBucket:
        """获取或创建指定用户和业务类型的令牌桶。"""
        key = f"{user_id}:{business_type}"
        bucket = self._buckets.get(key)
        if bucket is None:
            rate = self._limits.get(business_type, DEFAULT_LIMIT)
            capacity = rate * self._burst_multiplier
            bucket = TokenBucket(capacity=capacity, tokens=capacity, refill_rate=rate)
            self._buckets[key] = bucket
        return bucket

    async def _cleanup_stale_buckets(self) -> None:
        """清理长时间未使用的令牌桶，防止内存泄漏。"""
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now

        stale_keys = []
        for key, bucket in self._buckets.items():
            # 超过 5 分钟未使用则清理
            if now - bucket.last_refill > 300.0:
                stale_keys.append(key)
        for key in stale_keys:
            del self._buckets[key]

        if stale_keys:
            logger.info("限流器清理了 %d 个过期令牌桶", len(stale_keys))

    async def check_rate_limit(
        self,
        user_id: str,
        business_type: str,
    ) -> None:
        """
        检查并消费一个令牌，超限时抛出 HTTPException.

        Args:
            user_id: 用户 ID
            business_type: 业务类型（regulation / research / general）

        Raises:
            HTTPException: 429 Too Many Requests
        """
        async with self._lock:
            await self._cleanup_stale_buckets()
            bucket = self._get_or_create_bucket(user_id, business_type)
            if not bucket.consume():
                limit = self._limits.get(business_type, DEFAULT_LIMIT)
                logger.warning(
                    "用户 [%s] 业务类型 [%s] 触发限流 (QPS 上限: %.0f)",
                    user_id,
                    business_type,
                    limit,
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"请求过于频繁，{business_type} 类型限流上限为 {limit:.0f} QPS",
                    headers={"Retry-After": "1"},
                )

    @property
    def stats(self) -> Dict[str, int]:
        """返回当前各维度的令牌桶数量（监控用）。"""
        return {"active_buckets": len(self._buckets)}


# ---------------------------------------------------------------------------
# 全局限流器实例（单例）
# ---------------------------------------------------------------------------
_rate_limiter_instance: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """获取全局 RateLimiter 单例。"""
    global _rate_limiter_instance
    if _rate_limiter_instance is None:
        _rate_limiter_instance = RateLimiter()
    return _rate_limiter_instance


async def rate_limit_dependency(request: Request) -> None:
    """
    FastAPI 依赖项：基于用户身份和业务类型执行限流检查.

    从请求路径推断业务类型：
    - /api/regulation 或包含 regulation -> regulation (60 QPS)
    - /api/research 或包含 research -> research (30 QPS)
    - 其他 -> general (10 QPS)

    如果请求头 X-Business-Type 存在，则优先使用。
    """
    from middleware.auth_middleware import get_current_user

    identity = await get_current_user(request)

    # 优先从请求头获取业务类型
    business_type = request.headers.get("X-Business-Type", "")
    if not business_type:
        # 根据请求路径推断业务类型
        path = request.url.path.lower()
        if "regulation" in path or "compliance" in path:
            business_type = "regulation"
        elif "research" in path or "rd" in path:
            business_type = "research"
        else:
            business_type = "general"

    limiter = get_rate_limiter()
    await limiter.check_rate_limit(identity.user_id, business_type)
