"""
MetricsCollector -- 全链路指标收集

收集 readme 12 节要求的关键指标：
- L1/L2 命中率（按权限分区统计）
- Rewrite 延迟/成功率
- Evidence Gate 分数分布
- KV Pressure 实时值
- 降级触发次数
- Rerank Batch Aggregator 指标
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

sys_path_done = False
try:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys_path_done = True
except Exception:
    pass

with open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"),
    encoding="utf-8",
) as f:
    config = json.load(f)

logger = logging.getLogger(__name__)


class MetricsCollector:
    """
    指标收集器

    收集 readme 12 节要求的关键指标：
    - L1/L2 命中率（按权限分区统计）
    - Rewrite 延迟/成功率
    - Evidence Gate 分数分布
    - KV Pressure 实时值
    - 降级触发次数
    - Rerank Batch Aggregator 指标
    """

    def __init__(self):
        self._counters: Dict[str, int] = defaultdict(int)
        self._gauges: Dict[str, float] = {}
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        self._start_time = time.time()
        logger.info("MetricsCollector 初始化完成")

    def increment(self, name: str, value: int = 1):
        """递增计数器"""
        self._counters[name] += value

    def set_gauge(self, name: str, value: float):
        """设置仪表盘值"""
        self._gauges[name] = value

    def observe_histogram(self, name: str, value: float):
        """记录直方图值"""
        self._histograms[name].append(value)
        if len(self._histograms[name]) > 1000:
            self._histograms[name] = self._histograms[name][-1000:]

    def record_request(self, ctx):
        """
        记录单次请求的指标

        Args:
            ctx: RequestContext (dict or object with the expected attributes)
        """
        # 缓存命中率
        cache_hit_level = getattr(ctx, "cache_hit_level", None) or (
            ctx.get("cache_hit_level") if isinstance(ctx, dict) else None
        )
        if cache_hit_level:
            self.increment(f"cache.hit.{cache_hit_level}")
        self.increment("cache.total")

        # Rewrite 成功率
        rewrite_result = getattr(ctx, "rewrite_result", None) or (
            ctx.get("rewrite_result") if isinstance(ctx, dict) else None
        )
        if rewrite_result:
            self.increment("rewrite.success")
            fallback = getattr(rewrite_result, "fallback", None) or (
                rewrite_result.get("fallback")
                if isinstance(rewrite_result, dict)
                else None
            )
            if fallback:
                self.increment("rewrite.fallback")
        else:
            self.increment("rewrite.fail")

        # Evidence Gate 分数
        evidence_result = getattr(ctx, "evidence_result", None) or (
            ctx.get("evidence_result") if isinstance(ctx, dict) else None
        )
        if evidence_result:
            ev_score = getattr(evidence_result, "evidence_score", None) or (
                evidence_result.get("evidence_score")
                if isinstance(evidence_result, dict)
                else None
            )
            if ev_score is not None:
                self.observe_histogram("evidence.score", ev_score)
            decision = getattr(evidence_result, "decision", None) or (
                evidence_result.get("decision")
                if isinstance(evidence_result, dict)
                else None
            )
            if decision:
                self.increment(f"evidence.decision.{decision}")

        # 降级
        degraded = getattr(ctx, "degraded", None) or (
            ctx.get("degraded") if isinstance(ctx, dict) else None
        )
        if degraded:
            self.increment("degradation.total")

        # KV Pressure
        kv_pressure = getattr(ctx, "kv_pressure_at_entry", None) or (
            ctx.get("kv_pressure_at_entry") if isinstance(ctx, dict) else None
        )
        if kv_pressure and kv_pressure > 0:
            self.set_gauge("kv_pressure", kv_pressure)

        # 各阶段延迟
        stage_timings = getattr(ctx, "stage_timings", None) or (
            ctx.get("stage_timings") if isinstance(ctx, dict) else {}
        )
        if stage_timings:
            for stage, duration_ms in stage_timings.items():
                self.observe_histogram(f"latency.{stage}", duration_ms)

    def get_counter(self, name: str) -> int:
        return self._counters.get(name, 0)

    def get_gauge(self, name: str) -> float:
        return self._gauges.get(name, 0.0)

    @staticmethod
    def _percentile(values: List[float], p: float) -> float:
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        idx = int(len(sorted_vals) * p)
        return sorted_vals[min(idx, len(sorted_vals) - 1)]

    def get_stats(self) -> dict:
        """获取统计摘要"""
        total_req = max(self._counters.get("cache.total", 1), 1)
        rewrite_total = max(
            self._counters.get("rewrite.success", 0)
            + self._counters.get("rewrite.fail", 0),
            1,
        )

        stats = {
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "cache_hit_rate": {
                "L1": self._counters.get("cache.hit.L1", 0) / total_req,
                "L2": self._counters.get("cache.hit.L2", 0) / total_req,
            },
            "rewrite_fallback_rate": self._counters.get("rewrite.fallback", 0)
            / rewrite_total,
            "latency_percentiles": {},
        }

        for name, values in self._histograms.items():
            stats["latency_percentiles"][name] = {
                "p50": round(self._percentile(values, 0.5), 2),
                "p95": round(self._percentile(values, 0.95), 2),
                "p99": round(self._percentile(values, 0.99), 2),
                "count": len(values),
            }

        return stats
