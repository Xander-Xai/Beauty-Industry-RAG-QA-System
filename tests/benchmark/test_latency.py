"""Latency aggregation: unset stages stay null, never zero."""

from __future__ import annotations

import pytest

from benchmarks.latency import (
    new_latency_record,
    record_stage,
    stage_availability,
    summarize_latency,
    timed_stage,
)
from benchmarks.models import LATENCY_STAGES


def test_new_record_has_all_stages_as_none():
    record = new_latency_record()
    assert set(record) == set(LATENCY_STAGES)
    assert all(value is None for value in record.values())


def test_unexecuted_stage_is_null_not_zero():
    summary = summarize_latency([{"total_retrieval_ms": 10.0}])
    assert summary["bm25_ms"]["count"] == 0
    assert summary["bm25_ms"]["mean"] is None
    assert summary["bm25_ms"]["p95"] is None
    # and a real one is summarised
    assert summary["total_retrieval_ms"]["count"] == 1
    assert summary["total_retrieval_ms"]["mean"] == 10.0


def test_percentiles_over_known_series():
    records = [{"total_retrieval_ms": float(value)} for value in range(1, 101)]
    summary = summarize_latency(records)["total_retrieval_ms"]
    assert summary["count"] == 100
    assert summary["min"] == 1.0
    assert summary["max"] == 100.0
    assert 49.0 <= summary["p50"] <= 51.0
    assert 94.0 <= summary["p95"] <= 96.0
    assert 98.0 <= summary["p99"] <= 100.0


def test_timed_stage_uses_monotonic_and_records():
    record = new_latency_record()
    with timed_stage(record, "bm25_ms"):
        pass
    assert record["bm25_ms"] is not None
    assert record["bm25_ms"] >= 0.0


def test_timed_stage_rejects_unknown_stage():
    record = new_latency_record()
    with pytest.raises(KeyError):
        with timed_stage(record, "not_a_stage"):
            pass


def test_stage_availability_counts_only_recorded():
    availability = stage_availability([{"total_retrieval_ms": 1.0}, {"total_retrieval_ms": 2.0, "bm25_ms": 3.0}])
    assert availability["bm25_ms"] == 1
    assert availability["crossencoder_ms"] == 0


def test_record_stage_rejects_unknown_stage():
    record = new_latency_record()
    with pytest.raises(KeyError):
        record_stage(record, "nope", 1.0)
