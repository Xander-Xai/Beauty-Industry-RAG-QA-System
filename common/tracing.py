"""
Lightweight metrics collector and alerting (no OTel dependency required).

Mirrors the structure of monitoring/otel_tracer.py but lives in common/ so
that every microservice can import it without pulling in the full monitoring
package.

Public API:
    tracer    = get_tracer()         -> ServiceTracer (context-manager spans)
    metrics   = get_metrics()        -> MetricsCollector (counters / gauges / histograms)
    alerter   = get_alerting()       -> AlertingManager (threshold rule engine)

All singletons are lazily created on first access.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from common.config import get_config

logger = logging.getLogger(__name__)


# ── Thread-safe lock helper ──────────────────────────────────────────────

_global_lock = threading.Lock()


# ===================================================================
# ServiceTracer -- lightweight span tracer
# ===================================================================


class ServiceTracer:
    """
    Minimal span-based tracer.

    Usage::

        tracer = ServiceTracer()
        with tracer.trace("rewrite", {"query": "..."}) as span:
            result = do_rewrite()
        # span automatically records duration and status

    When OpenTelemetry is available the tracer will delegate to it;
    otherwise spans are kept in an in-memory ring buffer.
    """

    def __init__(self, *, max_spans: int = 1000) -> None:
        self._spans: list[dict[str, Any]] = []
        self._max_spans = max_spans
        self._use_otel = False
        self._otel_tracer: Any = None
        self._try_init_otel()
        mode = "OTel" if self._use_otel else "local"
        logger.info("ServiceTracer initialised (%s mode)", mode)

    # -- OTel optional bootstrap -------------------------------------------

    def _try_init_otel(self) -> None:
        try:
            from opentelemetry import trace as otel_trace

            provider = otel_trace.get_tracer_provider()
            # Only use OTel if the provider has actual span processors configured
            if provider is not None and hasattr(provider, "add_span_processor"):
                self._otel_tracer = otel_trace.get_tracer("rag-microservices")
                self._use_otel = True
        except Exception as exc:
            logger.debug("OpenTelemetry is unavailable; using local tracing: %s", exc)

    # -- public API --------------------------------------------------------

    @contextmanager
    def trace(
        self,
        span_name: str,
        attributes: dict[str, Any] | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Context manager that records a timed span."""
        if self._use_otel and self._otel_tracer is not None:
            yield from self._trace_otel(span_name, attributes or {})
        else:
            yield from self._trace_local(span_name, attributes or {})

    def get_trace_summary(self, *, last_n: int = 100) -> list[dict[str, Any]]:
        """Return the most recent *last_n* spans."""
        with _global_lock:
            return list(self._spans[-last_n:])

    # -- internal ----------------------------------------------------------

    def _trace_local(
        self,
        span_name: str,
        attributes: dict[str, Any],
    ) -> Generator[dict[str, Any], None, None]:
        span: dict[str, Any] = {
            "name": span_name,
            "start_time": time.time(),
            "attributes": attributes,
            "status": "OK",
        }
        try:
            yield span
        except Exception as exc:
            span["status"] = "ERROR"
            span["error"] = str(exc)
            raise
        finally:
            span["end_time"] = time.time()
            span["duration_ms"] = (span["end_time"] - span["start_time"]) * 1000
            with _global_lock:
                self._spans.append(span)
                if len(self._spans) > self._max_spans:
                    self._spans = self._spans[-self._max_spans :]

    def _trace_otel(
        self,
        span_name: str,
        attributes: dict[str, Any],
    ) -> Generator[dict[str, Any], None, None]:
        with self._otel_tracer.start_as_current_span(span_name) as otel_span:
            for k, v in attributes.items():
                otel_span.set_attribute(k, str(v))
            span: dict[str, Any] = {
                "name": span_name,
                "start_time": time.time(),
                "attributes": attributes,
                "status": "OK",
            }
            try:
                yield span
            except Exception as exc:
                span["status"] = "ERROR"
                span["error"] = str(exc)
                otel_span.record_exception(exc)
                raise
            finally:
                span["end_time"] = time.time()
                span["duration_ms"] = (span["end_time"] - span["start_time"]) * 1000


