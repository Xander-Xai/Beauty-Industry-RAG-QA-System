"""静态验证 Locust 压测脚本的导入和基本结构。"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Mock locust module for environments where locust is not installed ──
try:
    import locust  # noqa: F401
except ImportError:
    _locust_mod = types.ModuleType("locust")
    _locust_mod.HttpUser = type("HttpUser", (), {})
    _locust_mod.between = lambda a, b: None
    _locust_mod.events = types.ModuleType("locust.events")
    _locust_mod.events.request = type("EventHook", (), {"add_listener": lambda *a, **kw: None})()
    _locust_mod.events.quit = type("EventHook", (), {"add_listener": lambda *a, **kw: None})()
    _locust_mod.task = lambda *a, **kw: (lambda f: f) if a and callable(a[0]) else (lambda f: None)
    _locust_mod.__file__ = "<mock>"
    sys.modules["locust"] = _locust_mod


@pytest.mark.unit
class TestLocustScriptStructure:
    """验证 locustfile.py 的导入和基本结构。"""

    def test_locust_compiles(self):
        """locustfile.py 应能成功编译（语法正确）。"""
        with open("tests/load/locustfile.py", encoding="utf-8") as f:
            code = f.read()
        compile(code, "tests/load/locustfile.py", "exec")  # should not raise

    def test_queries_non_empty(self):
        """测试查询列表应非空。"""
        from tests.load.locustfile import (
            ALL_QUERIES,
            QUERIES_GENERAL,
            QUERIES_INGREDIENT,
            QUERIES_REGULATION,
        )

        assert len(QUERIES_REGULATION) > 0
        assert len(QUERIES_INGREDIENT) > 0
        assert len(QUERIES_GENERAL) > 0
        assert len(ALL_QUERIES) == (len(QUERIES_REGULATION) + len(QUERIES_INGREDIENT) + len(QUERIES_GENERAL))

    def test_latency_stats(self):
        """LatencyStats 数据结构正确。"""
        from tests.load.locustfile import LatencyStats

        stats = LatencyStats()
        assert stats.p50 == 0.0
        assert stats.p95 == 0.0

        stats.add(100)
        stats.add(200)
        stats.add(300)
        assert stats.count == 3
        assert stats.avg == 200.0
        assert stats.p50 == 200
        assert stats.p95 == 300
        assert stats.p99 == 300
        assert stats.min == 100
        assert stats.max == 300

    def test_report_dir(self):
        """REPORT_DIR 应指向 reports/benchmark/。"""
        from tests.load.locustfile import REPORT_DIR

        assert "reports" in str(REPORT_DIR)
        assert "benchmark" in str(REPORT_DIR)
