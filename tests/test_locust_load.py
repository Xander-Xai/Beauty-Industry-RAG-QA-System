"""静态验证 Locust 压测脚本的导入和基本结构。"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# These tests inspect the locustfile's static data and helpers. Always stub
# Locust here so importing it cannot install gevent monkey patches during
# pytest collection; runtime load tests still use the real Locust executable.
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
        """LatencyStats 数据结构正确。

        LatencyStats 已移入 `benchmarks.performance`，与 performance artifact 共用
        同一实现，避免「未测量」语义在两处漂移。

        行为变更：空样本集的统计量返回 ``None`` 而非 ``0.0``。旧断言
        (``p50 == 0.0``) 把「没有测量」写成了「0 毫秒」，会让一次根本没跑起来
        的压测看起来又快又健康 —— 这正是 performance artifact 契约要禁止的。
        """
        from benchmarks.performance import LatencyStats

        stats = LatencyStats()
        assert stats.count == 0
        assert stats.p50 is None
        assert stats.p95 is None
        assert stats.p99 is None
        assert stats.avg is None
        assert stats.min is None
        assert stats.max is None
        assert stats.to_dict() == {"count": 0}

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