# ===================================================================
# MetricsCollector
# ===================================================================


class MetricsCollector:
    """
    Thread-safe metrics collector with counters, gauges and histograms.

    Provides the subset of metrics needed by every microservice:
    - request counts, cache hits, rewrite fallback rates
    - latency histograms with percentile computation
    - gauge-style values (KV pressure, queue depth, ...)
    """

    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._start_time = time.time()
        self._histogram_cap = 1000
        logger.info("MetricsCollector initialised")

    # -- mutators ----------------------------------------------------------

    def increment(self, name: str, value: int = 1) -> None:
        """Increment a named counter."""
        with _global_lock:
            self._counters[name] += value

    def set_gauge(self, name: str, value: float) -> None:
        """Set a gauge to a specific value."""
        with _global_lock:
            self._gauges[name] = value

    def observe_histogram(self, name: str, value: float) -> None:
        """Record a value in a named histogram."""
        with _global_lock:
            bucket = self._histograms[name]
            bucket.append(value)
            if len(bucket) > self._histogram_cap:
                self._histograms[name] = bucket[-self._histogram_cap :]

    def get_counter(self, name: str) -> int:
        with _global_lock:
            return self._counters.get(name, 0)

    def get_gauge(self, name: str) -> float:
        with _global_lock:
            return self._gauges.get(name, 0.0)

    # -- queries -----------------------------------------------------------

    @staticmethod
    def _percentile(values: list[float], p: float) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        idx = int(len(s) * p)
        return s[min(idx, len(s) - 1)]

    def get_stats(self) -> dict[str, Any]:
        """Return a snapshot of all collected metrics."""
        with _global_lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            histograms = dict(self._histograms)

        total_req = counters.get("cache.total", 1) or 1
        rewrite_total = (counters.get("rewrite.success", 0) + counters.get("rewrite.fail", 0)) or 1

        stats: dict[str, Any] = {
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "counters": counters,
            "gauges": gauges,
            "cache_hit_rate": {
                "L1": counters.get("cache.hit.L1", 0) / total_req,
                "L2": counters.get("cache.hit.L2", 0) / total_req,
            },
            "rewrite_fallback_rate": counters.get("rewrite.fallback", 0) / rewrite_total,
            "latency_percentiles": {},
        }

        for name, values in histograms.items():
            stats["latency_percentiles"][name] = {
                "p50": round(self._percentile(values, 0.50), 2),
                "p95": round(self._percentile(values, 0.95), 2),
                "p99": round(self._percentile(values, 0.99), 2),
                "count": len(values),
            }

        return stats


# ===================================================================
# AlertingManager
# ===================================================================


