"""The GPU topology gate must report reality and never invent a number.

`router/gpu_gate.py` exists because `config.json` declaring a two-GPU
Qwen3-14B/4B topology is a *plan*, and the failure this guards against is
treating that plan as a deployment. The dangerous artefact is a throughput or
latency figure, so the tests below check two things separately:

* the gate reports the endpoints, weights and visible GPUs it actually found;
* it is **structurally incapable** of emitting a GPU performance number, so
  there is nothing to fabricate even by accident.

Everything runs read-only and offline: the endpoint probe is monkeypatched to a
fake, and the real network is never touched.
"""

from __future__ import annotations

import json

import pytest

from router import gpu_gate as gg


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Keep the gate offline without disabling the function under test.

    `urlopen` is stubbed rather than `_probe_endpoint`, so the probing logic —
    including its HTTPError branch — is still exercised for real.
    """
    import urllib.error

    def refuse(url, timeout=None):
        raise urllib.error.URLError("stubbed offline")

    monkeypatch.setattr(gg.urllib.request, "urlopen", refuse)


CONFIG = {
    "deployment_mode": "development",
    "model_routing": {
        "tiers": {"complex": {"endpoint": "gen_14b"}, "simple": {"endpoint": "gen_4b"}},
        "complexity_fallback": "simple",
    },
    "gpu0": {"models": {"gen_14b": {"port": 8100, "model_path": "./models/Qwen3-14B-Instruct"}}},
    "gpu1": {"models": {"vllm_4b": {"port": 8101, "model_path": "./models/Qwen3-4B-Instruct"}}},
}


# ── the module cannot produce a performance number ──────────────────────────


def test_gate_reports_gpu_metrics_as_never_measured():
    """Structural, not incidental: the field exists and is False.

    A reader could otherwise assume the gate measured throughput. There is no
    code path in this module that can set it True, so no number can be invented.
    """
    report = gg.gpu_gate_status(CONFIG)
    assert report.gpu_metrics_measured is False


def test_gate_source_contains_no_throughput_measurement():
    """The absence of a measurement is enforced on the source.

    If someone later adds a latency or QPS probe here, this fails — the module
    is a presence gate, and a real load run belongs in
    `benchmarks/performance.py` with its own artifact contract.
    """
    source = open(gg.__file__, encoding="utf-8").read()
    # Check for measurement *constructs*, not for the word "QPS" — the module
    # deliberately mentions the concept in its docstring to explain why it is
    # absent, and a keyword scan would flag that disclaimer instead.
    for construct in ("perf_counter", "time.monotonic", "time.time()", "statistics.", "percentile(", "locust"):
        assert construct not in source, f"{construct!r} would imply a measurement this gate cannot make"


def test_report_has_no_performance_fields():
    payload = gg.gpu_gate_status(CONFIG).as_dict()
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    for forbidden in ("qps", "p95", "latency_ms", "throughput_rps"):
        assert forbidden not in serialized


# ── what the gate actually reports ──────────────────────────────────────────


def test_absent_weights_and_endpoints_produce_pending_with_reasons():
    report = gg.gpu_gate_status(CONFIG)
    assert report.status == gg.STATUS_PENDING
    assert report.reasons, "a pending gate must name what is missing"
    joined = " ".join(report.reasons).lower()
    assert "weights" in joined
    assert "unreachable" in joined


def test_every_missing_asset_is_named_individually():
    report = gg.gpu_gate_status(CONFIG)
    joined = " ".join(report.reasons)
    assert "Qwen3-14B" in joined
    assert "Qwen3-4B" in joined


def test_exit_code_is_pending_not_failed_for_an_absent_asset():
    report = gg.gpu_gate_status(CONFIG)
    assert report.exit_code == gg.EXIT_PENDING
    assert report.exit_code != gg.EXIT_FAILED


def test_require_live_returns_nonzero_when_nothing_serves():
    assert gg.main(["--require-live", "--json"]) == gg.EXIT_PENDING


def test_json_output_is_serialisable_and_carries_no_performance_number():
    assert json.dumps(gg.gpu_gate_status(CONFIG).as_dict(), ensure_ascii=False)


# ── routing truth ───────────────────────────────────────────────────────────


def test_development_mode_is_reported_as_not_live_for_the_complex_tier(monkeypatch):
    """The silent downgrade: config says 14B, runtime calls 4B.

    `models/llm_client.py:220-234` maps the complex tier to the 14B endpoint but
    downgrades to simple outside production, so the 14B path is never called.
    Reporting only the configuration would overstate what runs.
    """
    monkeypatch.setattr("common.config.is_production_mode", lambda *a, **k: False, raising=False)
    report = gg.gpu_gate_status(CONFIG)
    assert report.routing["configured_complex_endpoint"] == "gen_14b"
    assert report.routing["complex_tier_reachable_at_runtime"] is False
    assert any("downgraded" in reason for reason in report.reasons)


def test_topology_comparison_names_the_gpu_shortfall(monkeypatch):
    """A configured two-GPU topology on a one-GPU host must be called out."""
    monkeypatch.setattr(
        gg,
        "gpu_topology_status",
        lambda config=None: gg.GpuTopologyStatus(
            assumed_gpu_count=2,
            observed_gpu_count=1,
            observed=[{"index": 0, "name": "NVIDIA GeForce RTX 5060 Ti", "total_memory_bytes": 17179869184}],
            torch_available=True,
            cuda_available=True,
            detail="config declares 2 GPU roles but 1 device(s) are visible",
        ),
    )
    report = gg.gpu_gate_status(CONFIG)
    assert report.topology.assumed_gpu_count == 2
    assert report.topology.observed_gpu_count == 1
    assert any("2 GPU roles" in reason for reason in report.reasons)


def test_a_live_single_gpu_host_is_still_not_the_configured_topology(monkeypatch):
    """One visible GPU cannot validate a two-GPU deployment, even if healthy."""
    monkeypatch.setattr(
        gg,
        "gpu_topology_status",
        lambda config=None: gg.GpuTopologyStatus(
            assumed_gpu_count=2,
            observed_gpu_count=1,
            observed=[{"index": 0, "name": "GPU", "total_memory_bytes": 1}],
            torch_available=True,
            cuda_available=True,
            detail="shortfall",
        ),
    )
    report = gg.gpu_gate_status(CONFIG)
    assert "shortfall" in " ".join(report.reasons)


# ── endpoint probing ────────────────────────────────────────────────────────


def test_http_error_still_counts_as_a_serving_endpoint(monkeypatch):
    """A 503 from vLLM during startup proves the port is live."""
    import urllib.error

    def fake(url, timeout=None):
        raise urllib.error.HTTPError(url, 503, "loading", {}, None)

    monkeypatch.setattr(gg.urllib.request, "urlopen", fake)
    reachable, status, detail = gg._probe_endpoint("http://localhost:8100")
    assert reachable is True
    assert status == 503


def test_probe_returns_false_for_an_unconfigured_url():
    reachable, status, detail = gg._probe_endpoint("")
    assert reachable is False
    assert "no URL configured" in detail


def test_weights_resolution_reports_absence_rather_than_guessing():
    present, detail = gg._resolve_weights(None)
    assert present is False
    assert "no model_path" in detail

    present, detail = gg._resolve_weights("./models/definitely-not-here")
    assert present is False
    assert "absent" in detail


def test_endpoint_urls_honour_environment_overrides(monkeypatch):
    monkeypatch.setenv("VLLM_4B_URL", "http://gpu-box:9999/v1")
    config = {
        "gpu1": {"models": {"vllm_4b": {"port": 8101, "model_path": None}}},
        "gpu0": {"models": {}},
    }
    assert gg._endpoint_url(config, "gpu1", "vllm_4b") == "http://gpu-box:9999/v1"
