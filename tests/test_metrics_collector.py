"""MetricsCollector 测试 — 计数器、仪表盘、直方图、请求记录。"""

import os
import sys
import types

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


class TestMetricsCollector:
    """测试 MetricsCollector 核心指标收集功能。"""

    def _make_collector(self):
        from monitoring.otel_tracer import MetricsCollector

        return MetricsCollector()

    def test_increment_counter(self):
        """计数器应正确递增。"""
        mc = self._make_collector()
        mc.increment("test_counter")
        mc.increment("test_counter")
        mc.increment("test_counter", value=5)
        assert mc._counters["test_counter"] == 7

    def test_set_gauge(self):
        """仪表盘应正确设置值。"""
        mc = self._make_collector()
        mc.set_gauge("kv_pressure", 0.75)
        assert mc._gauges["kv_pressure"] == 0.75
        mc.set_gauge("kv_pressure", 0.85)
        assert mc._gauges["kv_pressure"] == 0.85

    def test_observe_histogram(self):
        """直方图应记录值并限制最大长度。"""
        mc = self._make_collector()
        for i in range(5):
            mc.observe_histogram("latency", float(i * 100))
        assert len(mc._histograms["latency"]) == 5
        assert mc._histograms["latency"][0] == 0.0
        assert mc._histograms["latency"][-1] == 400.0

    def test_histogram_max_length(self):
        """直方图应限制最大记录数为 1000。"""
        mc = self._make_collector()
        for i in range(1200):
            mc.observe_histogram("test_hist", float(i))
        assert len(mc._histograms["test_hist"]) == 1000

    def test_record_request_from_ctx(self):
        """record_request 应从 RequestContext 中提取指标。"""
        mc = self._make_collector()
        from core.pipeline_context import (
            EvidenceGateResult,
            QueryRewriteResult,
            RequestContext,
        )

        ctx = RequestContext(
            user_input="测试查询",
            session_id="test",
            user_id="test_user",
        )
        ctx.rewrite_result = QueryRewriteResult(
            rewritten_query="测试查询",
            requires_context=True,
            business_type="development",
            intent="ingredient",
        )
        ctx.evidence_result = EvidenceGateResult(
            evidence_score=0.85,
            ce_top1_score=0.9,
            ce_top3_mean_score=0.85,
            retrieval_agreement_score=0.8,
            doc_consistency_score=0.9,
            decision="pass",
        )
        ctx.cache_hit_level = "L1"
        ctx.total_latency_ms = 1500.0
        ctx.record_timing("rewrite", 50.0)
        ctx.record_timing("recall", 120.0)

        mc.record_request(ctx)
        # 应记录了请求相关的计数器
        total_counters = sum(mc._counters.values())
        assert total_counters > 0, f"record_request 应记录计数器，当前: {dict(mc._counters)}"

    def test_get_prometheus_format(self):
        """to_prometheus_text 应返回 Prometheus 格式字符串。"""
        mc = self._make_collector()
        mc.increment("test_requests", 42)
        mc.set_gauge("test_pressure", 0.75)
        mc.observe_histogram("test_latency", 100.0)

        output = mc.to_prometheus_text()
        assert isinstance(output, str)
        # Prometheus 格式包含 TYPE 注释
        assert "test_requests" in output or "test_pressure" in output

    def test_record_cache_hit(self):
        """缓存命中应正确计数。"""
        mc = self._make_collector()
        mc.increment("cache.l1.hit")
        mc.increment("cache.l2.hit")
        mc.increment("cache.l1.hit")
        assert mc._counters["cache.l1.hit"] == 2
        assert mc._counters["cache.l2.hit"] == 1

    def test_record_admission_rejected(self):
        """准入拒绝应正确计数。"""
        mc = self._make_collector()
        mc.increment("admission.rejected")
        mc.increment("admission.rejected")
        assert mc._counters["admission.rejected"] == 2
