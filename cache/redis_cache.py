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
import os
import threading
import time
from collections import OrderedDict

try:
    from common.config import get_config_dict

    config = get_config_dict()
except Exception:
    config = {
        "cache_config": {
            "l1_max_entries": 1000,
            "l1_ttl_seconds": 300,
            "l2_ttl_seconds": 3600,
        },
        "redis": {"cache": {"host": "localhost", "port": 6379, "db": 0}},
    }

logger = logging.getLogger(__name__)

#: Canonical permission-mask bound: 32-bit unsigned. Mirrors auth.bitmask_rbac and
#: common.auth; defined locally so the cache boundary does not import auth internals.
_MAX_UINT32 = 0xFFFFFFFF


class RedisCache:
    """
    L1/L2 双层缓存

    L1: 进程内 dict + TTL + 线程安全锁，仅服务 role_mask=0 & dept_mask=0 的全公开文档
    L2: Redis，服务所有权限组合，Key 中包含权限指纹

    用法：
        cache = RedisCache()
        val = cache.get(key, user_role_mask, user_dept_mask)
        if val is None:
            val = expensive_compute()
            cache.set(key, val, user_role_mask, user_dept_mask)
    """

    def __init__(self):
        # L1 内存缓存（线程安全，LRU 淘汰）— PRD §10
        self._l1: OrderedDict = OrderedDict()
        self._l1_lock = threading.Lock()
        self._l1_max = config.get("cache_config", {}).get("l1_max_entries", 1000)
        self._l1_ttl = config.get("cache_config", {}).get("l1_ttl_seconds", 300)

        # L2 Redis
        self.redis_client = None
        self.enabled = False
        self._degraded = False
        self._degraded_since = 0
        # 缓存命中/未命中计数器
        self._hit_count = 0
        self._miss_count = 0
        self.l1_ttl = 300
        self.l2_ttl = 3600
        self._try_connect()

    def _try_connect(self):
        """尝试连接 Redis"""
        try:
            import redis

            rc = config["redis"]["cache"]
            # 支持密码认证：优先从环境变量读取，其次从配置读取
            redis_password = os.environ.get("REDIS_CACHE_PASSWORD") or rc.get("password")
            connect_kwargs = {
                "host": rc["host"],
                "port": rc["port"],
                "db": rc.get("db", 0),
                "decode_responses": True,
                "socket_timeout": 2,
                "socket_connect_timeout": 2,
            }
            if redis_password:
                connect_kwargs["password"] = redis_password
            self.redis_client = redis.Redis(**connect_kwargs)
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

    @staticmethod
    def _validate_permission_scope(role_mask: int, dept_mask: int) -> tuple[int, int]:
        """Validate a permission identity against the canonical uint32 contract.

        缓存边界的 fail-closed 校验。L2 物理 key 把 mask 直接插入 f-string，因此没有这一步
        时 ``"1"`` 与 ``1`` 会落到同一个 Redis 地址；而 ``True == 1`` / ``False == 0``
        又让布尔值与合法掩码别名，甚至能绕过 L1 的 public-only 判断。

        规则：

        * ``type(value) is int`` —— 不用 ``isinstance``，因为 ``isinstance(True, int)``
          为真，布尔值会被当成 0/1 掩码；
        * ``0 <= value <= 0xFFFFFFFF``。

        不做 ``int(value)`` 转换、不 clamp、不取 abs、不 fallback：非法 identity 直接
        ``ValueError``，由调用方在任何 cache IO 之前失败。

        唯一实现：``get()`` / ``set()`` / ``_build_l2_storage_key()`` 共用本方法。
        与 ``common.auth`` 的 JWT claim 校验是两个独立的 defense-in-depth 边界，
        cache 层不 import auth 内部实现。
        """
        for name, value in (("role_mask", role_mask), ("dept_mask", dept_mask)):
            if type(value) is not int:
                raise ValueError(f"{name} must be an int, got {type(value).__name__}")
            if not 0 <= value <= _MAX_UINT32:
                raise ValueError(f"{name} must be within [0, {_MAX_UINT32}], got {value}")
        return role_mask, dept_mask

    @staticmethod
    def _build_l2_storage_key(key: str, role_mask: int, dept_mask: int) -> str:
        """构造 L2 物理 Redis key：logical key + 权限分区。

        L1/L2 双层缓存的第二层防线。``compute_cache_key()`` 与 pipeline 的
        ``_build_cache_key()`` 已经把 role_mask/dept_mask 放进 *logical* key，但那是调用方
        纪律：一旦某个调用方忘了先做 permission-aware 构造，相同 logical key 在不同
        权限下会落到同一个物理地址。旧实现正是 ``rag:l2:{key}``，与权限完全无关。

        这里由 cache 对象自身强制分区，使权限隔离不再只依赖调用方。mask 是授权位掩码，
        直接放进 key 便于调试，不再额外做一次哈希。

        本方法自身也 fail closed：非法 identity 在此抛 ``ValueError``，不依赖 get/set
        先校验——否则直接调用本方法仍可生成别名地址。

        唯一来源：``get()`` 与 ``set()`` 必须共用本 helper，避免读写 key drift。
        """
        role_mask, dept_mask = RedisCache._validate_permission_scope(role_mask, dept_mask)
        return f"rag:l2:rm:{role_mask}:dm:{dept_mask}:{key}"

    def _ensure_counters(self):
        """确保 hit/miss 计数器存在（兼容部分初始化场景）。"""
        if not hasattr(self, "_hit_count"):
            self._hit_count = 0
        if not hasattr(self, "_miss_count"):
            self._miss_count = 0

    def get(self, key: str, role_mask: int = 0, dept_mask: int = 0):
        """
        查询缓存（含命中/未命中计数）

        L1: 仅公开文档 (role_mask=0, dept_mask=0)，线程安全
        L2: 所有权限组合
        """
        self._ensure_counters()
        # 先校验 identity，再做任何 cache IO：非法掩码不得读取 L1，也不得触碰 Redis。
        role_mask, dept_mask = self._validate_permission_scope(role_mask, dept_mask)
        # L1 查询（公开文档）— LRU 淘汰
        if role_mask == 0 and dept_mask == 0:
            with self._l1_lock:
                if key in self._l1:
                    val, exp = self._l1[key]
                    if time.time() < exp:
                        self._l1.move_to_end(key)  # LRU: 标记为最近使用
                        self._hit_count += 1
                        return val
                    else:
                        del self._l1[key]

        # L2 查询（Redis）— 物理 key 由 role/dept 分区，cache 对象自身强制
        if self.enabled and self.redis_client:
            try:
                storage_key = self._build_l2_storage_key(key, role_mask, dept_mask)
                raw = self.redis_client.get(storage_key)
                if raw:
                    self._hit_count += 1
                    return json.loads(raw)
            except Exception as e:
                logger.warning(f"Redis L2 读取异常: {e}")
                self._maybe_enter_degraded()

        self._miss_count += 1
        return None

    def set(self, key: str, val, role_mask: int = 0, dept_mask: int = 0, ttl: int = None):
        """
        写入缓存

        L1: 公开文档写入内存（TTL = l1_ttl_seconds），线程安全
        L2: 所有文档写入 Redis（TTL = l2_ttl_seconds）
        """
        # 先校验 identity：非法掩码不得写入 L1，也不得调用 Redis.setex。
        role_mask, dept_mask = self._validate_permission_scope(role_mask, dept_mask)
        # L1 写入（公开文档）— LRU 淘汰
        if role_mask == 0 and dept_mask == 0:
            with self._l1_lock:
                if key in self._l1:
                    self._l1.pop(key)
                elif len(self._l1) >= self._l1_max:
                    self._l1.popitem(last=False)  # LRU: 淘汰最久未使用的
                self._l1[key] = (val, time.time() + self._l1_ttl)
                self._l1.move_to_end(key)  # 标记为最近使用

        # L2 写入（Redis）— 与读取共用同一个 storage key helper
        if self.enabled and self.redis_client:
            try:
                l2_ttl = ttl or self.l2_ttl
                self.redis_client.setex(
                    self._build_l2_storage_key(key, role_mask, dept_mask),
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
        with self._l1_lock:
            cleared = len(self._l1)
            self._l1.clear()
        logger.info(f"缓存失效（epoch={new_epoch}）: L1 清空 {cleared} 条")

    def _maybe_enter_degraded(self):
        """Redis 异常时进入降级模式"""
        if not self._degraded:
            self._degraded = True
            self._degraded_since = time.time()
            logger.warning("Redis 进入降级模式（仅使用 L1 内存缓存）")

    def get_hit_stats(self) -> dict:
        """获取缓存命中/未命中统计"""
        self._ensure_counters()
        total = self._hit_count + self._miss_count
        return {
            "hit_count": self._hit_count,
            "miss_count": self._miss_count,
            "total_requests": total,
            "hit_rate": round(self._hit_count / total, 4) if total > 0 else 0.0,
        }

    def get_stats(self) -> dict:
        """获取缓存统计（含命中率）"""
        degraded_duration = 0
        if self._degraded:
            degraded_duration = time.time() - self._degraded_since
        with self._l1_lock:
            l1_size = len(self._l1)

        hit_stats = self.get_hit_stats()
        return {
            "l1_size": l1_size,
            "l1_max": self._l1_max,
            "l2_enabled": self.enabled,
            "l2_degraded": self._degraded,
            "l2_degraded_duration_s": round(degraded_duration, 1) if self._degraded else 0,
            "hit_count": hit_stats["hit_count"],
            "miss_count": hit_stats["miss_count"],
            "hit_rate": hit_stats["hit_rate"],
        }
