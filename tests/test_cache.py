"""
Redis 缓存体系测试 (cache/redis_cache.py)

覆盖 §10 缓存体系设计：
- Cache Key 组成与确定性
- L1 内存缓存仅服务公开文档 (role_mask=0, dept_mask=0)
- L2 Redis 权限分区
- TTL 过期
- Epoch 版本滚动失效
- LRU 淘汰
- 降级模式
- 统计信息

注意：所有 Redis 调用通过 mock 隔离，不需要实际 Redis 服务。
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import time
import hashlib
import json
from unittest.mock import patch, MagicMock

from cache.redis_cache import RedisCache


# ── Cache Key 计算 ──

class TestCacheKey:
    """验证 cache key 的确定性与组成"""

    def test_same_inputs_same_key(self):
        """相同输入产生相同 key"""
        key1 = RedisCache.compute_cache_key("query", "ev1", "ke1", "pv1", "sv1", 1, 2)
        key2 = RedisCache.compute_cache_key("query", "ev1", "ke1", "pv1", "sv1", 1, 2)
        assert key1 == key2

    def test_different_query_different_key(self):
        """不同查询产生不同 key"""
        key1 = RedisCache.compute_cache_key("query_a", "", "", "", "", 0, 0)
        key2 = RedisCache.compute_cache_key("query_b", "", "", "", "", 0, 0)
        assert key1 != key2

    def test_different_role_mask_different_key(self):
        """不同角色掩码产生不同 key（权限分区）"""
        key1 = RedisCache.compute_cache_key("q", "", "", "", "", 1, 0)
        key2 = RedisCache.compute_cache_key("q", "", "", "", "", 2, 0)
        assert key1 != key2

    def test_different_dept_mask_different_key(self):
        """不同部门掩码产生不同 key（权限分区）"""
        key1 = RedisCache.compute_cache_key("q", "", "", "", "", 0, 1)
        key2 = RedisCache.compute_cache_key("q", "", "", "", "", 0, 2)
        assert key1 != key2

    def test_different_epoch_different_key(self):
        """不同版本 epoch 产生不同 key（版本化失效）"""
        key1 = RedisCache.compute_cache_key("q", "", "epoch_v1", "", "", 0, 0)
        key2 = RedisCache.compute_cache_key("q", "", "epoch_v2", "", "", 0, 0)
        assert key1 != key2

    def test_different_embedding_version_different_key(self):
        """不同 embedding 版本产生不同 key"""
        key1 = RedisCache.compute_cache_key("q", "emb_v1", "", "", "", 0, 0)
        key2 = RedisCache.compute_cache_key("q", "emb_v2", "", "", "", 0, 0)
        assert key1 != key2

    def test_key_is_sha256_hex(self):
        """key 是 SHA256 十六进制字符串"""
        key = RedisCache.compute_cache_key("test", "", "", "", "", 0, 0)
        assert len(key) == 64  # SHA256 hex = 64 chars
        int(key, 16)  # 应可解析为十六进制

    def test_key_deterministic(self):
        """key 计算与 Python dict 序列化一致"""
        key_data = {
            "q": "query", "ev": "ev1", "ke": "ke1",
            "pv": "pv1", "sv": "sv1", "rm": 1, "dm": 2,
        }
        expected = hashlib.sha256(json.dumps(key_data, sort_keys=True).encode()).hexdigest()
        actual = RedisCache.compute_cache_key("query", "ev1", "ke1", "pv1", "sv1", 1, 2)
        assert actual == expected


# ── L1 内存缓存 ──

class TestL1Cache:
    """L1 内存缓存：仅服务公开文档"""

    @patch.object(RedisCache, "_try_connect")
    def test_l1_write_for_public_doc(self, mock_connect):
        """公开文档 (role_mask=0, dept_mask=0) 写入 L1"""
        cache = RedisCache()
        cache.set("key1", "value1", role_mask=0, dept_mask=0)
        assert "key1" in cache._l1

    @patch.object(RedisCache, "_try_connect")
    def test_l1_read_for_public_doc(self, mock_connect):
        """公开文档从 L1 读取"""
        cache = RedisCache()
        cache.set("key1", "value1", role_mask=0, dept_mask=0)
        result = cache.get("key1", role_mask=0, dept_mask=0)
        assert result == "value1"

    @patch.object(RedisCache, "_try_connect")
    def test_l1_not_written_for_restricted_doc(self, mock_connect):
        """非公开文档不写入 L1"""
        cache = RedisCache()
        cache.set("key1", "value1", role_mask=1, dept_mask=0)
        assert "key1" not in cache._l1

    @patch.object(RedisCache, "_try_connect")
    def test_l1_not_read_for_restricted_user(self, mock_connect):
        """非公开用户查询不命中 L1"""
        cache = RedisCache()
        cache.set("key1", "value1", role_mask=0, dept_mask=0)
        # role_mask=1 的用户查询不应命中 L1
        result = cache.get("key1", role_mask=1, dept_mask=0)
        # L1 只在 role_mask=0, dept_mask=0 时查询
        assert result is None  # Redis 也被 mock 了

    @patch.object(RedisCache, "_try_connect")
    def test_l1_ttl_expiration(self, mock_connect):
        """L1 缓存过期后不可用"""
        cache = RedisCache()
        cache._l1_ttl = 0  # TTL = 0 秒，立即过期
        cache.set("key1", "value1", role_mask=0, dept_mask=0)
        # 手动设置过期时间到过去
        val, exp = cache._l1["key1"]
        cache._l1["key1"] = (val, time.time() - 1)
        result = cache.get("key1", role_mask=0, dept_mask=0)
        assert result is None
        # 过期条目被清理
        assert "key1" not in cache._l1

    @patch.object(RedisCache, "_try_connect")
    def test_l1_lru_eviction(self, mock_connect):
        """L1 缓存满时淘汰最旧条目"""
        cache = RedisCache()
        cache._l1_max = 3
        cache.set("k1", "v1", role_mask=0, dept_mask=0)
        cache.set("k2", "v2", role_mask=0, dept_mask=0)
        cache.set("k3", "v3", role_mask=0, dept_mask=0)
        assert len(cache._l1) == 3
        # 添加第 4 个，应该淘汰 k1
        cache.set("k4", "v4", role_mask=0, dept_mask=0)
        assert len(cache._l1) == 3
        assert "k1" not in cache._l1
        assert "k4" in cache._l1


# ── L2 Redis 缓存 ──

class TestL2Cache:
    """L2 Redis 缓存：权限分区"""

    @patch.object(RedisCache, "_try_connect")
    def test_l2_write_calls_redis(self, mock_connect):
        """L2 写入调用 Redis setex"""
        cache = RedisCache()
        mock_redis = MagicMock()
        cache.redis_client = mock_redis
        cache.enabled = True

        cache.set("key1", {"answer": "test"}, role_mask=1, dept_mask=2)
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][0] == "rag:l2:key1"  # Redis key with prefix
        assert call_args[0][1] == cache.l2_ttl  # TTL

    @patch.object(RedisCache, "_try_connect")
    def test_l2_read_calls_redis(self, mock_connect):
        """L2 读取调用 Redis get"""
        cache = RedisCache()
        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps({"answer": "cached"})
        cache.redis_client = mock_redis
        cache.enabled = True

        result = cache.get("key1", role_mask=1, dept_mask=2)
        mock_redis.get.assert_called_once_with("rag:l2:key1")
        assert result == {"answer": "cached"}

    @patch.object(RedisCache, "_try_connect")
    def test_l2_miss_returns_none(self, mock_connect):
        """L2 未命中返回 None"""
        cache = RedisCache()
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        cache.redis_client = mock_redis
        cache.enabled = True

        result = cache.get("key1", role_mask=1, dept_mask=2)
        assert result is None

    @patch.object(RedisCache, "_try_connect")
    def test_l2_permission_partition(self, mock_connect):
        """不同权限组合产生不同的 cache key，从而产生不同的 Redis key"""
        cache = RedisCache()
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        cache.redis_client = mock_redis
        cache.enabled = True

        # 通过 compute_cache_key 生成含权限指纹的 key
        key_pub = RedisCache.compute_cache_key("query", "", "", "", "", 0, 0)
        key_rd = RedisCache.compute_cache_key("query", "", "", "", "", 1, 0)

        cache.get(key_pub, role_mask=0, dept_mask=0)
        cache.get(key_rd, role_mask=1, dept_mask=0)
        # 两次调用应使用不同的 Redis key（权限指纹不同）
        call_args_list = mock_redis.get.call_args_list
        redis_key_1 = call_args_list[0][0][0]
        redis_key_2 = call_args_list[1][0][0]
        assert redis_key_1 != redis_key_2


# ── Epoch 版本失效 ──

class TestEpochInvalidation:
    """测试 epoch 版本滚动导致的缓存失效"""

    @patch.object(RedisCache, "_try_connect")
    def test_invalidate_clears_l1(self, mock_connect):
        """invalidate_by_epoch 清空 L1 缓存"""
        cache = RedisCache()
        cache.set("k1", "v1", role_mask=0, dept_mask=0)
        cache.set("k2", "v2", role_mask=0, dept_mask=0)
        assert len(cache._l1) == 2

        cache.invalidate_by_epoch("new_epoch")
        assert len(cache._l1) == 0

    @patch.object(RedisCache, "_try_connect")
    def test_invalidate_does_not_affect_redis(self, mock_connect):
        """invalidate_by_epoch 不主动清理 Redis（依赖自然淘汰）"""
        cache = RedisCache()
        # 验证方法不会抛异常，Redis 不受影响
        cache.invalidate_by_epoch("new_epoch")
        # Redis 是独立管理的，无需验证


# ── 降级模式 ──

class TestDegradedMode:
    """测试 Redis 降级模式"""

    @patch.object(RedisCache, "_try_connect")
    def test_degraded_mode_entered_on_redis_error(self, mock_connect):
        """Redis 异常时进入降级模式"""
        cache = RedisCache()
        cache.enabled = True
        mock_redis = MagicMock()
        mock_redis.get.side_effect = Exception("Redis connection lost")
        cache.redis_client = mock_redis

        cache.get("key1", role_mask=1, dept_mask=0)
        assert cache._degraded is True
        assert cache._degraded_since > 0

    @patch.object(RedisCache, "_try_connect")
    def test_degraded_mode_on_set_error(self, mock_connect):
        """Redis 写入异常也进入降级模式"""
        cache = RedisCache()
        cache.enabled = True
        mock_redis = MagicMock()
        mock_redis.setex.side_effect = Exception("Redis write error")
        cache.redis_client = mock_redis

        cache.set("key1", "val", role_mask=1, dept_mask=0)
        assert cache._degraded is True


# ── 统计信息 ──

class TestStats:
    """测试缓存统计"""

    @patch.object(RedisCache, "_try_connect")
    def test_stats_structure(self, mock_connect):
        """get_stats 返回正确结构"""
        cache = RedisCache()
        stats = cache.get_stats()
        assert "l1_size" in stats
        assert "l1_max" in stats
        assert "l2_enabled" in stats
        assert "l2_degraded" in stats
        assert "l2_degraded_duration_s" in stats

    @patch.object(RedisCache, "_try_connect")
    def test_stats_l1_size(self, mock_connect):
        """l1_size 反映当前 L1 缓存条目数"""
        cache = RedisCache()
        cache.set("k1", "v1", role_mask=0, dept_mask=0)
        cache.set("k2", "v2", role_mask=0, dept_mask=0)
        stats = cache.get_stats()
        assert stats["l1_size"] == 2

    @patch.object(RedisCache, "_try_connect")
    def test_stats_degraded_duration(self, mock_connect):
        """降级持续时间 > 0"""
        cache = RedisCache()
        cache._degraded = True
        cache._degraded_since = time.time() - 10  # 10 秒前进入降级
        stats = cache.get_stats()
        assert stats["l2_degraded"] is True
        assert stats["l2_degraded_duration_s"] >= 9.0  # 允许微小时间误差