class AlertingManager:
    """
    Threshold-based alerting engine.

    Rules are loaded from ``config.json -> alerting.rules``.
    ``check_alerts()`` should be called periodically (e.g. by the
    monitoring-service cron or a background task).
    """

    def __init__(self, metrics: MetricsCollector) -> None:
        self.metrics = metrics
        self._rules = self._load_rules()
        self._active: dict[str, dict[str, Any]] = {}
        self._first_violation: dict[str, float] = {}
        logger.info("AlertingManager initialised (%d rules)", len(self._rules))

    # -- rule loading ------------------------------------------------------

    @staticmethod
    def _load_rules() -> list[dict[str, Any]]:
        defaults = [
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
        ]
        try:
            custom = get_config().alerting.rules
            if custom:
                merged = {r["name"]: r for r in defaults}
                for r in custom:
                    merged[r.name] = {
                        "name": r.name,
                        "metric": r.metric,
                        "threshold": r.threshold,
                        "duration_s": r.duration_s,
                        "severity": r.severity,
                        "comparison": r.comparison,
                    }
                return list(merged.values())
        except Exception as exc:
            logger.debug("Could not load configured alert rules: %s", exc)
        return defaults

    # -- evaluation --------------------------------------------------------

    def _resolve_metric(self, metric_name: str) -> float | None:
        """Resolve a metric value, supporting derived metrics."""
        # Direct gauge
        val = self.metrics.get_gauge(metric_name)
        if metric_name in self.metrics._gauges:
            return val

        # Direct counter
        if metric_name in self.metrics._counters:
            return float(self.metrics.get_counter(metric_name))

        # Derived: rewrite_fallback_rate
        if metric_name == "rewrite_fallback_rate":
            total = self.metrics.get_counter("rewrite.success") + self.metrics.get_counter("rewrite.fail")
            if total > 0:
                return self.metrics.get_counter("rewrite.fallback") / total
            return 0.0

        # Derived: cache_hit_rate_L1
        if metric_name == "cache_hit_rate_L1":
            total = self.metrics.get_counter("cache.total")
            if total > 0:
                return self.metrics.get_counter("cache.hit.L1") / total
            return 0.0

        # Histogram p99
        with _global_lock:
            bucket = self.metrics._histograms.get(metric_name)
            if bucket:
                return self._p99(bucket)
        return None

    @staticmethod
    def _p99(values: list[float]) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        return s[int(len(s) * 0.99)]

    def check_alerts(self) -> list[dict[str, Any]]:
        """
        Evaluate all rules and return a list of currently-active alerts.

        Call periodically (e.g. every 10 seconds).
        """
        now = time.time()

        for rule in self._rules:
            name: str = rule["name"]
            metric: str = rule["metric"]
            threshold: float = rule["threshold"]
            duration: float = rule["duration_s"]
            comparison: str = rule.get("comparison", "gt")
            severity: str = rule.get("severity", "warning")

            current = self._resolve_metric(metric)
            if current is None:
                continue

            violated = False
            if comparison == "gt":
                violated = current > threshold
            elif comparison == "lt":
                violated = current < threshold
            elif comparison == "eq":
                violated = abs(current - threshold) < 1e-6

            if violated:
                if name not in self._first_violation:
                    self._first_violation[name] = now

                elapsed = now - self._first_violation[name]
                if elapsed >= duration:
                    self._active[name] = {
                        "name": name,
                        "severity": severity,
                        "metric": metric,
                        "current_value": round(current, 4),
                        "threshold": threshold,
                        "triggered_at": self._first_violation[name],
                        "duration_s": round(elapsed, 1),
                        "message": (
                            f"{name}: {metric}={current:.4f} "
                            f"{'>' if comparison == 'gt' else '<'} {threshold} "
                            f"for {elapsed:.0f}s"
                        ),
                    }
                    logger.warning("ALERT: %s", self._active[name]["message"])
            else:
                # Recovered
                self._active.pop(name, None)
                self._first_violation.pop(name, None)

        return self.get_active_alerts()

    def get_active_alerts(self) -> list[dict[str, Any]]:
        """Return a snapshot of all currently-active alerts."""
        return list(self._active.values())

    def clear_alert(self, name: str) -> None:
        """Manually silence / clear a named alert."""
        self._active.pop(name, None)
        self._first_violation.pop(name, None)


# ===================================================================
# Module-level singletons
# ===================================================================

_tracer_instance: ServiceTracer | None = None
_metrics_instance: MetricsCollector | None = None
_alerting_instance: AlertingManager | None = None


def get_tracer() -> ServiceTracer:
    global _tracer_instance
    if _tracer_instance is None:
        with _global_lock:
            if _tracer_instance is None:
                _tracer_instance = ServiceTracer()
    return _tracer_instance


def get_metrics() -> MetricsCollector:
    global _metrics_instance
    if _metrics_instance is None:
        with _global_lock:
            if _metrics_instance is None:
                _metrics_instance = MetricsCollector()
    return _metrics_instance


def get_alerting() -> AlertingManager:
    global _alerting_instance
    if _alerting_instance is None:
        with _global_lock:
            if _alerting_instance is None:
                _alerting_instance = AlertingManager(get_metrics())
    return _alerting_instance
