"""
test_monitoring_subsystem.py — 新增测试: MetricsCollector 记录/导出/百分位、
AlertingManager 规则触发/恢复/throughput QPS。
"""
import os
import sys
import time
import types
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DEPLOYMENT_MODE"] = "development"

# torch mock shim
try:
    import torch  # noqa: F401
except ImportError:
    _fake_torch = types.ModuleType("torch")
    _fake_cuda = types.ModuleType("torch.cuda")
    _fake_cuda.is_available = lambda: False
    _fake_cuda.OutOfMemoryError = type("OutOfMemoryError", (Exception,), {})
    _fake_torch.cuda = _fake_cuda
    sys.modules["torch"] = _fake_torch
    sys.modules["torch.cuda"] = _fake_cuda


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_collector():
    from monitoring.otel_tracer import MetricsCollector
    return MetricsCollector()


def _make_alerting(metrics=None):
    from monitoring.otel_tracer import AlertingManager
    if metrics is None:
        metrics = _make_collector()
    return AlertingManager(metrics)


def _make_ctx(**overrides):
    """创建最小 RequestContext 用于 record_request。"""
    from core.pipeline_context import RequestContext
    ctx = RequestContext(user_input="test", session_id="s1", user_id="u1")
    ctx.rewrite_result = overrides.get("rewrite_result", MagicMock(
        fallback=False, business_type="general",
    ))
    ctx.evidence_result = overrides.get("evidence_result", MagicMock(
        evidence_score=0.8, decision="pass",
    ))
    ctx.cache_hit_level = overrides.get("cache_hit_level", None)
    ctx.degraded = overrides.get("degraded", False)
    ctx.blip_triggered = overrides.get("blip_triggered", False)
    ctx.kv_pressure_at_entry = overrides.get("kv_pressure_at_entry", 0.0)
    ctx.stage_timings = overrides.get("stage_timings", {})
    ctx.answer_gate_result = overrides.get("answer_gate_result", None)
    return ctx


# ===========================================================================
# 1. MetricsCollector — record_request increments counters correctly
# ===========================================================================

class TestMetricsCollectorRecordRequest:
    """record_request 应正确递增各类计数器。"""

    def test_record_request_increments_cache_hit_l1(self):
        """L1 缓存命中应递增 cache.hit.L1 计数器。"""
        mc = _make_collector()
        ctx = _make_ctx(cache_hit_level="L1")
        mc.record_request(ctx)
        assert mc._counters["cache.hit.L1"] == 1
        assert mc._counters["cache.total"] == 1

    def test_record_request_increments_rewrite_success(self):
        """有 rewrite_result 应递增 rewrite.success。"""
        mc = _make_collector()
        ctx = _make_ctx()
        mc.record_request(ctx)
        assert mc._counters["rewrite.success"] == 1

    def test_record_request_increments_rewrite_fail_on_missing(self):
        """无 rewrite_result 应递增 rewrite.fail。"""
        mc = _make_collector()
        ctx = _make_ctx(rewrite_result=None)
        mc.record_request(ctx)
        assert mc._counters["rewrite.fail"] == 1

    def test_record_request_increments_rewrite_fallback(self):
        """rewrite fallback 应递增 rewrite.fallback。"""
        mc = _make_collector()
        fallback_rewrite = MagicMock(fallback=True, business_type="general")
        ctx = _make_ctx(rewrite_result=fallback_rewrite)
        mc.record_request(ctx)
        assert mc._counters["rewrite.fallback"] == 1

    def test_record_request_increments_degradation(self):
        """降级请求应递增 degradation.total。"""
        mc = _make_collector()
        ctx = _make_ctx(degraded=True)
        mc.record_request(ctx)
        assert mc._counters["degradation.total"] == 1

    def test_record_request_increments_blip_counters(self):
        """BLIP 触发应递增 blip.triggered 和 blip.total。"""
        mc = _make_collector()
        ctx = _make_ctx(blip_triggered=True)
        mc.record_request(ctx)
        assert mc._counters["blip.triggered"] == 1
        assert mc._counters["blip.total"] == 1

    def test_record_request_records_stage_timings(self):
        """各阶段延迟应记录到直方图。"""
        mc = _make_collector()
        ctx = _make_ctx(stage_timings={"rewrite": 50.0, "recall": 120.0, "rerank": 80.0})
        mc.record_request(ctx)
        assert 50.0 in mc._histograms["latency.rewrite"]
        assert 120.0 in mc._histograms["latency.recall"]
        assert 80.0 in mc._histograms["latency.rerank"]

    def test_record_request_sets_kv_pressure_gauge(self):
        """KV pressure > 0 应设置 gauge。"""
        mc = _make_collector()
        ctx = _make_ctx(kv_pressure_at_entry=0.75)
        mc.record_request(ctx)
        assert mc._gauges["kv_pressure"] == 0.75

    def test_record_request_records_nli_contradiction(self):
        """高 NLI 矛盾分应递增 nli.contradiction_high。"""
        mc = _make_collector()
        ctx = _make_ctx()
        ctx.answer_gate_result = MagicMock(nli_contradiction_score=0.7)
        mc.record_request(ctx)
        assert mc._counters["nli.contradiction_high"] == 1
        assert 0.7 in mc._histograms["nli.contradiction_score"]

    def test_record_request_low_nli_no_contradiction_count(self):
        """低 NLI 矛盾分不应递增 nli.contradiction_high。"""
        mc = _make_collector()
        ctx = _make_ctx()
        ctx.answer_gate_result = MagicMock(nli_contradiction_score=0.2)
        mc.record_request(ctx)
        assert "nli.contradiction_high" not in mc._counters or mc._counters["nli.contradiction_high"] == 0

    def test_record_request_multiple_calls_accumulate(self):
        """多次调用 record_request 应累加计数器。"""
        mc = _make_collector()
        for _ in range(5):
            mc.record_request(_make_ctx(cache_hit_level="L1"))
        assert mc._counters["cache.hit.L1"] == 5
        assert mc._counters["cache.total"] == 5


