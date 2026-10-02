"""Failure handling: empty dataset, missing truth, unknown config, unavailable model."""

from __future__ import annotations

import json

import pytest

from benchmarks import backends
from benchmarks.dataset import DatasetError, load_queries, load_rows
from benchmarks.models import STATUS_BLOCKED, STATUS_SKIPPED
from benchmarks.runner import run_configuration


def test_empty_dataset_is_rejected(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("\n\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="empty"):
        load_rows(path)


def test_malformed_json_is_rejected(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"question": "q"}\nnot-json\n', encoding="utf-8")
    with pytest.raises(DatasetError, match="invalid JSON"):
        load_rows(path)


def test_sample_without_question_is_rejected(tmp_path):
    path = tmp_path / "noq.jsonl"
    path.write_text(json.dumps({"contexts": ["a"]}) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="question"):
        load_queries(path)


def test_missing_relevance_truth_is_rejected(tmp_path):
    path = tmp_path / "notruth.jsonl"
    path.write_text(json.dumps({"question": "q", "contexts": []}) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="ground truth"):
        load_queries(path)


def test_unknown_config_is_skipped_not_measured(make_query):
    queries = [make_query("0000", ["alpha passage"])]
    run = run_configuration("not_a_config", queries)
    assert run.outcome.status == STATUS_SKIPPED
    assert "unknown configuration" in run.outcome.reason
    assert run.results == []
    assert run.executed is False


def test_no_retriever_means_blocked_not_fabricated(make_query):
    queries = [make_query("0000", ["alpha passage"])]
    run = run_configuration("bm25", queries)
    assert run.outcome.status == STATUS_BLOCKED
    assert run.results == []
    assert run.executed is False


def test_backend_probe_reports_unavailable_in_offline_env():
    availability = backends.probe_corpus(10)
    assert availability.available is False
    assert availability.reason == backends.REASON_CORPUS_UNRESOLVED


def test_crossencoder_backend_is_not_claimed_available():
    availability = backends.probe_crossencoder()
    assert availability.available is False


def test_config_matrix_covers_expected_names():
    assert set(backends.CONFIG_DESCRIPTIONS) == {
        "bm25",
        "dense",
        "hybrid_rrf",
        "hybrid_rrf_biencoder",
        "hybrid_rrf_biencoder_crossencoder",
    }
    for name in backends.CONFIG_DESCRIPTIONS:
        assert name in backends.REQUIRED_BACKENDS
        assert name in backends.STAGE_BY_CONFIG


def test_unknown_config_availability_is_false():
    availability = backends.evaluate_config("nope", 1)
    assert availability.available is False


def test_benchmark_unavailable_is_raisable():
    with pytest.raises(backends.BenchmarkUnavailable):
        raise backends.BenchmarkUnavailable("x")
