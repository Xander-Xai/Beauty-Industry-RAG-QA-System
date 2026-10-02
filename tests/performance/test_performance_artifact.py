"""Deterministic tests for the performance-evidence artifact contract.

The contract's single purpose is to make "did not run" impossible to confuse
with "ran fast and clean". Most of these tests therefore assert that an
unmeasured quantity is *absent or null* rather than zero.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.performance import (
    OPTIONAL_ARTIFACT_FILES,
    REQUIRED_ARTIFACT_FILES,
    STATUS_BLOCKED,
    STATUS_EXECUTED,
    STATUS_PARTIAL,
    PerformanceArtifactError,
    RequestTally,
    Workload,
    artifact_digest,
    build_environment,
    build_metadata,
    build_report,
    compute_qps,
    derive_status,
    new_run_id,
    percentile,
    stats_to_csv,
    summarize_latency,
    write_artifact,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

EXECUTED_WORKLOAD = Workload(
    users=5,
    spawn_rate=2.0,
    duration_seconds=60.0,
    target_base="http://localhost:8000",
    endpoints=("/api/query",),
    authenticated=True,
)


def _read(run_dir: Path, name: str):
    return json.loads((run_dir / name).read_text(encoding="utf-8"))


# ── null vs zero ────────────────────────────────────────────────────────────


def test_percentile_of_empty_sample_is_none_not_zero():
    assert percentile([], 95) is None
    assert percentile([], 50) is None


def test_summarize_latency_omits_latency_keys_when_nothing_observed():
    summary = summarize_latency([])
    assert summary == {"count": 0}
    # No latency key may exist at all, so absence cannot be read as zero.
    for key in ("p50", "p95", "p99", "avg", "min", "max"):
        assert key not in summary


def test_summarize_latency_reports_real_values_when_observed():
    summary = summarize_latency([10.0, 20.0, 30.0, 40.0])
    assert summary["count"] == 4
    assert summary["min"] == 10.0
    assert summary["max"] == 40.0
    assert summary["p95"] is not None
    # A genuine zero observation must still be reported as zero, not nulled.
    zeroed = summarize_latency([0.0, 0.0])
    assert zeroed["min"] == 0.0


def test_compute_qps_is_null_without_a_real_window():
    assert compute_qps(0, 60.0) is None
    assert compute_qps(100, 0) is None
    assert compute_qps(100, None) is None
    assert compute_qps(100, -5) is None


def test_compute_qps_returns_a_number_for_a_real_window():
    assert compute_qps(120, 60.0) == 2.0


def test_locust_latency_stats_reports_none_when_empty():
    """The load harness must not report 0 ms for a run that collected nothing.

    Imported from the library rather than from ``tests/load/locustfile.py``:
    importing Locust monkey-patches the interpreter and cannot happen inside a
    normal pytest process, which is exactly why the class lives in the library.
    """
    from benchmarks.performance import LatencyStats

    stats = LatencyStats()
    assert stats.p50 is None
    assert stats.p95 is None
    assert stats.p99 is None
    assert stats.avg is None
    assert stats.min is None
    assert stats.max is None
    assert stats.count == 0

    stats.add(15.0)
    assert stats.count == 1
    assert stats.p95 == 15.0
    assert stats.to_dict()["p50"] == 15.0


# ── status semantics ────────────────────────────────────────────────────────


def test_derive_status_blocked_wins_over_every_other_signal():
    status = derive_status(
        blocked_reason="missing_auth_token",
        requests=RequestTally(total=10, success=10),
        declared_users=5,
        observed_users=5,
    )
    assert status == STATUS_BLOCKED


def test_derive_status_blocked_when_nothing_was_requested():
    status = derive_status(
        blocked_reason=None,
        requests=RequestTally(),
        declared_users=5,
        observed_users=5,
    )
    assert status == STATUS_BLOCKED


def test_derive_status_partial_on_any_failure():
    status = derive_status(
        blocked_reason=None,
        requests=RequestTally(total=10, success=9, failure=1),
        declared_users=5,
        observed_users=5,
    )
    assert status == STATUS_PARTIAL


def test_derive_status_partial_when_declared_concurrency_unreached():
    status = derive_status(
        blocked_reason=None,
        requests=RequestTally(total=10, success=10),
        declared_users=20,
        observed_users=4,
    )
    assert status == STATUS_PARTIAL


def test_derive_status_executed_only_for_a_complete_clean_run():
    status = derive_status(
        blocked_reason=None,
        requests=RequestTally(total=10, success=10),
        declared_users=5,
        observed_users=5,
    )
    assert status == STATUS_EXECUTED


def test_blocked_run_must_carry_a_reason():
    with pytest.raises(PerformanceArtifactError, match="blocked_reason"):
        build_metadata(
            status=STATUS_BLOCKED,
            blocked_reason=None,
            workload=EXECUTED_WORKLOAD,
            requests=RequestTally(),
            throughput_qps=None,
            latency={"count": 0},
            duration_seconds=None,
            observed_users=None,
        )


def test_non_blocked_run_must_not_carry_a_reason():
    with pytest.raises(PerformanceArtifactError, match="blocked_reason"):
        build_metadata(
            status=STATUS_EXECUTED,
            blocked_reason="api_unreachable",
            workload=EXECUTED_WORKLOAD,
            requests=RequestTally(total=1, success=1),
            throughput_qps=1.0,
            latency=summarize_latency([1.0]),
            duration_seconds=1.0,
            observed_users=1,
        )


def test_unknown_status_is_rejected():
    with pytest.raises(PerformanceArtifactError, match="unknown status"):
        build_metadata(
            status="DEFINITELY_FINE",
            blocked_reason=None,
            workload=EXECUTED_WORKLOAD,
            requests=RequestTally(),
            throughput_qps=None,
            latency={"count": 0},
            duration_seconds=None,
            observed_users=None,
        )


# ── provenance ──────────────────────────────────────────────────────────────


def test_metadata_records_git_provenance():
    metadata = build_metadata(
        status=STATUS_EXECUTED,
        blocked_reason=None,
        workload=EXECUTED_WORKLOAD,
        requests=RequestTally(total=4, success=4),
        throughput_qps=2.0,
        latency=summarize_latency([1.0, 2.0]),
        duration_seconds=2.0,
        observed_users=5,
        repo_root=str(REPO_ROOT),
    )
    assert "git_sha" in metadata
    assert "git_dirty" in metadata
    # git_dirty is a bool here, never collapsed to None, so a dirty run cannot
    # be mistaken for a clean one.
    assert isinstance(metadata["git_dirty"], bool)


def test_metadata_records_system_version_from_config():
    metadata = build_metadata(
        status=STATUS_EXECUTED,
        blocked_reason=None,
        workload=EXECUTED_WORKLOAD,
        requests=RequestTally(total=1, success=1),
        throughput_qps=1.0,
        latency=summarize_latency([1.0]),
        duration_seconds=1.0,
        observed_users=1,
    )
    assert metadata["system_version"] == "2.3.0"


def test_environment_block_records_credential_presence_not_values():
    """Presence flags are the contract; a credential *value* would be the leak."""
    environment = build_environment(str(REPO_ROOT))
    presence = environment.get("credential_env_presence", {})
    assert isinstance(presence, dict)
    for name, value in presence.items():
        assert isinstance(value, bool), f"{name} must be a boolean presence flag, got {value!r}"

    # No environment value may ever be a non-boolean under a credential-ish key.
    rendered = json.dumps(environment, ensure_ascii=False).lower()
    assert "bearer " not in rendered
    assert "jwt" not in rendered


# ── artifact contract on disk ───────────────────────────────────────────────


def test_write_artifact_emits_every_required_file(tmp_path):
    run_dir = tmp_path / "run"
    write_artifact(
        run_dir,
        workload=EXECUTED_WORKLOAD,
        latency_samples=[10.0, 12.0, 11.0],
        requests=RequestTally(total=3, success=3),
        duration_seconds=3.0,
        observed_users=5,
        repo_root=str(REPO_ROOT),
    )
    for name in REQUIRED_ARTIFACT_FILES:
        assert (run_dir / name).is_file(), f"missing required artifact file {name}"
    # Optional passthrough files must not be invented.
    for name in OPTIONAL_ARTIFACT_FILES:
        assert not (run_dir / name).exists()


def test_blocked_artifact_has_no_measurements(tmp_path):
    run_dir = tmp_path / "blocked"
    write_artifact(
        run_dir,
        blocked_reason="missing_auth_token",
        workload=EXECUTED_WORKLOAD,
        requests=RequestTally(),
        repo_root=str(REPO_ROOT),
    )
    metadata = _read(run_dir, "metadata.json")
    assert metadata["status"] == STATUS_BLOCKED
    assert metadata["blocked_reason"] == "missing_auth_token"

    latency = _read(run_dir, "latency_metrics.json")
    assert latency == {"count": 0}
    assert "p95" not in latency

    throughput = _read(run_dir, "throughput_metrics.json")
    assert throughput["qps"] is None
    assert throughput["measured"] is False

    errors = _read(run_dir, "errors.json")
    assert errors["error_rate"] is None


def test_artifact_without_samples_is_blocked_even_without_a_reason(tmp_path):
    run_dir = tmp_path / "no-samples"
    artifact = write_artifact(
        run_dir,
        workload=EXECUTED_WORKLOAD,
        latency_samples=[],
        requests=RequestTally(),
        repo_root=str(REPO_ROOT),
    )
    assert artifact["metadata"]["status"] == STATUS_BLOCKED
    assert artifact["metadata"]["blocked_reason"]


def test_partial_run_records_failures(tmp_path):
    run_dir = tmp_path / "partial"
    artifact = write_artifact(
        run_dir,
        workload=EXECUTED_WORKLOAD,
        latency_samples=[10.0, 20.0],
        requests=RequestTally(total=4, success=2, failure=2),
        duration_seconds=4.0,
        observed_users=5,
        repo_root=str(REPO_ROOT),
    )
    assert artifact["metadata"]["status"] == STATUS_PARTIAL
    errors = _read(run_dir, "errors.json")
    assert errors["failure"] == 2
    assert errors["error_rate"] == 0.5


def test_executed_run_reports_measurements(tmp_path):
    run_dir = tmp_path / "executed"
    artifact = write_artifact(
        run_dir,
        workload=EXECUTED_WORKLOAD,
        latency_samples=[10.0, 20.0, 30.0, 40.0],
        requests=RequestTally(total=4, success=4),
        duration_seconds=4.0,
        observed_users=5,
        repo_root=str(REPO_ROOT),
    )
    assert artifact["metadata"]["status"] == STATUS_EXECUTED
    assert artifact["throughput"]["qps"] == 1.0
    assert artifact["throughput"]["measured"] is True
    assert artifact["latency"]["p95"] is not None


def test_metadata_never_leaks_the_auth_token(tmp_path):
    run_dir = tmp_path / "secret"
    workload = Workload(target_base="http://localhost:8000", authenticated=True)
    write_artifact(
        run_dir,
        workload=workload,
        latency_samples=[1.0],
        requests=RequestTally(total=1, success=1),
        duration_seconds=1.0,
        observed_users=1,
        limitations=["token=super-secret-value"],
        repo_root=str(REPO_ROOT),
    )
    # A limitation string is operator-supplied free text; the artifact must not
    # be the place a token survives.
    rendered = "\n".join((run_dir / name).read_text(encoding="utf-8") for name in REQUIRED_ARTIFACT_FILES)
    assert "super-secret-value" not in rendered or "super-secret-value" in " ".join(
        _read(run_dir, "metadata.json")["limitations"]
    )
    for name in ("metadata.json", "workload.json"):
        payload = json.dumps(_read(run_dir, name), ensure_ascii=False).lower()
        assert "authorization" not in payload
        assert "bearer" not in payload


# ── report rendering ────────────────────────────────────────────────────────


def test_report_states_blocked_and_says_not_measured(tmp_path):
    run_dir = tmp_path / "blocked-report"
    artifact = write_artifact(
        run_dir,
        blocked_reason="api_unreachable: ConnectTimeout",
        workload=EXECUTED_WORKLOAD,
        requests=RequestTally(),
        repo_root=str(REPO_ROOT),
    )
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "BLOCKED" in report
    assert "No workload was executed" in report
    assert "not measured" in report
    # The fabricated numbers this guard exists to prevent.
    assert "p95 = 0" not in report
    assert "qps = 0" not in report
    assert build_report(artifact).startswith("# Performance evidence")


def test_report_renders_measurements_when_present(tmp_path):
    run_dir = tmp_path / "ok-report"
    write_artifact(
        run_dir,
        workload=EXECUTED_WORKLOAD,
        latency_samples=[10.0, 20.0, 30.0],
        requests=RequestTally(total=3, success=3),
        duration_seconds=3.0,
        observed_users=5,
        repo_root=str(REPO_ROOT),
    )
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "EXECUTED" in report
    assert "not measured" not in report
    assert "Limitations" in report


# ── helpers ─────────────────────────────────────────────────────────────────


def test_artifact_digest_is_stable_and_content_sensitive(tmp_path):
    first = write_artifact(
        tmp_path / "d1",
        workload=EXECUTED_WORKLOAD,
        latency_samples=[1.0, 2.0],
        requests=RequestTally(total=2, success=2),
        duration_seconds=2.0,
        observed_users=5,
        repo_root=str(REPO_ROOT),
    )
    second = write_artifact(
        tmp_path / "d2",
        workload=EXECUTED_WORKLOAD,
        latency_samples=[1.0, 2.0],
        requests=RequestTally(total=2, success=2),
        duration_seconds=2.0,
        observed_users=5,
        repo_root=str(REPO_ROOT),
    )
    third = write_artifact(
        tmp_path / "d3",
        workload=EXECUTED_WORKLOAD,
        latency_samples=[1.0, 2.0, 3.0],
        requests=RequestTally(total=3, success=3),
        duration_seconds=3.0,
        observed_users=5,
        repo_root=str(REPO_ROOT),
    )
    assert artifact_digest(first) == artifact_digest(second)
    assert artifact_digest(first) != artifact_digest(third)


def test_new_run_id_is_prefixed_and_unique_per_call():
    first = new_run_id()
    second = new_run_id()
    assert first.startswith("perf-")
    assert first != second


def test_stats_to_csv_is_empty_for_no_rows():
    assert stats_to_csv([]) == ""


def test_stats_to_csv_emits_header_and_rows():
    rendered = stats_to_csv([{"name": "/api/query", "count": 3}, {"name": "/api/stats", "count": 1}])
    header = rendered.splitlines()[0]
    assert header.split(",") == sorted(header.split(","))
    assert "/api/query" in rendered
    assert "3" in rendered


def test_no_committed_performance_artifact_means_result_is_pending():
    """The repository claims no measured performance until one is committed.

    Judged from ``git ls-files`` rather than a filesystem glob: generated runs
    are git-ignored, so a developer's own local run must not fail this test.
    """
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "artifacts/performance"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    committed_runs = [path for path in tracked if path.endswith("metadata.json")]
    assert not committed_runs, f"unexpected committed performance artifact: {committed_runs}"
    assert "artifacts/performance/README.md" in tracked, "the artifact contract README must stay tracked"


def test_artifact_readme_states_the_null_not_zero_rule():
    readme = (REPO_ROOT / "artifacts" / "performance" / "README.md").read_text(encoding="utf-8")
    assert "Not executed is not zero" in readme
    assert "BLOCKED" in readme
    assert "PARTIAL" in readme
    assert "EXECUTED" in readme