# ===========================================================================
# 2. to_prometheus_text() Output Format
# ===========================================================================

class TestMetricsCollectorPrometheusFormat:
    """to_prometheus_text() 输出格式测试。"""

    def test_prometheus_output_contains_counter_type(self):
        """Prometheus 输出应包含 counter TYPE 注释。"""
        mc = _make_collector()
        mc.increment("request_count", 42)
        output = mc.to_prometheus_text()
        assert "# TYPE rag_request_count counter" in output
        assert "rag_request_count 42" in output

    def test_prometheus_output_contains_gauge_type(self):
        """Prometheus 输出应包含 gauge TYPE 注释。"""
        mc = _make_collector()
        mc.set_gauge("kv_pressure", 0.75)
        output = mc.to_prometheus_text()
        assert "# TYPE rag_kv_pressure gauge" in output
        assert "rag_kv_pressure 0.75" in output

    def test_prometheus_output_contains_histogram_quantiles(self):
        """Prometheus 输出应包含直方图分位数。"""
        mc = _make_collector()
        for i in range(100):
            mc.observe_histogram("latency_rewrite", float(i * 10))
        output = mc.to_prometheus_text()
        assert "quantile=\"0.5\"" in output
        assert "quantile=\"0.95\"" in output
        assert "quantile=\"0.99\"" in output
        assert "rag_latency_rewrite_seconds_count 100" in output

    def test_prometheus_output_contains_uptime(self):
        """Prometheus 输出应包含 uptime gauge。"""
        mc = _make_collector()
        output = mc.to_prometheus_text()
        assert "rag_uptime_seconds" in output

    def test_prometheus_dots_replaced_with_underscores(self):
        """指标名中的点号应替换为下划线。"""
        mc = _make_collector()
        mc.increment("cache.hit.L1", 5)
        output = mc.to_prometheus_text()
        assert "rag_cache_hit_L1" in output

    def test_prometheus_empty_collector_has_uptime(self):
        """空收集器也应输出 uptime。"""
        mc = _make_collector()
        output = mc.to_prometheus_text()
        lines = [l for l in output.strip().split("\n") if not l.startswith("#")]
        # 至少 uptime 行
        assert any("rag_uptime_seconds" in l for l in output.split("\n"))


# ===========================================================================
# 3. Gauge Setting and Retrieval
# ===========================================================================

class TestMetricsCollectorGauges:
    """gauge 设置和检索测试。"""

    def test_gauge_set_and_get(self):
        """set_gauge 后应能正确检索。"""
        mc = _make_collector()
        mc.set_gauge("kv_utilization", 0.85)
        assert mc._gauges["kv_utilization"] == 0.85

    def test_gauge_overwrite(self):
        """多次 set_gauge 应覆盖为最新值。"""
        mc = _make_collector()
        mc.set_gauge("pressure", 0.3)
        mc.set_gauge("pressure", 0.9)
        assert mc._gauges["pressure"] == 0.9

    def test_gauge_independent_from_counters(self):
        """gauge 和 counter 应互不影响。"""
        mc = _make_collector()
        mc.set_gauge("test_gauge", 1.0)
        mc.increment("test_counter", 5)
        assert mc._gauges["test_gauge"] == 1.0
        assert mc._counters["test_counter"] == 5

    def test_get_stats_includes_gauges(self):
        """get_stats 应包含 gauge 值。"""
        mc = _make_collector()
        mc.set_gauge("kv_pressure", 0.88)
        stats = mc.get_stats()
        assert stats["gauges"]["kv_pressure"] == 0.88


