"""测试 MetricsCollector 的 prefix cache 指标记录（B3d）。

覆盖：
- record_prefix_cache_hit / record_prefix_cache_miss 方法
- 命中率计算（全部命中、混合、全部未命中）
- 通过 record_request(ctx) 集成时 ctx.prefix_cache_hit 的处理
- Prometheus 文本输出包含 prefix cache 指标
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from monitoring.otel_tracer import MetricsCollector


class TestMetricsCollectorPrefixCache:
    """验证 Prefix Cache 指标记录正确性。"""

    @pytest.fixture
    def metrics(self):
        return MetricsCollector()

    # ── 专用方法测试 ─────────────────────────────────

    def test_record_prefix_cache_hit(self, metrics):
        """record_prefix_cache_hit 应递增 prefix_cache.hit。"""
        metrics.record_prefix_cache_hit()
        assert metrics._counters.get("prefix_cache.hit", 0) == 1

    def test_record_prefix_cache_miss(self, metrics):
        """record_prefix_cache_miss 应递增 prefix_cache.miss。"""
        metrics.record_prefix_cache_miss()
        assert metrics._counters.get("prefix_cache.miss", 0) == 1

    # ── 命中率计算 ───────────────────────────────────

    def test_prefix_cache_hit_rate_all_hits(self, metrics):
        """全部命中时命中率应为 1.0。"""
        for _ in range(10):
            metrics.record_prefix_cache_hit()
        stats = metrics.get_stats()
        assert stats["prefix_cache_hit_rate"] == 1.0

    def test_prefix_cache_hit_rate_all_misses(self, metrics):
        """全部未命中时命中率应为 0.0。"""
        for _ in range(5):
            metrics.record_prefix_cache_miss()
        stats = metrics.get_stats()
        assert stats["prefix_cache_hit_rate"] == 0.0

    def test_prefix_cache_hit_rate_mixed(self, metrics):
        """7 命中 3 未命中时命中率应为 0.7。"""
        for _ in range(7):
            metrics.record_prefix_cache_hit()
        for _ in range(3):
            metrics.record_prefix_cache_miss()
        stats = metrics.get_stats()
        assert stats["prefix_cache_hit_rate"] == pytest.approx(0.7, abs=0.01)

    def test_prefix_cache_hit_rate_zero_total(self, metrics):
        """无数据时命中率应为 0.0（分母为 0 时回退 0.0）。"""
        stats = metrics.get_stats()
        assert stats["prefix_cache_hit_rate"] == 0.0

    # ── record_request 集成 ──────────────────────────

    def test_request_context_prefix_cache_hit(self, metrics):
        """record_request(ctx.prefix_cache_hit=True) 应记录命中。"""
        from core.pipeline_context import RequestContext

        ctx = RequestContext(user_input="test")
        ctx.prefix_cache_hit = True
        metrics.record_request(ctx)
        stats = metrics.get_stats()
        assert stats["prefix_cache_hit_rate"] == 1.0

    def test_request_context_prefix_cache_miss(self, metrics):
        """record_request(ctx.prefix_cache_hit=False) 应记录未命中。"""
        from core.pipeline_context import RequestContext

        ctx = RequestContext(user_input="test")
        ctx.prefix_cache_hit = False
        metrics.record_request(ctx)
        stats = metrics.get_stats()
        assert stats["prefix_cache_hit_rate"] == 0.0

    def test_request_context_prefix_cache_none(self, metrics):
        """ctx.prefix_cache_hit=None 应跳过（不计数 → 0.0）。"""
        from core.pipeline_context import RequestContext

        ctx = RequestContext(user_input="test")
        ctx.prefix_cache_hit = None  # 未设置
        metrics.record_request(ctx)
        stats = metrics.get_stats()
        # 没有 hit 也没有 miss → total=0 → 0.0
        assert stats["prefix_cache_hit_rate"] == 0.0

    def test_request_context_stacked_hits_and_misses(self, metrics):
        """多次 record_request 应累积 prefix cache 指标。"""
        from core.pipeline_context import RequestContext

        for _ in range(3):
            ctx = RequestContext(user_input="q1")
            ctx.prefix_cache_hit = True
            metrics.record_request(ctx)
        for _ in range(2):
            ctx = RequestContext(user_input="q2")
            ctx.prefix_cache_hit = False
            metrics.record_request(ctx)
        stats = metrics.get_stats()
        assert stats["prefix_cache_hit_rate"] == pytest.approx(0.6, abs=0.01)

    # ── Prometheus 输出 ──────────────────────────────

    def test_prefix_cache_in_prometheus_output(self, metrics):
        """to_prometheus_text 应包含 prefix_cache 相关指标。"""
        metrics.record_prefix_cache_hit()
        metrics.record_prefix_cache_miss()
        output = metrics.to_prometheus_text()
        assert "prefix_cache" in output
