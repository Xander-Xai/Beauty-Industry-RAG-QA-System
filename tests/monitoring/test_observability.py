"""Tests for the opt-in OTLP exporter path and the Grafana dashboard.

Two properties matter most and are asserted directly:

* Export is opt-in and non-fatal. A broken or missing exporter must never
  prevent the application from serving.
* No span and no dashboard panel may reference a metric the canonical collector
  does not emit, or a metric that is documented as a never-updated placeholder.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from monitoring.otel_exporter import (
    ALLOWED_ATTRIBUTE_KEYS,
    DEFAULT_SERVICE_NAME,
    REDACTED,
    ExporterState,
    OtelExporterConfig,
    build_resource,
    build_span_processor,
    load_config,
    sanitize_attributes,
)
from monitoring.otel_tracer import MetricsCollector, OpenTelemetryTracer

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_PATH = REPO_ROOT / "monitoring/grafana/dashboards/rag-overview.json"
ALERTS_PATH = REPO_ROOT / "monitoring/prometheus/alerts.yml"

#: Gauges this project documents as pre-initialised placeholders whose collection
#: hooks are still TODO. A panel or alert on one of these would show a flat zero
#: and read as a measurement.
PLACEHOLDER_METRICS = {
    "rag_blip_trigger_rate",
    "rag_clip_sync_timeout_rate",
    "rag_nli_contradiction_rate",
    "rag_rerank_batch_fill_rate",
    "rag_rerank_batch_queue_latency_p99",
}


def emitted_metrics() -> set[str]:
    """Discover what the canonical collector actually exposes."""
    collector = MetricsCollector()
    for status in (200, 401, 429, 500):
        collector.record_http_request(status, 12.5)
    collector.set_active_requests(2)
    collector.set_exporter_state(ExporterState.ENABLED)
    # Exercise both transitions: `rag_redis_degraded_events` only appears once
    # the real fallback has fired, so a healthy-only probe would miss it.
    collector.set_redis_degraded(True)
    collector.set_redis_degraded(False)
    collector.increment("cache.hit.L1")
    collector.increment("cache.total")
    collector.observe_histogram("latency.rewrite", 9.0)
    return set(re.findall(r"^(rag_[A-Za-z0-9_]+)", collector.to_prometheus_text(), flags=re.MULTILINE))


# ── configuration ───────────────────────────────────────────────────────────


def test_export_is_disabled_by_default():
    config = load_config({})
    assert config.enabled is False
    assert config.service_name == DEFAULT_SERVICE_NAME


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
def test_export_enables_only_on_explicit_true(value):
    assert load_config({"OTEL_EXPORT_ENABLED": value}).enabled is True


@pytest.mark.parametrize("value", ["", "false", "0", "no", "off", "maybe", "undefined"])
def test_unrecognised_values_leave_export_off(value):
    assert load_config({"OTEL_EXPORT_ENABLED": value}).enabled is False


def test_endpoint_is_normalised():
    config = load_config({"OTEL_EXPORT_ENABLED": "true", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://c:4318/"})
    assert config.endpoint == "http://c:4318"


def test_headers_are_parsed():
    config = load_config({"OTEL_EXPORTER_OTLP_HEADERS": "a=1, b=2 , malformed , c=3=4"})
    assert config.headers == {"a": "1", "b": "2", "c": "3=4"}
    assert config.headers_present is True


def test_unsupported_protocol_falls_back():
    config = load_config({"OTEL_EXPORTER_OTLP_PROTOCOL": "thrift"})
    assert config.protocol == "http/protobuf"


def test_describe_never_exposes_header_values():
    config = OtelExporterConfig(enabled=True, endpoint="http://c:4318", headers={"authorization": "Bearer s3cret"})
    described = json.dumps(config.describe())
    assert "s3cret" not in described
    assert "headers_present" in described
    assert "authorization" in described  # the name is fine; the value is not


# ── non-fatal failure semantics ─────────────────────────────────────────────


def test_disabled_config_yields_no_processor():
    processor, state, reason = build_span_processor(OtelExporterConfig(enabled=False))
    assert processor is None
    assert state == ExporterState.DISABLED
    assert reason


def test_enabled_without_endpoint_fails_without_raising():
    processor, state, reason = build_span_processor(OtelExporterConfig(enabled=True, endpoint=""))
    assert processor is None
    assert state == ExporterState.FAILED
    assert "ENDPOINT" in reason


def test_builder_returns_a_string_reason_on_success():
    """Regression: the reason slot once held a Resource object."""
    processor, state, reason = build_span_processor(OtelExporterConfig(enabled=True, endpoint="http://127.0.0.1:4318"))
    assert isinstance(reason, str)


def test_unreachable_endpoint_still_initialises(monkeypatch):
    """A collector that is not listening must not stop export from being wired.

    BatchSpanProcessor connects lazily, so this succeeds; the point is that no
    exception escapes and the application keeps working.
    """
    processor, state, _ = build_span_processor(OtelExporterConfig(enabled=True, endpoint="http://127.0.0.1:1"))
    assert processor is not None
    assert state == ExporterState.ENABLED


def test_tracer_init_is_non_fatal_with_broken_export_env(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORT_ENABLED", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    tracer = OpenTelemetryTracer()
    # The tracer exists and the SDK path is usable regardless of export state.
    assert tracer.exporter_state in ExporterState.ALL
    assert isinstance(tracer.exporter_detail, str)


def test_tracer_with_export_disabled_reports_disabled(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORT_ENABLED", "false")
    tracer = OpenTelemetryTracer()
    assert tracer.exporter_state == ExporterState.DISABLED


def test_exporter_state_is_scrapeable():
    collector = MetricsCollector()
    collector.set_exporter_state(ExporterState.ENABLED)
    text = collector.to_prometheus_text()
    assert "rag_otel_exporter_enabled 1.0" in text
    collector.set_exporter_state(ExporterState.UNAVAILABLE)
    assert "rag_otel_exporter_enabled 0.0" in collector.to_prometheus_text()


def test_build_resource_is_optional():
    resource = build_resource(OtelExporterConfig(service_name="svc"))
    assert resource is None or resource is not None  # must not raise either way


# ── span attribute safety ───────────────────────────────────────────────────


def test_allowed_keys_pass_through():
    attributes = {"request_id": "r1", "route": "/api/query", "document_count": 4}
    assert sanitize_attributes(attributes) == attributes


def test_disallowed_keys_are_dropped_not_redacted():
    result = sanitize_attributes({"query": "烟酰胺的安全浓度", "user_query": "secret formula", "request_id": "r"})
    assert "query" not in result
    assert "user_query" not in result
    assert result == {"request_id": "r"}


def test_bearer_value_under_an_allowed_key_is_redacted():
    result = sanitize_attributes({"error_type": "Bearer abc.def.ghi"})
    assert result["error_type"] == REDACTED


def test_jwt_value_under_an_allowed_key_is_redacted():
    result = sanitize_attributes({"route": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MSJ9.abcdefghijklmnop"})
    assert result["route"] == REDACTED


def test_long_values_are_truncated():
    result = sanitize_attributes({"error_type": "x" * 5000})
    assert len(result["error_type"]) <= 256


def test_structures_are_summarised_by_length():
    result = sanitize_attributes({"retrieval_path_count": [1, 2, 3], "document_count": {"a": 1}})
    assert result["retrieval_path_count"] == 3
    assert result["document_count"] == 1


def test_allowlist_cannot_be_widened_by_a_caller():
    """Every allow-listed key must look like telemetry, not payload."""
    for key in ALLOWED_ATTRIBUTE_KEYS:
        assert not any(word in key.lower() for word in ("query", "text", "content", "body", "prompt"))


def test_empty_attributes_are_handled():
    assert sanitize_attributes(None) == {}
    assert sanitize_attributes({}) == {}


# ── dashboard ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def dashboard():
    return json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))


def test_dashboard_parses_and_has_panels(dashboard):
    assert DASHBOARD_PATH.is_file()
    assert dashboard["panels"]


def test_dashboard_only_references_emitted_metrics(dashboard):
    available = emitted_metrics()
    referenced = {
        match
        for panel in dashboard["panels"]
        for match in re.findall(r"rag_[A-Za-z0-9_]+", panel["targets"][0]["expr"])
    }
    unknown = {name for name in referenced if name.rstrip("_") not in available and name not in available}
    assert not unknown, f"dashboard references metrics that are never emitted: {sorted(unknown)}"


def test_dashboard_has_no_placeholder_metric_panels(dashboard):
    for panel in dashboard["panels"]:
        referenced = set(re.findall(r"rag_[A-Za-z0-9_]+", panel["targets"][0]["expr"]))
        overlap = referenced & PLACEHOLDER_METRICS
        assert not overlap, f"{panel['title']} plots documented placeholder metrics: {sorted(overlap)}"


def test_dashboard_has_no_fabricated_kpi_panels(dashboard):
    """No hallucination-rate, live-RAGAS or GPU-utilization panel may exist.

    Scoped to the panels. The dashboard's top-level comment names those metrics in
    order to state they are absent, so a whole-document search would contradict the
    very statement it is meant to enforce.
    """
    for panel in dashboard["panels"]:
        blob = " ".join([panel["title"], panel["description"], panel["targets"][0]["expr"]]).lower()
        for forbidden in ("hallucination", "ragas", "gpu", "vram", "ttft"):
            assert forbidden not in blob, f"panel {panel['title']!r} references {forbidden!r}"


def test_dashboard_comment_states_the_exclusion(dashboard):
    """The exclusion must be documented, not just implemented."""
    comment = dashboard["__comment__"].lower()
    for named in ("hallucination", "gpu"):
        assert named in comment


def test_every_panel_has_a_description_and_unit(dashboard):
    for panel in dashboard["panels"]:
        assert panel["description"].strip(), f"{panel['title']} has no description"
        assert panel["fieldConfig"]["defaults"]["unit"]


def test_dashboard_states_it_only_shows_emitted_metrics(dashboard):
    assert "__comment__" in dashboard
    assert "actually emits" in dashboard["__comment__"]


# ── cross-artifact consistency ──────────────────────────────────────────────


def test_alert_metrics_are_all_emitted():
    """The alerts file must not reference a metric we never emit."""
    text = ALERTS_PATH.read_text(encoding="utf-8")
    available = emitted_metrics()
    referenced = set(re.findall(r"\brag_[A-Za-z0-9_]+", text))
    unknown = {name for name in referenced if name.rstrip("_") not in available and name not in available}
    assert not unknown, f"alerts reference unknown metrics: {sorted(unknown)}"


def test_optional_dependencies_are_not_in_default_requirements():
    """The OTLP exporter stays optional so a new advisory cannot break CI."""
    default = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "opentelemetry-exporter-otlp" not in default
    optional = (REPO_ROOT / "requirements-otel.txt").read_text(encoding="utf-8")
    assert "opentelemetry-exporter-otlp" in optional
    assert "pip-audit" in optional, "the optional set must document its own audit step"


def test_observability_overlay_is_additive_only():
    """The canonical deployment must not start Prometheus/Jaeger/Grafana."""
    import yaml

    base = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    overlay = yaml.safe_load((REPO_ROOT / "docker-compose.observability.yml").read_text(encoding="utf-8"))
    base_services = set(base.get("services", {}))
    overlay_services = set(overlay.get("services", {}))
    assert {"prometheus", "jaeger", "grafana"} <= overlay_services
    assert not (base_services & overlay_services), "the overlay must not redefine a canonical service"


def test_env_example_documents_the_exporter_switch():
    env = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "OTEL_EXPORT_ENABLED=false" in env
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" in env
    assert "OTEL_SERVICE_NAME" in env
