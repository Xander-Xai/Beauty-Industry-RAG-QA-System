"""
Redis 缓存模块（readme 10 节）

两层缓存设计：
- L1: 内存 LRU 缓存（仅公开文档，TTL 管理）
- L2: Redis 缓存（权限绑定 Key，AOF 持久化）

权限原子化 Key：role_mask + dept_mask 直接参与 cache key 计算
版本化失效：knowledge_version_epoch 驱动，旧缓存自然淘汰（Lazy GC）
"""

import hashlib
import json
import logging
import time

with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

logger = logging.getLogger(__name__)


class RedisCache:
    """
    L1/L2 双层缓存

    L1: 进程内 dict + TTL，仅服务 role_mask=0 & dept_mask=0 的全公开文档
    L2: Redis，服务所有权限组合，Key 中包含权限指纹

    用法：
        cache = RedisCache()
        val = cache.get(key, user_role_mask, user_dept_mask)
        if val is None:
            val = expensive_compute()
            cache.set(key, val, user_role_mask, user_dept_mask)
    """

    def __init__(self):
        # L1 内存缓存
        self._l1 = {}
        self._l1_max = config.get("cache_config", {}).get("l1_max_entries", 1000)
        self._l1_ttl = config.get("cache_config", {}).get("l1_ttl_seconds", 300)

        # L2 Redis
        self.redis_client = None
        self.enabled = False
        self._degraded = False  # Redis 降级模式
        self._degraded_since = 0
        self.l1_ttl = 300
        self.l2_ttl = 3600
        self._try_connect()

    def _try_connect(self):
        """尝试连接 Redis"""
        try:
            import redis
            rc = config["redis"]["cache"]
            self.redis_client = redis.Redis(
                host=rc["host"], port=rc["port"], db=rc.get("db", 0),
                decode_responses=True, socket_timeout=2, socket_connect_timeout=2,
            )
            self.redis_client.ping()
            self.enabled = True
            self._degraded = False
            self.l1_ttl = config.get("cache_config", {}).get("l1_ttl_seconds", 300)
            self.l2_ttl = config.get("cache_config", {}).get("l2_ttl_seconds", 3600)
            logger.info(f"Redis 缓存连接成功: {rc['host']}:{rc['port']}")
        except ImportError:
            logger.info("redis-py 未安装，仅使用 L1 内存缓存")
        except Exception as e:
            logger.warning(f"Redis 连接失败，进入降级模式: {e}")
            self._degraded = True
            self._degraded_since = time.time()

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
        缓存 Key 统一构造（readme 10.2）

        权限掩码直接参与 Key 计算，相同 query 对不同用户产生不同缓存分区。
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
        return hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()

    def get(self, key: str, role_mask: int = 0, dept_mask: int = 0):
        """
        查询缓存

        L1: 仅公开文档 (role_mask=0, dept_mask=0)
        L2: 所有权限组合
        """
        # L1 查询（公开文档）
        if role_mask == 0 and dept_mask == 0:
            if key in self._l1:
                val, exp = self._l1[key]
                if time.time() < exp:
                    return val
                else:
                    del self._l1[key]  # 过期清理

        # L2 查询（Redis）
        if self.enabled and self.redis_client:
            try:
                raw = self.redis_client.get(f"rag:l2:{key}")
                if raw:
                    return json.loads(raw)
            except Exception as e:
                logger.warning(f"Redis L2 读取异常: {e}")
                self._maybe_enter_degraded()

        return None

    def set(self, key: str, val, role_mask: int = 0, dept_mask: int = 0, ttl: int = None):
        """
        写入缓存

        L1: 公开文档写入内存（TTL = l1_ttl_seconds）
        L2: 所有文档写入 Redis（TTL = l2_ttl_seconds）
        """
        # L1 写入（公开文档）
        if role_mask == 0 and dept_mask == 0:
            if len(self._l1) >= self._l1_max:
                # 淘汰最旧条目
                oldest_key = next(iter(self._l1))
                del self._l1[oldest_key]
            self._l1[key] = (val, time.time() + self._l1_ttl)

        # L2 写入（Redis）
        if self.enabled and self.redis_client:
            try:
                l2_ttl = ttl or self.l2_ttl
                self.redis_client.setex(
                    f"rag:l2:{key}",
                    l2_ttl,
                    json.dumps(val, ensure_ascii=False),
                )
            except Exception as e:
                logger.warning(f"Redis L2 写入异常: {e}")
                self._maybe_enter_degraded()

    def invalidate_by_epoch(self, new_epoch: str):
        """
        版本滚动导致的缓存失效（readme 10.5）

        新请求使用新 epoch 生成 Cache Key，旧 epoch 的缓存由 LRU 自然淘汰。
        L1 需要手动清空（因为是进程内 dict）。
        """
        cleared = len(self._l1)
        self._l1.clear()
        logger.info(f"缓存失效（epoch={new_epoch}）: L1 清空 {cleared} 条")

    def _maybe_enter_degraded(self):
        """Redis 异常时进入降级模式"""
        if not self._degraded:
            self._degraded = True
            self._degraded_since = time.time()
            logger.warning("Redis 进入降级模式（仅使用 L1 内存缓存）")

    def get_stats(self) -> dict:
        """获取缓存统计"""
        degraded_duration = 0
        if self._degraded:
            degraded_duration = time.time() - self._degraded_since

        return {
            "l1_size": len(self._l1),
            "l1_max": self._l1_max,
            "l2_enabled": self.enabled,
            "l2_degraded": self._degraded,
            "l2_degraded_duration_s": round(degraded_duration, 1) if self._degraded else 0,
        }
