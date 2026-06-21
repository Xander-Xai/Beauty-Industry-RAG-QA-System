"""
OpenTelemetry 全链路追踪（readme 12 节）

对应 readme 要求：
- 全链路追踪（OpenTelemetry + Jaeger）
- 关键监控指标：L1/L2 命中率、Rewrite 延迟、Evidence Gate 分数、KV Cache 占用率等

实现层级：
- OpenTelemetryTracer: 本地 span 追踪（可选接入 OTel SDK + Jaeger）
- MetricsCollector: 全链路指标收集
- AlertingManager: 告警规则引擎
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from contextlib import contextmanager

from common.config import get_config_dict

config = get_config_dict()

logger = logging.getLogger(__name__)


class OpenTelemetryTracer:
    """
    全链路追踪器

    当前实现：本地内存 span 追踪器
    可选升级：接入 OpenTelemetry SDK + Jaeger 导出器

    用法：
        tracer = OpenTelemetryTracer()
        with tracer.trace("rewrite", {"query": "..."}):
            result = rewriter.rewrite(query)
        # span 自动记录耗时和状态
    """

    def __init__(self):
        import threading
        self._spans = []
        self._max_spans = 1000
        self._thread_lock = threading.Lock()
        self._use_otel = False
        # 吞吐量 QPS 追踪（PRD §5.2.6）
        self._request_count = 0
        self._throughput_start_time = time.time()
        self._try_init_otel()
        logger.info(f"OpenTelemetryTracer 初始化完成 ({'OTel' if self._use_otel else '本地模式'})")

    def _try_init_otel(self):
        """尝试初始化 OpenTelemetry + Jaeger 导出器（PRD §12）"""
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider()

            # Jaeger 导出器（PRD §12: 全链路追踪 OpenTelemetry + Jaeger）
            jaeger_config = config.get("monitoring", {}).get("jaeger", {})
            if jaeger_config.get("enabled", False):
                try:
                    from opentelemetry.exporter.jaeger.thrift import JaegerExporter
                    jaeger_exporter = JaegerExporter(
                        agent_host_name=jaeger_config.get("agent_host", "localhost"),
                        agent_port=jaeger_config.get("agent_port", 6831),
                    )
                    provider.add_span_processor(BatchSpanProcessor(jaeger_exporter))
                    logger.info("Jaeger 导出器已启用")
                except ImportError:
                    logger.warning("opentelemetry-exporter-jaeger 未安装，跳过 Jaeger 导出")
                except Exception as e:
                    logger.warning(f"Jaeger 导出器初始化失败: {e}")

            trace.set_tracer_provider(provider)
            self._otel_tracer = trace.get_tracer("rag-system")
            self._use_otel = True
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"OTel 初始化失败，使用本地模式: {e}")

    @contextmanager
    def trace(self, span_name: str, attributes: dict = None):
        """
        追踪代码块

        Usage:
            with tracer.trace("rewrite", {"query": "..."}):
                result = rewriter.rewrite(query)
        """
        if self._use_otel:
            yield from self._trace_with_otel(span_name, attributes or {})
        else:
            yield from self._trace_local(span_name, attributes or {})

    def _trace_local(self, span_name: str, attributes: dict):
        """本地内存追踪"""
        span = {
            "name": span_name,
            "start_time": time.time(),
            "attributes": attributes,
            "status": "OK",
        }
        try:
            yield span
        except Exception as e:
            span["status"] = "ERROR"
            span["error"] = str(e)
            raise
        finally:
            span["end_time"] = time.time()
            span["duration_ms"] = (span["end_time"] - span["start_time"]) * 1000
            with self._lock():
                self._spans.append(span)
                if len(self._spans) > self._max_spans:
                    self._spans = self._spans[-self._max_spans:]

    def _trace_with_otel(self, span_name: str, attributes: dict):
        """OpenTelemetry 追踪（防御性实现）"""
        span = {
            "name": span_name,
            "start_time": time.time(),
            "attributes": attributes,
            "status": "OK",
        }
        otel_span = None
        try:
            ctx_mgr = self._otel_tracer.start_as_current_span(span_name)
            otel_span = ctx_mgr.__enter__()
            # 防御性设置属性（OTel span 可能不支持 set_attribute）
            if otel_span and hasattr(otel_span, "set_attribute"):
                for k, v in attributes.items():
                    otel_span.set_attribute(k, str(v))
        except Exception:
            pass  # OTel 不可用时静默降级

        try:
            yield span
        except Exception as e:
            span["status"] = "ERROR"
            span["error"] = str(e)
            if otel_span and hasattr(otel_span, "set_status"):
                try:
                    otel_span.set_status({"status_code": "ERROR", "description": str(e)})
                except Exception:
                    pass
            raise
        finally:
            span["end_time"] = time.time()
            span["duration_ms"] = (span["end_time"] - span["start_time"]) * 1000
            # 安全退出 OTel span
            try:
                if otel_span is not None:
                    ctx_mgr.__exit__(None, None, None)
            except Exception:
                pass

    def _lock(self):
        """简单线程安全"""
        return self._thread_lock

    def get_trace_summary(self) -> list[dict]:
        """获取追踪摘要"""
        with self._lock():
            return self._spans[-100:]  # 保留最近 100 条


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
        self._counters = defaultdict(int)
        self._gauges = defaultdict(float)
        self._histograms = defaultdict(list)
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
            ctx: RequestContext
        """
        # 缓存命中率
        if ctx.cache_hit_level:
            self.increment(f"cache.hit.{ctx.cache_hit_level}")
        self.increment("cache.total")

        # Rewrite 成功率
        if ctx.rewrite_result:
            self.increment("rewrite.success")
            if ctx.rewrite_result.fallback:
                self.increment("rewrite.fallback")
        else:
            self.increment("rewrite.fail")

        # Evidence Gate 分数
        if ctx.evidence_result:
            self.observe_histogram("evidence.score", ctx.evidence_result.evidence_score)
            self.increment(f"evidence.decision.{ctx.evidence_result.decision}")

        # 降级
        if ctx.degraded:
            self.increment("degradation.total")

        # BLIP 触发统计
        if hasattr(ctx, 'blip_triggered') and ctx.blip_triggered:
            self.increment("blip.triggered")
        self.increment("blip.total")

        # KV Pressure
        if ctx.kv_pressure_at_entry > 0:
            self.set_gauge("kv_pressure", ctx.kv_pressure_at_entry)

        # PRD §12: NLI 矛盾比例（Answer Gate）
        if hasattr(ctx, 'answer_gate_result') and ctx.answer_gate_result:
            nli_score = ctx.answer_gate_result.nli_contradiction_score
            self.observe_histogram("nli.contradiction_score", nli_score)
            if nli_score > 0.5:
                self.increment("nli.contradiction_high")

        # PRD §12: Admission Control 拒绝/排队计数
        if hasattr(ctx, 'kv_pressure_at_entry'):
            self.increment("admission.total")
            # 在 pipeline 中 admission_reason 已记录到 audit log，此处统计通过/拒绝
            if hasattr(ctx, '_admission_admitted'):
                if ctx._admission_admitted:
                    self.increment("admission.admitted")
                else:
                    self.increment("admission.rejected")

        # 各阶段延迟
        for stage, duration_ms in ctx.stage_timings.items():
            self.observe_histogram(f"latency.{stage}", duration_ms)

    def record_prefix_cache_hit(self):
        """PRD §12: Prefix Cache 命中"""
        self.increment("prefix_cache.hit")

    def record_prefix_cache_miss(self):
        """PRD §12: Prefix Cache 未命中"""
        self.increment("prefix_cache.miss")

    def record_cache_epoch_switch(self):
        """PRD §12: 缓存版本切换次数"""
        self.increment("cache.epoch_switches")

    def record_redis_degraded(self):
        """PRD §12: Redis 降级模式"""
        self.increment("redis.degraded_events")

    def get_stats(self) -> dict:
        """获取统计摘要"""
        def _percentile(values, p):
            if not values:
                return 0.0
            sorted_vals = sorted(values)
            idx = int(len(sorted_vals) * p)
            return sorted_vals[min(idx, len(sorted_vals) - 1)]

        stats = {
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "cache_hit_rate": {
                "L1": self._counters.get("cache.hit.L1", 0) / max(self._counters.get("cache.total", 1), 1),
                "L2": self._counters.get("cache.hit.L2", 0) / max(self._counters.get("cache.total", 1), 1),
                "L2_SESSION": self._counters.get("cache.hit.L2_SESSION", 0) / max(self._counters.get("cache.total", 1), 1),
            },
            "rewrite_fallback_rate": self._counters.get("rewrite.fallback", 0) / max(
                self._counters.get("rewrite.success", 0) + self._counters.get("rewrite.fail", 0), 1
            ),
            # PRD §12: NLI 矛盾比例
            "nli_contradiction_rate": self._counters.get("nli.contradiction_high", 0) / max(
                self._counters.get("answer_gate.total", 1), 1
            ),
            # PRD §12: Prefix Caching 命中率
            "prefix_cache_hit_rate": self._counters.get("prefix_cache.hit", 0) / max(
                self._counters.get("prefix_cache.hit", 0) + self._counters.get("prefix_cache.miss", 0), 1
            ),
            # PRD §12: Redis 降级状态
            "redis_degraded": self._counters.get("redis.degraded_events", 0),
            # PRD §12: 缓存版本切换次数
            "cache_epoch_switches": self._counters.get("cache.epoch_switches", 0),
            # PRD §12: Admission 拒绝/排队计数
            "admission": {
                "total": self._counters.get("admission.total", 0),
                "admitted": self._counters.get("admission.admitted", 0),
                "rejected": self._counters.get("admission.rejected", 0),
            },
            "latency_percentiles": {},
        }

        for name, values in self._histograms.items():
            stats["latency_percentiles"][name] = {
                "p50": round(_percentile(values, 0.5), 2),
                "p95": round(_percentile(values, 0.95), 2),
                "p99": round(_percentile(values, 0.99), 2),
                "count": len(values),
            }

        return stats

    def to_prometheus_text(self) -> str:
        """
        导出 Prometheus 文本格式指标（PRD §12）

        支持 counter / gauge / histogram 类型。
        用于 /metrics 端点暴露给 Prometheus 拉取。
        """
        lines = []

        # Counters
        for name, value in self._counters.items():
            safe_name = name.replace(".", "_").replace("-", "_")
            lines.append(f"# TYPE rag_{safe_name} counter")
            lines.append(f"rag_{safe_name} {value}")

        # Gauges
        for name, value in self._gauges.items():
            safe_name = name.replace(".", "_").replace("-", "_")
            lines.append(f"# TYPE rag_{safe_name} gauge")
            lines.append(f"rag_{safe_name} {value}")

        # Histograms → summary (simplified: expose p50/p95/p99 + count)
        for name, values in self._histograms.items():
            if not values:
                continue
            safe_name = name.replace(".", "_").replace("-", "_")
            sorted_v = sorted(values)
            n = len(sorted_v)

            def _pct(p):
                idx = int(n * p)
                return sorted_v[min(idx, n - 1)]

            lines.append(f"# TYPE rag_{safe_name}_seconds summary")
            lines.append(f'rag_{safe_name}_seconds{{quantile="0.5"}} {_pct(0.5) / 1000:.6f}')
            lines.append(f'rag_{safe_name}_seconds{{quantile="0.95"}} {_pct(0.95) / 1000:.6f}')
            lines.append(f'rag_{safe_name}_seconds{{quantile="0.99"}} {_pct(0.99) / 1000:.6f}')
            lines.append(f"rag_{safe_name}_seconds_count {n}")

        # Uptime
        lines.append("# TYPE rag_uptime_seconds gauge")
        lines.append(f"rag_uptime_seconds {round(time.time() - self._start_time, 1)}")

        return "\n".join(lines) + "\n"


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
        self._active_alerts = {}
        self._alert_timestamps = {}  # 记录告警首次触发时间
        logger.info(f"AlertingManager 初始化完成 ({len(self._alert_rules)} 条规则)")

    def _load_alert_rules(self) -> list[dict]:
        """
        从 config.json 加载告警规则

        config.json 中的配置格式：
        "alerting": {
            "rules": [
                {"name": "...", "metric": "...", "threshold": 0.9, "duration_s": 30, "severity": "critical"}
            ]
        }
        """
        # 默认规则（PRD §12 完整告警清单）
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
            {"name": "blip_trigger_rate_high", "metric": "blip_trigger_rate", "threshold": 0.10,
             "duration_s": 300, "severity": "warning", "comparison": "gt"},
            # PRD §12 新增告警规则
            {"name": "prefix_cache_drop", "metric": "prefix_cache_hit_rate", "threshold": 0.5,
             "duration_s": 300, "severity": "warning", "comparison": "lt"},
            {"name": "redis_degraded_long", "metric": "redis.degraded_events", "threshold": 5,
             "duration_s": 300, "severity": "warning", "comparison": "gt"},
            {"name": "l1_hit_rate_drop", "metric": "cache_hit_rate_L1", "threshold": 0.3,
             "duration_s": 300, "severity": "warning", "comparison": "lt"},
            {"name": "l2_hit_rate_drop", "metric": "cache_hit_rate_L2", "threshold": 0.3,
             "duration_s": 300, "severity": "warning", "comparison": "lt"},
            {"name": "cache_failure_spike", "metric": "cache.failure_rate", "threshold": 0.1,
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

    def check_alerts(self):
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
                    logger.info(f"告警恢复: {rule_name} (metric={metric_name}, value={current_value:.4f})")
                    del self._active_alerts[rule_name]
                if rule_name in self._alert_timestamps:
                    del self._alert_timestamps[rule_name]

    def _get_metric_value(self, metric_name: str) -> float | None:
        """从 MetricsCollector 获取指标值"""
        # 优先从 gauges 读取
        if metric_name in self.metrics._gauges:
            return self.metrics._gauges[metric_name]

        # 从 counters 读取（用于速率类指标）
        if metric_name in self.metrics._counters:
            return float(self.metrics._counters[metric_name])

        # 特殊计算：fallback_rate
        if metric_name == "rewrite_fallback_rate":
            total = self.metrics._counters.get("rewrite.success", 0) + \
                    self.metrics._counters.get("rewrite.fail", 0)
            if total > 0:
                return self.metrics._counters.get("rewrite.fallback", 0) / total
            return 0.0

        # 特殊计算：cache_hit_rate
        if metric_name == "cache_hit_rate_L1":
            total = self.metrics._counters.get("cache.total", 0)
            if total > 0:
                return self.metrics._counters.get("cache.hit.L1", 0) / total
            return 0.0

        if metric_name == "cache_hit_rate_L2":
            total = self.metrics._counters.get("cache.total", 0)
            if total > 0:
                return self.metrics._counters.get("cache.hit.L2", 0) / total
            return 0.0

        # PRD §12: Prefix Caching 命中率
        if metric_name == "prefix_cache_hit_rate":
            hits = self.metrics._counters.get("prefix_cache.hits", 0)
            misses = self.metrics._counters.get("prefix_cache.misses", 0)
            total = hits + misses
            return hits / total if total > 0 else 1.0

        # PRD §12: BLIP 触发率
        if metric_name == "blip_trigger_rate":
            triggered = self.metrics._counters.get("blip.triggered", 0)
            total = self.metrics._counters.get("blip.total", 0)
            return triggered / total if total > 0 else 0.0

        # PRD §12: Cache 失败率
        if metric_name == "cache.failure_rate":
            # 从 Redis 降级事件估算缓存失败率
            degraded = self.metrics._counters.get("redis.degraded_events", 0)
            total = self.metrics._counters.get("cache.total", 1)
            return degraded / total if total > 0 else 0.0

        # 从直方图取 p99
        if metric_name in self.metrics._histograms:
            values = self.metrics._histograms[metric_name]
            if values:
                sorted_v = sorted(values)
                idx = int(len(sorted_v) * 0.99)
                return sorted_v[min(idx, len(sorted_v) - 1)]

        return None

    def get_active_alerts(self) -> list[dict]:
        """获取当前活跃告警"""
        return list(self._active_alerts.values())

    def clear_alert(self, rule_name: str):
        """手动清除告警"""
        self._active_alerts.pop(rule_name, None)
        self._alert_timestamps.pop(rule_name, None)
