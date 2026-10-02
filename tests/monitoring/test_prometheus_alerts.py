"""Tests for the Prometheus alert rules.

The point of these tests is that an alert can never reference a metric the
application does not actually emit. A rule pointing at a non-existent metric
would look like coverage while being incapable of ever firing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

from monitoring.otel_tracer import MetricsCollector  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
ALERTS_PATH = REPO_ROOT / "monitoring/prometheus/alerts.yml"

#: Metric families the canonical collector can emit, discovered by exercising it
#: rather than by hardcoding a list that could drift.
HTTP_PATHS = ("/api/health", "/api/auth/login")


def _collector_metric_names() -> set[str]:
    """Names the collector actually exposes after real observations."""
    collector = MetricsCollector()
    collector.set_active_requests(0)
    for status in (200, 401, 429, 500):
        collector.record_http_request(status, 12.5)
    # Exercise the degraded path too: `rag_redis_degraded_events` only exists
    # once the real fallback has actually fired.
    collector.set_redis_degraded(True)
    collector.set_redis_degraded(False)
    text = collector.to_prometheus_text()
    return set(re.findall(r"^(rag_[A-Za-z0-9_]+)", text, flags=re.MULTILINE))


@pytest.fixture(scope="module")
def rules_doc():
    return yaml.safe_load(ALERTS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def all_rules(rules_doc):
    return [rule for group in rules_doc["groups"] for rule in group["rules"]]


# ── structure ───────────────────────────────────────────────────────────────


def test_alerts_file_exists_and_parses(rules_doc):
    assert ALERTS_PATH.is_file()
    assert "groups" in rules_doc
    assert rules_doc["groups"]


def test_every_rule_is_well_formed(all_rules):
    for rule in all_rules:
        assert "alert" in rule, f"rule without a name: {rule}"
        assert "expr" in rule, f"{rule['alert']} has no expr"
        assert rule["expr"].strip(), f"{rule['alert']} has an empty expr"
        labels = rule.get("labels", {})
        annotations = rule.get("annotations", {})
        assert labels.get("severity") in {"critical", "warning", "info"}, rule["alert"]
        assert annotations.get("summary"), f"{rule['alert']} has no summary"
        assert annotations.get("description"), f"{rule['alert']} has no description"
        assert annotations.get("runbook"), f"{rule['alert']} has no runbook link"


def test_required_alerts_exist(all_rules):
    names = {rule["alert"] for rule in all_rules}
    assert {
        "RagAppDown",
        "RagHighErrorRate",
        "RagHighLatencyP95",
        "RagRedisDegraded",
        "RagHighLoginRateLimit",
    } <= names


def test_alert_names_are_unique(all_rules):
    names = [rule["alert"] for rule in all_rules]
    assert len(names) == len(set(names))


def test_every_rule_maps_to_an_slo(all_rules):
    for rule in all_rules:
        assert "slo" in rule.get("labels", {}), f"{rule['alert']} is not tied to an SLO"


# ── metric correspondence ───────────────────────────────────────────────────


def test_every_referenced_metric_is_actually_emitted(all_rules):
    """The core guard: a rule may not reference a metric we never emit."""
    available = _collector_metric_names()
    # `up` is provided by Prometheus itself, not the application.
    externally_provided = {"up"}
    referenced: set[str] = set()
    for rule in all_rules:
        for name in re.findall(r"\brag_[A-Za-z0-9_]+", rule["expr"]):
            referenced.add(name.split("{")[0])
    unknown = referenced - available - externally_provided
    assert not unknown, f"rules reference metrics that are never emitted: {sorted(unknown)}"


def test_inventory_covers_the_documented_metrics():
    """The header comment lists the inventory; keep it honest."""
    available = _collector_metric_names()
    assert {
        "rag_http_requests",
        "rag_http_responses_2xx",
        "rag_http_responses_4xx",
        "rag_http_responses_5xx",
        "rag_http_rate_limited",
        "rag_http_active_requests",
        "rag_http_request_duration_seconds",
        "rag_redis_degraded_mode",
        "rag_redis_degraded_events",
        "rag_uptime_seconds",
    } <= available


def test_prometheus_text_is_valid_for_every_emitted_sample():
    """Regression: malformed exposition lines are silently unscrapeable.

    A summary sample line without its metric name (`rag_{quantile="0.5"}`) is not
    valid exposition format, so a latency alert would have had no data at all.
    """
    text = _exposition_text()
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        metric_part = line.split("{", 1)[0].split(" ", 1)[0]
        assert metric_part.startswith("rag_"), f"sample line has no metric name: {line!r}"
        assert "{" not in metric_part, f"unterminated label braces: {line!r}"


def _exposition_text() -> str:
    collector = MetricsCollector()
    collector.record_http_request(200, 5.0)
    collector.observe_histogram("latency.rewrite", 12.0)
    return collector.to_prometheus_text()


def test_monitoring_service_histogram_samples_carry_their_metric_name():
    """The second collector had the same malformed-summary bug.

    `monitoring-service` is a hyphenated directory, so it is loaded by path
    rather than imported as a package.
    """
    text = _monitoring_service_exposition_text()
    quantile_lines = [line for line in text.splitlines() if "quantile" in line]
    assert quantile_lines, "expected quantile samples after recording a histogram"
    for line in quantile_lines:
        metric_part = line.split("{", 1)[0]
        assert metric_part.startswith("rag_"), f"quantile sample without a metric name: {line!r}"
        assert metric_part.strip(), f"empty metric name before label: {line!r}"


def _monitoring_service_exposition_text() -> str:
    import importlib.util

    path = REPO_ROOT / "monitoring-service" / "metrics_collector.py"
    spec = importlib.util.spec_from_file_location("ms_metrics_collector", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    collector = module.MetricsCollector()
    collector.observe_histogram("latency.total", 30.0)
    collector.observe_histogram("latency.total", 10.0)
    return collector.to_prometheus_text()


# ── impossibility / default-zero guards ─────────────────────────────────────


def test_no_alert_fires_only_on_a_metric_that_is_always_zero(all_rules):
    """A pre-initialized placeholder gauge must not back an alert on its own.

    Several gauges in this project are documented as placeholders that are
    pre-set to 0 and never updated. An alert on one of those would either never
    fire or fire constantly, and would misrepresent it as coverage.
    """
    placeholder_only = {
        "rag_blip_trigger_rate",
        "rag_clip_sync_timeout_rate",
        "rag_nli_contradiction_rate",
        "rag_rerank_batch_fill_rate",
        "rag_rerank_batch_queue_latency_p99",
        "rag_evidence_score",
    }
    for rule in all_rules:
        for name in re.findall(r"\brag_[A-Za-z0-9_]+", rule["expr"]):
            assert name not in placeholder_only, f"{rule['alert']} alerts on placeholder metric {name}"


def test_latency_alert_guards_against_a_stale_quantile(all_rules):
    """A summary keeps its last quantile forever, so traffic must be required."""
    rule = next(r for r in all_rules if r["alert"] == "RagHighLatencyP95")
    assert "rag_http_request_duration_seconds_count" in rule["expr"]
    assert "rate(" in rule["expr"]


def test_error_rate_alert_requires_traffic(all_rules):
    """Without a traffic floor, one 500 during idle looks like a 100% error rate."""
    rule = next(r for r in all_rules if r["alert"] == "RagHighErrorRate")
    assert "clamp_min" in rule["expr"], "division by request rate must be guarded"
    assert "rag_http_requests" in rule["expr"]


def test_redis_alert_is_driven_by_the_real_fallback_path(all_rules):
    """`rag_redis_degraded_mode` must be set by code, not only pre-initialized."""
    from core.pipeline_context import SessionState

    assert hasattr(SessionState, "_publish_redis_degraded")
    collector = MetricsCollector()
    collector.set_redis_degraded(True)
    assert "rag_redis_degraded_mode 1.0" in collector.to_prometheus_text()
    collector.set_redis_degraded(False)
    assert "rag_redis_degraded_mode 0.0" in collector.to_prometheus_text()


def test_redis_client_absent_actually_flips_the_metric(monkeypatch):
    """Drive the real fallback path, not just the setter.

    `_publish_redis_degraded` swallows its own exceptions so a metrics problem
    can never break session handling. That is correct, but it also means a
    broken publish would go unnoticed: `hasattr` would still pass and the gauge
    would silently stay at its pre-initialised 0. This test therefore triggers
    the genuine code path with Redis unavailable and asserts the exported value.
    """
    import core.pipeline_context as pipeline_context
    from api.routes import get_metrics

    monkeypatch.setattr(pipeline_context, "_get_redis_client", lambda: None)
    collector = get_metrics()
    collector.set_redis_degraded(False)

    # Redis unavailable: no client, so the session must fall back to memory...
    assert pipeline_context.SessionState._try_get_redis("degraded-probe") is None
    # ...and the gauge must actually have moved, without raising AttributeError.
    assert "rag_redis_degraded_mode 1.0" in collector.to_prometheus_text()


def test_redis_read_failure_actually_flips_the_metric(monkeypatch):
    """A read that raises is the second degradation branch."""
    import core.pipeline_context as pipeline_context
    from api.routes import get_metrics

    class BrokenRedis:
        def get(self, key):
            raise ConnectionError("redis is down")

    monkeypatch.setattr(pipeline_context, "_get_redis_client", lambda: BrokenRedis())
    collector = get_metrics()
    collector.set_redis_degraded(False)

    assert pipeline_context.SessionState._try_get_redis("degraded-probe-2") is None
    assert "rag_redis_degraded_mode 1.0" in collector.to_prometheus_text()


def test_successful_redis_read_clears_the_metric(monkeypatch):
    """A healthy read must clear the gauge, so the alert can recover."""
    import json

    import core.pipeline_context as pipeline_context
    from api.routes import get_metrics

    class HealthyRedis:
        def get(self, key):
            return json.dumps(pipeline_context.SessionState("probe-session")._to_dict())

        def expire(self, key, ttl):
            return True

        def setex(self, key, ttl, value):
            return True

    monkeypatch.setattr(pipeline_context, "_get_redis_client", lambda: HealthyRedis())
    collector = get_metrics()
    collector.set_redis_degraded(True)

    session = pipeline_context.SessionState._try_get_redis("healthy-probe")
    assert session is not None
    assert "rag_redis_degraded_mode 0.0" in collector.to_prometheus_text()


# ── documentation boundary ──────────────────────────────────────────────────


def test_alerts_file_states_that_thresholds_are_design_targets():
    text = ALERTS_PATH.read_text(encoding="utf-8")
    assert "DESIGN_TARGET" in text
    assert "NOT VALIDATED IN PRODUCTION" in text.upper()


def test_alerts_file_documents_why_qdrant_and_es_are_not_alerted():
    """An honest gap beats a rule that can never fire."""
    text = ALERTS_PATH.read_text(encoding="utf-8")
    assert "Qdrant" in text
    assert "Elasticsearch" in text
    assert "DELIBERATELY NOT ALERTED" in text


def test_runbook_covers_every_alert(all_rules):
    runbook = (REPO_ROOT / "docs/slo-runbook.md").read_text(encoding="utf-8")
    for rule in all_rules:
        assert rule["alert"] in runbook, f"{rule['alert']} is not covered by the runbook"
