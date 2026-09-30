"""
Tests for GET /api/metrics endpoint (GAP-18)

Covers:
- Returns 200 with Content-Type text/plain
- Output matches Prometheus text exposition format
- Contains counter, gauge, and histogram (summary) lines
- Includes rag_uptime_seconds metric
- No authentication required
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app import app

    return TestClient(app)


# ── Basic response validation ───────────────────────────────────────────────


class TestMetricsEndpointBasic:
    """GET /api/metrics returns 200 with valid Prometheus text."""

    def test_metrics_returns_200(self, client):
        """Metrics endpoint returns HTTP 200."""
        resp = client.get("/api/metrics")
        assert resp.status_code == 200

    def test_metrics_content_type_is_text_plain(self, client):
        """Response Content-Type should be text/plain; charset=utf-8."""
        resp = client.get("/api/metrics")
        content_type = resp.headers.get("content-type", "")
        assert "text/plain" in content_type

    def test_metrics_body_is_not_empty(self, client):
        """Response body should not be empty."""
        resp = client.get("/api/metrics")
        assert len(resp.text) > 0


# ── Prometheus text format validation ───────────────────────────────────────


class TestMetricsPrometheusFormat:
    """Validate that output conforms to Prometheus text exposition format."""

    def test_output_contains_type_declarations(self, client):
        """Each metric group should have a # TYPE declaration."""
        resp = client.get("/api/metrics")
        lines = resp.text.strip().splitlines()
        type_lines = [line for line in lines if line.startswith("# TYPE")]
        assert len(type_lines) > 0, "Expected at least one # TYPE declaration"

    def test_type_declarations_match_metric_lines(self, client):
        """Every # TYPE line should be followed by at least one metric line."""
        resp = client.get("/api/metrics")
        lines = resp.text.strip().splitlines()

        for i, line in enumerate(lines):
            if line.startswith("# TYPE"):
                # Format: "# TYPE rag_foo counter" -> parts[2] = "rag_foo"
                parts = line.split()
                assert len(parts) >= 3, f"Malformed TYPE line: {line}"
                metric_name = parts[2]
                # The next non-comment line should reference this metric
                found = False
                for j in range(i + 1, min(i + 5, len(lines))):
                    if lines[j].startswith("#"):
                        continue
                    if lines[j].startswith(metric_name):
                        found = True
                        break
                assert found, f"No metric value found after TYPE declaration for {metric_name}"

    def test_uptime_metric_always_present(self, client):
        """rag_uptime_seconds gauge should always be present."""
        resp = client.get("/api/metrics")
        assert "rag_uptime_seconds" in resp.text
        # Should be declared as gauge
        assert "# TYPE rag_uptime_seconds gauge" in resp.text

    def test_uptime_value_is_numeric(self, client):
        """rag_uptime_seconds value should be a positive float."""
        resp = client.get("/api/metrics")
        for line in resp.text.strip().splitlines():
            if line.startswith("rag_uptime_seconds ") and "quantile" not in line:
                value_str = line.split()[1]
                value = float(value_str)
                assert value >= 0.0, f"uptime should be >= 0, got {value}"
                return
        pytest.fail("rag_uptime_seconds metric line not found")


# ── Counter / gauge / summary format ────────────────────────────────────────


class TestMetricsMetricTypes:
    """Validate correct Prometheus metric type formatting."""

    def test_counter_metrics_have_correct_format(self, client):
        """Counter metrics should have TYPE ... counter and an integer value."""
        resp = client.get("/api/metrics")
        lines = resp.text.strip().splitlines()

        counter_names = set()
        for line in lines:
            if "# TYPE" in line and "counter" in line:
                parts = line.split()
                counter_names.add(parts[2])

        for name in counter_names:
            # Find the metric line (not a comment)
            for line in lines:
                if line.startswith(name + " ") and not line.startswith("#"):
                    value_str = line.split()[-1]
                    value = float(value_str)
                    assert value >= 0, f"Counter {name} should be >= 0, got {value}"
                    break

    def test_gauge_metrics_have_correct_format(self, client):
        """Gauge metrics should have TYPE ... gauge and a numeric value."""
        resp = client.get("/api/metrics")
        lines = resp.text.strip().splitlines()

        gauge_names = set()
        for line in lines:
            if "# TYPE" in line and "gauge" in line:
                parts = line.split()
                gauge_names.add(parts[2])

        assert len(gauge_names) > 0, "Expected at least one gauge metric"
        for name in gauge_names:
            for line in lines:
                if line.startswith(name + " ") and "quantile" not in line and not line.startswith("#"):
                    value_str = line.split()[-1]
                    float(value_str)  # should not raise
                    break

    def test_summary_metrics_have_quantiles(self, client):
        """Summary metrics (histograms exported as summary) should have quantile labels."""
        resp = client.get("/api/metrics")
        lines = resp.text.strip().splitlines()

        summary_names = set()
        for line in lines:
            if "# TYPE" in line and "summary" in line:
                parts = line.split()
                summary_names.add(parts[2])

        for name in summary_names:
            quantile_lines = [line for line in lines if name in line and "quantile=" in line]
            assert len(quantile_lines) > 0, f"Summary metric {name} should have quantile lines"

    def test_summary_has_count_line(self, client):
        """Each summary metric should have a _count line."""
        resp = client.get("/api/metrics")
        lines = resp.text.strip().splitlines()

        summary_base_names = set()
        for line in lines:
            if "# TYPE" in line and "summary" in line:
                parts = line.split()
                summary_base_names.add(parts[2])

        for name in summary_base_names:
            count_lines = [line for line in lines if line.startswith(name + "_count")]
            assert len(count_lines) > 0, f"Summary {name} should have a _count line"


# ── No auth required ────────────────────────────────────────────────────────


class TestMetricsNoAuth:
    """GET /api/metrics should work without any authentication headers."""

    def test_no_auth_headers_returns_200(self, client):
        """Request without any auth headers returns 200."""
        resp = client.get("/api/metrics")
        assert resp.status_code == 200

    def test_anonymous_user_can_access(self, client):
        """Anonymous user (no X-User-* headers) can read metrics."""
        resp = client.get("/api/metrics")
        assert resp.status_code == 200
        assert "rag_uptime_seconds" in resp.text


# ── Prometheus scrape compatibility ──────────────────────────────────────────


class TestMetricsScrapeCompatibility:
    """Ensure output is compatible with Prometheus scrape format."""

    def test_output_ends_with_newline(self, client):
        """Prometheus text format requires trailing newline."""
        resp = client.get("/api/metrics")
        assert resp.text.endswith("\n")

    def test_no_html_in_output(self, client):
        """Output should not contain HTML tags (pure text)."""
        resp = client.get("/api/metrics")
        assert "<" not in resp.text
        assert ">" not in resp.text

    def test_metric_values_are_numeric(self, client):
        """All metric values (after the metric name) should be parseable as float."""
        resp = client.get("/api/metrics")
        for line in resp.text.strip().splitlines():
            if line.startswith("#") or not line.strip():
                continue
            # Lines with quantiles: 'rag_foo_seconds{quantile="0.5"} 0.001'
            # Plain lines: 'rag_foo 42'
            parts = line.split()
            if len(parts) < 2:
                continue
            value_str = parts[-1]
            try:
                float(value_str)
            except ValueError:
                pytest.fail(f"Non-numeric value in metric line: {line}")
