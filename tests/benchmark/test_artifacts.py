"""Provenance, artifact contract and failure handling."""

from __future__ import annotations

import json

from benchmarks.dataset import sha256_file
from benchmarks.provenance import (
    CREDENTIAL_ENV_NAMES,
    collect_environment,
    credential_env_state,
    render_environment,
    sha256_json,
)
from benchmarks.report import REQUIRED_ARTIFACT_FILES, build_report, verify_artifact_set, write_artifacts


def test_dataset_sha256_is_stable(mini_dataset):
    first = sha256_file(mini_dataset)
    second = sha256_file(mini_dataset)
    assert first == second
    assert len(first) == 64


def test_config_sha256_is_order_independent():
    assert sha256_json({"a": 1, "b": 2}) == sha256_json({"b": 2, "a": 1})


def test_environment_never_exposes_secret_values(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-value")
    rendered = render_environment(collect_environment())
    state = credential_env_state()
    assert set(state) == set(CREDENTIAL_ENV_NAMES)
    # only presence is reported
    assert state["OPENAI_API_KEY"] == "set"
    assert "super-secret-value" not in json.dumps(rendered)


def test_unavailable_fields_render_as_unavailable():
    rendered = render_environment({"qdrant_version": None, "nested": {"cuda_version": None}})
    assert rendered["qdrant_version"] == "unavailable"
    assert rendered["nested"]["cuda_version"] == "unavailable"


def test_blocked_run_writes_full_artifact_set(tmp_path):
    metadata = {"run_id": "r1", "sample_count": 3, "requested_configs": ["bm25"]}
    summary = {
        "configs": {"bm25": {"status": "BLOCKED", "reason": "unavailable backends: bm25=service_unreachable"}},
        "executed_configs": [],
        "blocked_configs": ["bm25"],
        "latency": {},
        "any_results": False,
    }
    coverage = {
        "sample_count": 3,
        "relevance_level": "level2_normalized_exact_text",
        "available_buckets": [],
        "unavailable_buckets": [],
        "field_coverage": {},
    }
    run_dir = write_artifacts(
        tmp_path, "r1", metadata, {}, summary, coverage, per_query_rows=0, synthetic_retriever=False
    )
    assert verify_artifact_set(run_dir) == []
    for name in REQUIRED_ARTIFACT_FILES:
        assert (run_dir / name).is_file(), name
    report_text = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "BLOCKED" in report_text
    assert "No configuration executed" in report_text


def test_synthetic_run_is_flagged_in_report(tmp_path):
    metadata = {"run_id": "r2", "sample_count": 1, "requested_configs": ["bm25"]}
    summary = {
        "configs": {
            "bm25": {
                "status": "EXECUTED",
                "reason": "synthetic fixture retriever (not a benchmark)",
                "metrics": {
                    "overall": {"sample_count": 1, "recall_at_1": 1.0},
                    "by_business_type": {},
                    "by_difficulty": {},
                },
                "latency": {"total_retrieval_ms": {"count": 1, "p50": 1.0, "p95": 1.0, "p99": 1.0, "mean": 1.0}},
                "stage_availability": {},
            }
        },
        "executed_configs": ["bm25"],
        "blocked_configs": [],
        "latency": {},
        "any_results": True,
    }
    coverage = {
        "sample_count": 1,
        "relevance_level": "level2",
        "available_buckets": [],
        "unavailable_buckets": [],
        "field_coverage": {},
    }
    run_dir = write_artifacts(
        tmp_path, "r2", metadata, {}, summary, coverage, per_query_rows=1, synthetic_retriever=True
    )
    report_text = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "synthetic fixture retriever" in report_text
    assert "NOT a retrieval-quality benchmark result" in report_text


def test_missing_artifact_is_detected(tmp_path):
    from benchmarks.report import REQUIRED_ARTIFACT_FILES as required

    empty = tmp_path / "run"
    empty.mkdir()
    missing = verify_artifact_set(empty)
    assert set(missing) == set(required)


def test_build_report_has_all_required_sections(tmp_path):
    metadata = {"run_id": "r3", "requested_configs": []}
    summary = {"configs": {}, "executed_configs": [], "blocked_configs": [], "latency": {}, "any_results": False}
    coverage = {
        "sample_count": 0,
        "relevance_level": "level2",
        "available_buckets": [],
        "unavailable_buckets": [],
        "field_coverage": {},
    }
    text = build_report(metadata, {}, summary, coverage, per_query_rows=0, synthetic_retriever=False)
    for heading in (
        "# Benchmark Run",
        "## Provenance",
        "## Dataset",
        "## Configuration",
        "## Overall Metrics",
        "## Failed / Skipped Queries",
        "## Limitations",
    ):
        assert heading in text
    assert "not historical production metrics" in text
