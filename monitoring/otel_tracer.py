"""
OpenTelemetry 全链路追踪 + 全链路指标收集

当前实现状态（务必与 `docs/repository-truth-audit.md` 保持一致）：

- **追踪钩子**：已接入在线主链路（`core/pipeline.py` → `OpenTelemetryTracer`）。
- **OTLP 导出器**：已实现且有确定性测试覆盖（`monitoring/otel_exporter.py`），
  通过 `OTEL_EXPORT_ENABLED=true` + `OTEL_EXPORTER_OTLP_ENDPOINT` 显式开启。
- **默认状态**：**默认关闭**。默认安装不含 exporter 包（`requirements-otel.txt`
  为可选依赖），未设置环境变量时不构造任何 exporter，span 既不导出也不保留。
- **闭环证据**：`PENDING`。应用 → exporter → collector → 后端 → 实际查到 span
  这一整条链路在本仓库没有留下任何运行期证据，因此**不得**表述为
  "OTel/Jaeger 导出已闭环" 或 "tracing validated"。Jaeger 也不是默认 exporter：
  `config.json` → `monitoring.jaeger.*` 是历史 thrift agent 路径，OTel SDK 已不再
  附带 jaeger exporter，当前 OTLP 路径由 `monitoring/otel_exporter.py` 负责。

实现层级：
- OpenTelemetryTracer: span 追踪（OTel SDK provider，或 SDK 缺失/初始化失败时的本地内存模式）
- MetricsCollector: 全链路指标收集，`/api/metrics` 实际暴露的 `rag_*` series 来源
- AlertingManager: **遗留**进程内阈值引擎，未接入 canonical 请求路径。
  正式告警契约是 `monitoring/prometheus/alerts.yml`（外部 Prometheus 规则）。
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from contextlib import contextmanager

from common.config import get_config_dict
from monitoring.otel_exporter import (
    EXPORTER_ENABLED_METRIC,
    ExporterState,
    build_resource,
    build_span_processor,
    load_config,
    sanitize_attributes,
)

config = get_config_dict()

logger = logging.getLogger(__name__)


class OpenTelemetryTracer:
    """
    全链路追踪器

    当前实现：走 OpenTelemetry SDK `TracerProvider`。span 导出是**可选**的，由
    `monitoring/otel_exporter.py` 在 `OTEL_EXPORT_ENABLED=true` 时挂载
    `BatchSpanProcessor`；默认不挂载，因此 span 既不导出也不保留。只有 OTel SDK
    未安装或 provider 初始化失败时才退回本地内存 span 模式。

    导出器状态可通过 `exporter_state` / `exporter_detail` 读取，并由
    `MetricsCollector.set_exporter_state` 暴露为 `rag_otel_exporter_enabled`，
    便于运维在不读日志的情况下确认 span 是否真的离开了进程。

    注意：exporter **实现存在** 与 **导出闭环已验证** 是两件事。后者在本仓库
    仍是 `PENDING`，详见 `docs/slo-runbook.md#tracelookup`。

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
        """Initialize the OTel SDK tracer provider, with optional span export.

        Export is opt-in via ``OTEL_EXPORT_ENABLED`` and is handled by
        :mod:`monitoring.otel_exporter`. Every failure there is non-fatal: the
        provider still works, spans simply stay local, and the outcome is
        reported through ``otel_exporter_state`` plus a warning log. An
        unreachable collector must never fail a query.
        """
        self.exporter_state = ExporterState.DISABLED
        self.exporter_detail = ""
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider

            exporter_config = load_config()
            resource = build_resource(exporter_config)
            provider = TracerProvider(resource=resource) if resource is not None else TracerProvider()

            processor, state, detail = build_span_processor(exporter_config)
            self.exporter_state = state
            self.exporter_detail = detail
            if processor is not None:
                provider.add_span_processor(processor)
                logger.info(
                    "OTLP span export enabled: %s",
                    json.dumps(exporter_config.describe(), ensure_ascii=False),
                )
            elif state != ExporterState.DISABLED:
                # Export was requested but is not available. This is a warning,
                # not an error: the business path must keep working.
                logger.warning("OTLP span export requested but unavailable: %s", detail)

            trace.set_tracer_provider(provider)
            self._otel_tracer = trace.get_tracer("rag-system")
            self._use_otel = True
        except ImportError:
            self._use_otel = False
        except Exception as e:
            self._use_otel = False
            self.exporter_state = ExporterState.FAILED
            self.exporter_detail = f"{type(e).__name__}: {e}"
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
                    self._spans = self._spans[-self._max_spans :]

    def _trace_with_otel(self, span_name: str, attributes: dict):
        """OpenTelemetry 追踪（防御性实现）"""
        # Attributes are reduced to an allow-list before touching the SDK, so a
        # raw query or token cannot reach a trace backend even when the caller
        # passes one.
        attributes = sanitize_attributes(attributes)
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
        except Exception as exc:
            logger.debug("OpenTelemetry span setup failed; continuing with local span: %s", exc)

        try:
            yield span
        except Exception as e:
            span["status"] = "ERROR"
            span["error"] = str(e)
            if otel_span and hasattr(otel_span, "set_status"):
                try:
                    otel_span.set_status({"status_code": "ERROR", "description": str(e)})
                except Exception as exc:
                    logger.debug("Could not mark OpenTelemetry span as failed: %s", exc)
            raise
        finally:
            span["end_time"] = time.time()
            span["duration_ms"] = (span["end_time"] - span["start_time"]) * 1000
            # 安全退出 OTel span
            try:
                if otel_span is not None:
                    ctx_mgr.__exit__(None, None, None)
            except Exception as exc:
                logger.debug("Could not close OpenTelemetry span: %s", exc)

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
        # Redis 降级状态：0=正常，1=降级到进程内内存。由真实降级路径设置，
        # 不是默认常量——没有真实降级发生时它保持 0，告警才不会误报。
        self._gauges["redis_degraded_mode"] = 0.0
        logger.info("MetricsCollector 初始化完成")

    def record_http_request(self, status_code: int, duration_ms: float) -> None:
        """记录一次 HTTP 请求。

        与 :meth:`record_request` 的区别：后者只在一次完整 RAG 查询成功后调用，
        因此在 LLM / Qdrant / ES 不可用时不会更新。前者是 HTTP 层的真实观测，
        错误率与延迟告警必须建立在它之上，否则依赖故障恰好是最需要告警的时刻
        却没有指标。

        ``duration_ms`` 只在真正拿到响应耗时时才记入延迟直方图，避免把一个
        没有时延观测的请求混进分位数。
        """
        self.increment("http.requests")
        bucket = f"http.responses.{status_code // 100}xx"
        self.increment(bucket)
        if status_code == 429:
            self.increment("http.rate_limited")
        if duration_ms is not None and duration_ms >= 0:
            self.observe_histogram("http.request.duration", duration_ms)

    def set_active_requests(self, count: int) -> None:
        """当前在途请求数（用于饱和度观测）。"""
        self._gauges["http.active_requests"] = float(count)

    def set_exporter_state(self, state: str) -> None:
        """OTLP 导出器状态（1=已启用，0=未启用）。

        Exposed as a metric so an operator can confirm whether spans are leaving
        the process without reading logs. It is 0 both when export is off and when
        export was requested but is unavailable; the log line distinguishes them.
        """
        self._gauges[EXPORTER_ENABLED_METRIC] = 1.0 if state == "enabled" else 0.0
        self._gauges["otel.exporter_active"] = 1.0 if state == "enabled" else 0.0

    def set_redis_degraded(self, degraded: bool) -> None:
        """Redis 降级状态。

        由 core.pipeline_context 的真实降级路径调用，而不是由告警规则凭空假设。
        """
        self._gauges["redis_degraded_mode"] = 1.0 if degraded else 0.0
        if degraded:
            self.increment("redis.degraded_events")

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
        if hasattr(ctx, "blip_triggered") and ctx.blip_triggered:
            self.increment("blip.triggered")
        self.increment("blip.total")

        # KV Pressure
        if ctx.kv_pressure_at_entry > 0:
            self.set_gauge("kv_pressure", ctx.kv_pressure_at_entry)

        # PRD §12: NLI 矛盾比例（Answer Gate）
        if hasattr(ctx, "answer_gate_result") and ctx.answer_gate_result:
            nli_score = ctx.answer_gate_result.nli_contradiction_score
            self.observe_histogram("nli.contradiction_score", nli_score)
            if nli_score > 0.5:
                self.increment("nli.contradiction_high")

        # PRD §12: Prefix Cache 命中/未命中
        prefix_cache_hit = getattr(ctx, "prefix_cache_hit", None)
        if isinstance(ctx, dict):
            prefix_cache_hit = ctx.get("prefix_cache_hit")
        if prefix_cache_hit is True:
            self.increment("prefix_cache.hit")
        elif prefix_cache_hit is False:
            self.increment("prefix_cache.miss")

        # PRD §12: Admission Control 拒绝/排队计数
        if hasattr(ctx, "kv_pressure_at_entry"):
            self.increment("admission.total")
            # 在 pipeline 中 admission_reason 已记录到 audit log，此处统计通过/拒绝
            if hasattr(ctx, "_admission_admitted"):
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
                "L2_SESSION": self._counters.get("cache.hit.L2_SESSION", 0)
                / max(self._counters.get("cache.total", 1), 1),
            },
            "rewrite_fallback_rate": self._counters.get("rewrite.fallback", 0)
            / max(self._counters.get("rewrite.success", 0) + self._counters.get("rewrite.fail", 0), 1),
            # PRD §12: NLI 矛盾比例
            "nli_contradiction_rate": self._counters.get("nli.contradiction_high", 0)
            / max(self._counters.get("answer_gate.total", 1), 1),
            # PRD §12: Prefix Caching 命中率
            "prefix_cache_hit_rate": self._counters.get("prefix_cache.hit", 0)
            / max(self._counters.get("prefix_cache.hit", 0) + self._counters.get("prefix_cache.miss", 0), 1),
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

            def _pct(p, values=sorted_v, count=n):
                idx = int(count * p)
                return values[min(idx, count - 1)]

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
    告警规则引擎（**遗留 / 未接入 canonical 请求路径**）

    状态说明（请勿在文档中把它当作正式告警契约）：

    - 本类**没有**被 `app.py`、`api/*`、`core/pipeline.py` 或
      `core/pipeline_context.py` 引用。canonical 请求路径上不会构造它，
      也不会调用 `check_alerts()`。唯一调用方是本仓库的确定性测试。
    - 本仓库的**正式告警契约**是 `monitoring/prometheus/alerts.yml`：由外部
      Prometheus 加载、评估并触发 `RagAppDown` / `RagHighErrorRate` /
      `RagHighLatencyP95` / `RagRedisDegraded` / `RagHighLoginRateLimit` /
      `RagRequestSaturation`。这些规则只引用 `/api/metrics` 真实 emit 的 series。
    - 两套机制不可混为一谈：这里是**进程内**阈值比较，`config.json` 曾用于存放
      本引擎的自定义规则；该配置块已移除，因为它没有 canonical 消费者，留着会
      形成第二套"看起来像生产告警"的契约。规则阈值全部是 `DESIGN_TARGET`，
      任何一条都没有在生产触发过。
    - 若干默认规则依赖的指标（如 `kv_utilization`、`rerank_batch_queue_delay_p99`）
      在 canonical 路径上没有真实 producer，因此这些规则实际不会触发。这是
      保留本类的已知理由之一，不是可用的告警覆盖。

    保留本类而不是删除，是为了不破坏既有确定性测试；`tests/monitoring/` 中的
    reachability 契约测试会在它被真正接入时失败，从而强制同步文档口径。
    """

    def __init__(self, metrics: MetricsCollector):
        self.metrics = metrics
        self._alert_rules = self._load_alert_rules()
        self._active_alerts = {}
        self._alert_timestamps = {}  # 记录告警首次触发时间
        logger.info(f"AlertingManager 初始化完成 ({len(self._alert_rules)} 条规则)")

    def _load_alert_rules(self) -> list[dict]:
        """
        规则来源：**仅**代码内置的默认规则集。

        这里曾经支持从 `config.json` → `alerting.rules` 读取自定义覆盖。该配置块
        已移除：本类没有 canonical 消费者，`config.json` 里保留一份告警规则只会
        让人误以为存在第二套生产告警契约。`config.get("alerting", {})` 的读取
        保留为空操作路径，便于旧配置文件继续被容忍加载，但不会产生规则。

        注意这些 metric 名是**本引擎内部的 collector 名**（点分小写），不是
        `monitoring/prometheus/alerts.yml` 使用的 `rag_*` Prometheus series。
        两套命名不可互换。
        """
        # 默认规则（PRD §12 完整告警清单）
        default_rules = [
            {
                "name": "kv_pressure_critical",
                "metric": "kv_pressure",
                "threshold": 0.9,
                "duration_s": 30,
                "severity": "critical",
                "comparison": "gt",
            },
            {
                "name": "kv_cache_high",
                "metric": "kv_utilization",
                "threshold": 0.85,
                "duration_s": 60,
                "severity": "warning",
                "comparison": "gt",
            },
            {
                "name": "rerank_batch_delay",
                "metric": "rerank_batch_queue_delay_p99",
                "threshold": 50,
                "duration_s": 30,
                "severity": "warning",
                "comparison": "gt",
            },
            {
                "name": "rewrite_fallback_high",
                "metric": "rewrite_fallback_rate",
                "threshold": 0.3,
                "duration_s": 300,
                "severity": "warning",
                "comparison": "gt",
            },
            {
                "name": "degradation_spike",
                "metric": "degradation.total",
                "threshold": 10,
                "duration_s": 300,
                "severity": "critical",
                "comparison": "gt",
            },
            {
                "name": "blip_trigger_rate_high",
                "metric": "blip_trigger_rate",
                "threshold": 0.10,
                "duration_s": 300,
                "severity": "warning",
                "comparison": "gt",
            },
            # PRD §12 新增告警规则
            {
                "name": "prefix_cache_drop",
                "metric": "prefix_cache_hit_rate",
                "threshold": 0.5,
                "duration_s": 300,
                "severity": "warning",
                "comparison": "lt",
            },
            {
                "name": "redis_degraded_long",
                "metric": "redis.degraded_events",
                "threshold": 5,
                "duration_s": 300,
                "severity": "warning",
                "comparison": "gt",
            },
            {
                "name": "l1_hit_rate_drop",
                "metric": "cache_hit_rate_L1",
                "threshold": 0.3,
                "duration_s": 300,
                "severity": "warning",
                "comparison": "lt",
            },
            {
                "name": "l2_hit_rate_drop",
                "metric": "cache_hit_rate_L2",
                "threshold": 0.3,
                "duration_s": 300,
                "severity": "warning",
                "comparison": "lt",
            },
            {
                "name": "cache_failure_spike",
                "metric": "cache.failure_rate",
                "threshold": 0.1,
                "duration_s": 300,
                "severity": "critical",
                "comparison": "gt",
            },
        ]

        # Legacy compatibility path: an `alerting.rules` block may still exist in
        # an operator's local config.json. It is honoured only when the class is
        # actually driven by a caller, which today no canonical path does. The
        # repository's own config.json no longer ships the block.
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
            total = self.metrics._counters.get("rewrite.success", 0) + self.metrics._counters.get("rewrite.fail", 0)
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
        # Counter names must match the writers in record_request /
        # record_prefix_cache_hit / record_prefix_cache_miss (`prefix_cache.hit`
        # / `prefix_cache.miss`). They previously read the plural
        # `prefix_cache.hits` / `prefix_cache.misses`, which no writer ever
        # increments, so this rule could only ever observe zeros.
        if metric_name == "prefix_cache_hit_rate":
            hits = self.metrics._counters.get("prefix_cache.hit", 0)
            misses = self.metrics._counters.get("prefix_cache.miss", 0)
            total = hits + misses
            # No samples means "not observed", not "perfect" and not "zero".
            # Returning None makes check_alerts() skip the rule, so a cold
            # prefix cache can never fire a false drop alert.
            return hits / total if total > 0 else None

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