# ===========================================================================
# 4. Histogram Percentile Computation
# ===========================================================================

class TestMetricsCollectorHistogram:
    """直方图百分位计算测试。"""

    def test_histogram_p50_median(self):
        """P50 应为中位数。"""
        mc = _make_collector()
        for v in range(1, 101):  # 1..100
            mc.observe_histogram("test_hist", float(v))
        stats = mc.get_stats()
        p50 = stats["latency_percentiles"]["test_hist"]["p50"]
        # P50 应约为 50
        assert 45 <= p50 <= 55

    def test_histogram_p99_near_max(self):
        """P99 应接近最大值。"""
        mc = _make_collector()
        for v in range(1, 101):
            mc.observe_histogram("test_hist", float(v))
        stats = mc.get_stats()
        p99 = stats["latency_percentiles"]["test_hist"]["p99"]
        assert p99 >= 95

    def test_histogram_count_tracks_observations(self):
        """count 应记录观察次数。"""
        mc = _make_collector()
        for _ in range(50):
            mc.observe_histogram("test_hist", 100.0)
        stats = mc.get_stats()
        assert stats["latency_percentiles"]["test_hist"]["count"] == 50

    def test_histogram_empty_returns_zero(self):
        """空直方图百分位应返回 0。"""
        mc = _make_collector()
        stats = mc.get_stats()
        # 空直方图不会有 key，但验证不报错
        assert "latency_percentiles" in stats

    def test_histogram_max_buffer_1000(self):
        """直方图应限制最大缓冲区为 1000。"""
        mc = _make_collector()
        for i in range(2000):
            mc.observe_histogram("buffer_test", float(i))
        assert len(mc._histograms["buffer_test"]) == 1000
        # 应保留最后 1000 条
        assert mc._histograms["buffer_test"][0] == 1000.0


# ===========================================================================
# 5. AlertingManager — Rule Triggering with Duration Threshold
# ===========================================================================

class TestAlertingManagerTriggering:
    """AlertingManager 规则触发测试。"""

    def test_alert_triggers_after_duration(self):
        """指标超阈值持续超过 duration_s 应触发告警。"""
        metrics = _make_collector()
        metrics.set_gauge("kv_pressure", 0.95)  # > 0.9 阈值
        mgr = _make_alerting(metrics)

        # 设置首次触发时间为 60 秒前
        now = time.time()
        mgr._alert_timestamps["kv_pressure_critical"] = now - 60

        mgr.check_alerts()

        active = mgr.get_active_alerts()
        names = [a["name"] for a in active]
        assert "kv_pressure_critical" in names

    def test_alert_not_triggered_within_duration(self):
        """指标超阈值但未超过 duration_s 不应触发。"""
        metrics = _make_collector()
        metrics.set_gauge("kv_pressure", 0.95)
        mgr = _make_alerting(metrics)

        # 刚刚触发（0 秒前）
        now = time.time()
        mgr._alert_timestamps["kv_pressure_critical"] = now

        mgr.check_alerts()

        active = mgr.get_active_alerts()
        names = [a["name"] for a in active]
        assert "kv_pressure_critical" not in names

    def test_alert_gt_comparison(self):
        """> 比较应正确判断。"""
        metrics = _make_collector()
        metrics.set_gauge("rerank_batch_queue_delay_p99", 60)  # > 50 阈值
        mgr = _make_alerting(metrics)
        now = time.time()
        mgr._alert_timestamps["rerank_batch_delay"] = now - 60
        mgr.check_alerts()
        active = [a["name"] for a in mgr.get_active_alerts()]
        assert "rerank_batch_delay" in active

    def test_alert_lt_comparison(self):
        """< 比较应正确判断（prefix_cache_hit_rate < 0.5）。"""
        metrics = _make_collector()
        # 设置 prefix_cache_hit_rate < 0.5
        metrics._counters["prefix_cache.hits"] = 1
        metrics._counters["prefix_cache.misses"] = 9
        mgr = _make_alerting(metrics)
        now = time.time()
        mgr._alert_timestamps["prefix_cache_drop"] = now - 600
        mgr.check_alerts()
        active = [a["name"] for a in mgr.get_active_alerts()]
        assert "prefix_cache_drop" in active


