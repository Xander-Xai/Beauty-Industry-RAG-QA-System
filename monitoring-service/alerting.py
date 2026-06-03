"""
AlertingManager -- 告警规则引擎

支持从 config.json 读取告警规则，实时检测阈值并生成告警。

默认告警规则：
- KV Pressure > 0.9 持续 30s
- KV Cache > 85%
- Rerank Batch 延迟 > 50ms
- BLIP 触发率 > 10%
- L1/L2 命中率突降 > 30%
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

sys_path_done = False
try:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys_path_done = True
except Exception:
    pass

from metrics_collector import MetricsCollector

with open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"),
    encoding="utf-8",
) as f:
    config = json.load(f)

logger = logging.getLogger(__name__)


class AlertingManager:
    """
    告警管理器（readme 12 节）

    支持从 config.json 读取告警规则，实时检测阈值并生成告警。

    默认告警规则：
    - KV Pressure > 0.9 持续 30s
    - KV Cache > 85%
    - Rerank Batch 延迟 > 50ms
    - BLIP 触发率 > 10%
    - L1/L2 命中率突降 > 30%
    """

    def __init__(self, metrics: MetricsCollector):
        self.metrics = metrics
        self._alert_rules = self._load_alert_rules()
        self._active_alerts: Dict[str, Dict[str, Any]] = {}
        self._alert_timestamps: Dict[str, float] = {}  # 记录告警首次触发时间
        logger.info(f"AlertingManager 初始化完成 ({len(self._alert_rules)} 条规则)")

    def _load_alert_rules(self) -> list:
        """
        从 config.json 加载告警规则

        config.json 中的配置格式：
        "alerting": {
            "rules": [
                {"name": "...", "metric": "...", "threshold": 0.9, "duration_s": 30, "severity": "critical"}
            ]
        }
        """
        # 默认规则
        default_rules = [
            {"name": "kv_pressure_critical", "metric": "kv_pressure", "threshold": 0.9,
             "duration_s": 30, "severity": "critical", "comparison": "gt"},
            {"name": "kv_cache_high", "metric": "kv_utilization", "threshold": 0.85,
             "duration_s": 60, "severity": "warning", "comparison": "gt"},
            {"name": "rerank_batch_delay", "metric": "rerank_batch_queue_delay_p99", "threshold": 50,
             "duration_s": 30, "severity": "warning", "comparison": "gt"},
            {"name": "rewrite_fallback_high", "metric": "rewrite_fallback_rate", "threshold": 0.3,
             "duration_s": 300, "severity": "warning", "comparison": "gt"},
            {"name": "degradation_spike", "metric": "degradation.total", "threshold": 10,
             "duration_s": 300, "severity": "critical", "comparison": "gt"},
        ]

        # 从 config 加载自定义规则（覆盖默认）
        custom_rules = config.get("alerting", {}).get("rules", [])
        if custom_rules:
            # 合并：自定义规则覆盖同名默认规则
            rule_map = {r["name"]: r for r in default_rules}
            for r in custom_rules:
                rule_map[r["name"]] = r
            return list(rule_map.values())

        return default_rules

    def check_alerts(self) -> List[Dict[str, Any]]:
        """
        检查所有告警条件

        逻辑：
        1. 遍历告警规则
        2. 从 MetricsCollector 获取当前值
        3. 与阈值比较
        4. 持续时间超过 duration_s 则触发告警
        5. 条件恢复则清除告警
        """
        now = time.time()

        for rule in self._alert_rules:
            rule_name = rule["name"]
            metric_name = rule["metric"]
            threshold = rule["threshold"]
            duration_s = rule["duration_s"]
            comparison = rule.get("comparison", "gt")
            severity = rule.get("severity", "warning")

            # 获取当前指标值
            current_value = self._get_metric_value(metric_name)
            if current_value is None:
                continue

            # 判断是否超阈值
            violated = False
            if comparison == "gt":
                violated = current_value > threshold
            elif comparison == "lt":
                violated = current_value < threshold
            elif comparison == "eq":
                violated = abs(current_value - threshold) < 0.001

            if violated:
                # 记录首次触发时间
                if rule_name not in self._alert_timestamps:
                    self._alert_timestamps[rule_name] = now

                # 持续时间检查
                elapsed = now - self._alert_timestamps[rule_name]
                if elapsed >= duration_s:
                    self._active_alerts[rule_name] = {
                        "name": rule_name,
                        "severity": severity,
                        "metric": metric_name,
                        "current_value": round(current_value, 4),
                        "threshold": threshold,
                        "triggered_at": self._alert_timestamps[rule_name],
                        "duration_s": round(elapsed, 1),
                        "message": f"{rule_name}: {metric_name}={current_value:.4f} "
                                   f"{'>' if comparison == 'gt' else '<'} {threshold} "
                                   f"持续 {elapsed:.0f}s",
                    }
                    logger.warning(f"告警触发: {self._active_alerts[rule_name]['message']}")
            else:
                # 条件恢复，清除告警
                if rule_name in self._active_alerts:
                    logger.info(
                        f"告警恢复: {rule_name} "
                        f"(metric={metric_name}, value={current_value:.4f})"
                    )
                    del self._active_alerts[rule_name]
                if rule_name in self._alert_timestamps:
                    del self._alert_timestamps[rule_name]

        return self.get_active_alerts()

    def _get_metric_value(self, metric_name: str) -> Optional[float]:
        """从 MetricsCollector 获取指标值"""
        # 优先从 gauges 读取
        if metric_name in self.metrics._gauges:
            return self.metrics._gauges[metric_name]

        # 从 counters 读取（用于速率类指标）
        if metric_name in self.metrics._counters:
            return float(self.metrics._counters[metric_name])

        # 特殊计算：fallback_rate
        if metric_name == "rewrite_fallback_rate":
            total = (
                self.metrics._counters.get("rewrite.success", 0)
                + self.metrics._counters.get("rewrite.fail", 0)
            )
            if total > 0:
                return self.metrics._counters.get("rewrite.fallback", 0) / total
            return 0.0

        # 特殊计算：cache_hit_rate
        if metric_name == "cache_hit_rate_L1":
            total = self.metrics._counters.get("cache.total", 0)
            if total > 0:
                return self.metrics._counters.get("cache.hit.L1", 0) / total
            return 0.0

        # 从直方图取 p99
        if metric_name in self.metrics._histograms:
            values = self.metrics._histograms[metric_name]
            if values:
                sorted_v = sorted(values)
                idx = int(len(sorted_v) * 0.99)
                return sorted_v[min(idx, len(sorted_v) - 1)]

        return None

    def get_active_alerts(self) -> List[Dict[str, Any]]:
        """获取当前活跃告警"""
        return list(self._active_alerts.values())

    def clear_alert(self, rule_name: str):
        """手动清除告警"""
        self._active_alerts.pop(rule_name, None)
        self._alert_timestamps.pop(rule_name, None)
