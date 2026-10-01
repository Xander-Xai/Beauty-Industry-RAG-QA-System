"""
Tests for GET /api/metrics endpoint (GAP-18)

Covers:
- Returns 200 with Content-Type text/plain (authenticated)
- Output matches Prometheus text exposition format
- Contains counter, gauge, and histogram (summary) lines
- Includes rag_uptime_seconds metric
- Authentication is required (returns 401 without auth)
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


@pytest.fixture(scope="module")
def auth_header():
    """Generate a JWT auth header for authenticated requests."""
    import time
    import jwt as _jwt
    os.environ["JWT_SECRET"] = "test-secret-for-unit-tests-only"
    payload = {
        "sub": "metrics_test_user",
        "user_id": "metrics_test_user",
        "role_mask": 0x01,
        "dept_mask": 0x01,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    token = _jwt.encode(payload, "test-secret-for-unit-tests-only", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


# ── Basic response validation ───────────────────────────────────────────────


class TestMetricsEndpointBasic:
    """GET /api/metrics returns 200 with valid Prometheus text."""

    def test_metrics_returns_200(self, client, auth_header):
        """Metrics endpoint returns HTTP 200 when authenticated."""
        resp = client.get("/api/metrics", headers=auth_header)
        assert resp.status_code == 200

    def test_metrics_content_type_is_text_plain(self, client, auth_header):
        """Response Content-Type should be text/plain; charset=utf-8."""
        resp = client.get("/api/metrics", headers=auth_header)
        content_type = resp.headers.get("content-type", "")
        assert "text/plain" in content_type

    def test_metrics_body_is_not_empty(self, client, auth_header):
        """Response body should not be empty."""
        resp = client.get("/api/metrics", headers=auth_header)
        assert len(resp.text) > 0


# ── Prometheus text format validation ───────────────────────────────────────


class TestMetricsPrometheusFormat:
    """Validate that output conforms to Prometheus text exposition format."""

    def test_output_contains_type_declarations(self, client, auth_header):
        """Each metric group should have a # TYPE declaration."""
        resp = client.get("/api/metrics", headers=auth_header)
        lines = resp.text.strip().splitlines()
        type_lines = [l for l in lines if l.startswith("# TYPE")]
        assert len(type_lines) > 0, "Expected at least one # TYPE declaration"

    def test_type_declarations_match_metric_lines(self, client, auth_header):
        """Every # TYPE line should be followed by at least one metric line."""
        resp = client.get("/api/metrics", headers=auth_header)
        lines = resp.text.strip().splitlines()

        for i, line in enumerate(lines):
            if line.startswith("# TYPE"):
                parts = line.split()
                assert len(parts) >= 3, f"Malformed TYPE line: {line}"
                metric_name = parts[2]
                found = False
                for j in range(i + 1, min(i + 5, len(lines))):
                    if lines[j].startswith("#"):
                        continue
                    if lines[j].startswith(metric_name):
                        found = True
                        break
                assert found, f"No metric value found after TYPE declaration for {metric_name}"

    def test_uptime_metric_always_present(self, client, auth_header):
        """rag_uptime_seconds gauge should always be present."""
        resp = client.get("/api/metrics", headers=auth_header)
        assert "rag_uptime_seconds" in resp.text
        assert "# TYPE rag_uptime_seconds gauge" in resp.text

    def test_uptime_value_is_numeric(self, client, auth_header):
        """rag_uptime_seconds value should be a positive float."""
        resp = client.get("/api/metrics", headers=auth_header)
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

    def test_counter_metrics_have_correct_format(self, client, auth_header):
        """Counter metrics should have TYPE ... counter and an integer value."""
        resp = client.get("/api/metrics", headers=auth_header)
        lines = resp.text.strip().splitlines()

        counter_names = set()
        for line in lines:
            if "# TYPE" in line and "counter" in line:
                parts = line.split()
                counter_names.add(parts[2])

        for name in counter_names:
            for line in lines:
                if line.startswith(name + " ") and not line.startswith("#"):
                    value_str = line.split()[-1]
                    value = float(value_str)
                    assert value >= 0, f"Counter {name} should be >= 0, got {value}"
                    break

    def test_gauge_metrics_have_correct_format(self, client, auth_header):
        """Gauge metrics should have TYPE ... gauge and a numeric value."""
        resp = client.get("/api/metrics", headers=auth_header)
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
                    float(value_str)
                    break

    def test_summary_metrics_have_quantiles(self, client, auth_header):
        """Summary metrics should have quantile labels."""
        resp = client.get("/api/metrics", headers=auth_header)
        lines = resp.text.strip().splitlines()

        summary_names = set()
        for line in lines:
            if "# TYPE" in line and "summary" in line:
                parts = line.split()
                summary_names.add(parts[2])

        for name in summary_names:
            quantile_lines = [l for l in lines if name in l and 'quantile=' in l]
            assert len(quantile_lines) > 0, (
                f"Summary metric {name} should have quantile lines"
            )

    def test_summary_has_count_line(self, client, auth_header):
        """Each summary metric should have a _count line."""
        resp = client.get("/api/metrics", headers=auth_header)
        lines = resp.text.strip().splitlines()

        summary_base_names = set()
        for line in lines:
            if "# TYPE" in line and "summary" in line:
                parts = line.split()
                summary_base_names.add(parts[2])

        for name in summary_base_names:
            count_lines = [l for l in lines if l.startswith(name + "_count")]
            assert len(count_lines) > 0, (
                f"Summary {name} should have a _count line"
            )


# ── Auth enforcement ────────────────────────────────────────────────────────


class TestMetricsAuthRequired:
    """GET /api/metrics now requires authentication."""

    def test_no_auth_headers_returns_401(self, client):
        """Request without any auth headers returns 401."""
        resp = client.get("/api/metrics")
        assert resp.status_code == 401

    def test_authenticated_user_can_access(self, client, auth_header):
        """Authenticated user can read metrics."""
        resp = client.get("/api/metrics", headers=auth_header)
        assert resp.status_code == 200
        assert "rag_uptime_seconds" in resp.text


# ── Prometheus scrape compatibility ──────────────────────────────────────────


class TestMetricsScrapeCompatibility:
    """Ensure output is compatible with Prometheus scrape format."""

    def test_output_ends_with_newline(self, client, auth_header):
        """Prometheus text format requires trailing newline."""
        resp = client.get("/api/metrics", headers=auth_header)
        assert resp.text.endswith("\n")

    def test_no_html_in_output(self, client, auth_header):
        """Output should not contain HTML tags."""
        resp = client.get("/api/metrics", headers=auth_header)
        assert "<" not in resp.text
        assert ">" not in resp.text

    def test_metric_values_are_numeric(self, client, auth_header):
        """All metric values should be parseable as float."""
        resp = client.get("/api/metrics", headers=auth_header)
        for line in resp.text.strip().splitlines():
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            value_str = parts[-1]
            try:
                float(value_str)
            except ValueError:
                pytest.fail(f"Non-numeric value in metric line: {line}")