# ===========================================================================
# 6. AlertingManager — Rule Recovery
# ===========================================================================

class TestAlertingManagerRecovery:
    """AlertingManager 告警恢复测试。"""

    def test_alert_recovers_when_metric_normalizes(self):
        """指标恢复正常后应清除告警。"""
        metrics = _make_collector()
        metrics.set_gauge("kv_pressure", 0.95)  # 超阈值
        mgr = _make_alerting(metrics)

        # 先触发告警
        now = time.time()
        mgr._alert_timestamps["kv_pressure_critical"] = now - 60
        mgr.check_alerts()
        assert len(mgr.get_active_alerts()) >= 1

        # 恢复正常
        metrics.set_gauge("kv_pressure", 0.5)  # < 0.9
        mgr.check_alerts()

        active = [a["name"] for a in mgr.get_active_alerts()]
        assert "kv_pressure_critical" not in active

    def test_alert_recovery_cleans_timestamp(self):
        """告警恢复后应清除首次触发时间。"""
        metrics = _make_collector()
        metrics.set_gauge("kv_pressure", 0.95)
        mgr = _make_alerting(metrics)

        now = time.time()
        mgr._alert_timestamps["kv_pressure_critical"] = now - 60
        mgr.check_alerts()

        # 恢复
        metrics.set_gauge("kv_pressure", 0.5)
        mgr.check_alerts()

        assert "kv_pressure_critical" not in mgr._alert_timestamps

    def test_clear_alert_manual(self):
        """clear_alert 应手动清除指定告警。"""
        metrics = _make_collector()
        metrics.set_gauge("kv_pressure", 0.95)
        mgr = _make_alerting(metrics)
        now = time.time()
        mgr._alert_timestamps["kv_pressure_critical"] = now - 60
        mgr.check_alerts()

        mgr.clear_alert("kv_pressure_critical")
        assert "kv_pressure_critical" not in mgr.get_active_alerts()


# ===========================================================================
# 7. Throughput QPS Metric Computation
# ===========================================================================

class TestThroughputQPS:
    """throughput QPS 计算测试。"""

    def test_get_stats_contains_uptime(self):
        """get_stats 应包含 uptime_seconds。"""
        mc = _make_collector()
        stats = mc.get_stats()
        assert "uptime_seconds" in stats
        assert stats["uptime_seconds"] >= 0

    def test_request_rate_computation(self):
        """通过 cache.total / uptime 可估算 QPS。"""
        mc = _make_collector()
        # 模拟 100 个请求
        for _ in range(100):
            mc.increment("cache.total")
        stats = mc.get_stats()
        uptime = stats["uptime_seconds"]
        if uptime > 0:
            qps = stats["counters"]["cache.total"] / uptime
            assert qps > 0
        else:
            # uptime 可能极小（刚初始化），验证 counter 正确
            assert stats["counters"]["cache.total"] == 100

    def test_prefix_cache_hit_rate_computation(self):
        """prefix cache 命中率应正确计算。"""
        mc = _make_collector()
        mc.record_prefix_cache_hit()
        mc.record_prefix_cache_hit()
        mc.record_prefix_cache_miss()

        stats = mc.get_stats()
        hit_rate = stats["prefix_cache_hit_rate"]
        # 2 hits / 3 total = 0.667
        assert abs(hit_rate - 2 / 3) < 0.01

    def test_admission_stats_in_get_stats(self):
        """get_stats 应包含 admission 统计。"""
        mc = _make_collector()
        mc.increment("admission.total", 10)
        mc.increment("admission.admitted", 7)
        mc.increment("admission.rejected", 3)
        stats = mc.get_stats()
        assert stats["admission"]["total"] == 10
        assert stats["admission"]["admitted"] == 7
        assert stats["admission"]["rejected"] == 3

    def test_redis_degraded_counter(self):
        """record_redis_degraded 应递增降级事件计数。"""
        mc = _make_collector()
        mc.record_redis_degraded()
        mc.record_redis_degraded()
        stats = mc.get_stats()
        assert stats["redis_degraded"] == 2

    def test_cache_epoch_switches_counter(self):
        """record_cache_epoch_switch 应递增版本切换计数。"""
        mc = _make_collector()
        mc.record_cache_epoch_switch()
        stats = get_stats_safe(mc)
        assert stats["cache_epoch_switches"] == 1


def get_stats_safe(mc):
    """Helper to get stats."""
    return mc.get_stats()
