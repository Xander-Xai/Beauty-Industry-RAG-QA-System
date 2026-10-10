"""The rerank validation entry point must never manufacture evidence.

These tests are about *honesty of reporting*, not about ranking quality — the
weights are absent in this repository, so the ranking itself cannot be measured
here. What can be tested is that the harness:

* reports ``PENDING`` rather than a number when the weights are missing;
* labels the deterministic fallback as not CrossEncoder inference;
* refuses to call a fallback run reranking evidence;
* never imports a weight-downloading call;
* records provenance (git SHA, hardware, degradation) on every branch — including
  the PENDING branch, which is the only branch this repository takes.
"""

from __future__ import annotations

import json

import pytest

from retrieval import rerank_validation as rv


@pytest.fixture(scope="module")
def weights_status():
    from retrieval.rerank_status import rerank_weights_status

    return rerank_weights_status()


# ── the current, honest state of this repository ────────────────────────────


def test_missing_weights_are_pending_not_failed(weights_status):
    """The expected state here is an absent asset, not a broken configuration."""
    assert weights_status["status"] in ("AVAILABLE", "BLOCKED")
    if weights_status["status"] == "BLOCKED":
        assert weights_status["reason"], "a blocked state must name the missing asset"


def test_smoke_is_pending_while_the_weights_are_absent(weights_status):
    report = rv.run_smoke()
    if weights_status["status"] == "BLOCKED":
        assert report.status == "PENDING"
        assert report.provenance == rv.PROVENANCE_FALLBACK
        assert report.reason
    else:  # pragma: no cover - only on a host with the weights installed
        assert report.status in ("OK", "FAILED")


# ── provenance must survive every branch ───────────────────────────────────


def test_pending_report_still_carries_provenance():
    """Regression: the PENDING branch once returned a report with no git SHA.

    Provenance was only attached inside the smoke sub-dict, so the artifact for
    the state this repository is actually in had `git_sha: null` — exactly the
    report nobody could reproduce.
    """
    report = rv.run_smoke()
    assert report.git_sha is not None, "a report must record which commit produced it"
    assert isinstance(report.git_dirty, bool)
    assert "cuda_available" in report.hardware
    assert report.pair_count == len(rv.SMOKE_PAIRS)


def test_comparison_report_carries_provenance():
    report = rv.run_comparison()
    assert report.git_sha is not None
    assert report.hardware.get("cuda_available") is not None


# ── the fallback must never read as reranking evidence ─────────────────────


def test_fallback_report_is_never_reranking_evidence(weights_status):
    if weights_status["status"] == "AVAILABLE":  # pragma: no cover
        pytest.skip("weights present; the fallback path is not what runs here")
    for report in (rv.run_smoke(), rv.run_comparison()):
        assert report.is_reranking_evidence is False


def test_reranking_evidence_requires_real_cross_encoder():
    """The predicate itself, independent of this host's state."""
    fake_ok_fallback = rv.ValidationReport(
        status="OK", reason="", provenance=rv.PROVENANCE_FALLBACK, metrics={"x": 1.0}
    )
    fake_pending_real = rv.ValidationReport(status="PENDING", reason="", provenance=rv.PROVENANCE_CROSS_ENCODER)
    real = rv.ValidationReport(status="OK", reason="", provenance=rv.PROVENANCE_CROSS_ENCODER)
    assert fake_ok_fallback.is_reranking_evidence is False, "status=OK must not be enough"
    assert fake_pending_real.is_reranking_evidence is False, "provenance alone must not be enough"
    assert real.is_reranking_evidence is True


def test_comparison_computes_no_delta_without_real_inference(weights_status):
    """A fallback-vs-fallback delta would be a fabricated improvement number."""
    if weights_status["status"] == "AVAILABLE":  # pragma: no cover
        pytest.skip("weights present")
    report = rv.run_comparison()
    assert report.comparison["cross_encoder"] is None
    assert "top1_rate_delta" not in report.comparison
    assert report.metrics == {}


def test_fallback_side_is_labelled_as_degraded_not_reranking():
    report = rv.run_comparison()
    fallback = report.comparison["fallback"]
    assert fallback["provenance"] == rv.PROVENANCE_FALLBACK
    assert "not CrossEncoder inference" in fallback["note"]


# ── no silent weight acquisition ────────────────────────────────────────────


def test_module_never_imports_a_downloader():
    """A smoke test that fetches gigabytes as a side effect must not exist.

    Checked on the source rather than by mocking: the constraint is that the
    code contains no such call at all.
    """
    source = rv.__file__ or ""
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    for forbidden in (
        "snapshot_download",
        "hf_hub_download",
        "huggingface_hub",
        "requests.get('http",  # noqa: S310
        "urlretrieve",
        "wget ",
        "git clone",
    ):
        assert forbidden not in text, f"{forbidden!r} would download an asset as a side effect"


def test_load_only_touches_configured_local_paths(monkeypatch):
    """`model_revision` must report None for a path that is not there, not guess."""
    assert rv.model_revision(rv.PROJECT_ROOT / "models" / "does-not-exist") is None


# ── artifact contract ───────────────────────────────────────────────────────


def test_written_report_states_the_evidence_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(rv, "DEFAULT_REPORT_DIR", str(tmp_path))
    report = rv.run_smoke()
    path = rv.write_report(report, str(tmp_path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == report.status
    assert payload["provenance"] == report.provenance
    assert "is_reranking_evidence" in payload
    assert "requires provenance=cross_encoder" in payload["artifact_contract"]
    # git SHA must be present in the persisted artifact, not only in memory.
    assert payload["git_sha"] is not None


def test_report_is_json_serialisable():
    json.dumps(rv.run_comparison().as_dict(), ensure_ascii=False)


# ── CLI contract ────────────────────────────────────────────────────────────


def test_require_real_exits_pending_when_unavailable(tmp_path, capsys):
    from retrieval.rerank_status import rerank_weights_status

    if rerank_weights_status()["status"] == "AVAILABLE":  # pragma: no cover
        pytest.skip("weights present")
    code = rv.main(["--smoke", "--require-real", "--report-dir", str(tmp_path)])
    assert code == rv.EXIT_PENDING
    out = capsys.readouterr().out
    assert "NOT reranking evidence" in out
    assert "PENDING" in out


def test_smoke_cli_succeeds_when_pending(tmp_path):
    """Without --require-real, a PENDING asset is not a build failure."""
    assert rv.main(["--smoke", "--report-dir", str(tmp_path)]) == rv.EXIT_OK


def test_hardware_reports_cuda_only_when_actually_visible():
    info = rv.hardware_info()
    assert isinstance(info["cuda_available"], bool)
    if not info["cuda_available"]:
        assert info["gpus"] == []
    else:  # pragma: no cover - depends on host
        assert info["gpus"]


def test_percentile_handles_small_inputs():
    assert rv._percentile([], 50) is None
    assert rv._percentile([1.0], 50) == 1.0
    assert rv._percentile([1.0, 3.0], 50) == 2.0
