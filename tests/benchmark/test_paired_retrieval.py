"""Matched-arm retrieval diagnostics cannot hide losses or population drift."""
import pytest

from benchmarks.paired_retrieval import compare_arms


def row(arm, sample, gold, ranked):
    return {
        "config": arm,
        "sample_id": sample,
        "business_type": "ingredient",
        "relevant_items": gold,
        "retrieved_items": ranked,
    }


def test_pair_reports_gains_losses_and_no_gold():
    rows = [
        row("bm25", "gain", ["a"], []),
        row("hybrid", "gain", ["a"], ["a"]),
        row("bm25", "loss", ["b"], ["b"]),
        row("hybrid", "loss", ["b"], []),
        row("bm25", "none", [], []),
        row("hybrid", "none", [], []),
    ]
    result = compare_arms(rows, "bm25", "hybrid")
    assert result["scored"] == 2
    assert result["excluded_no_gold"] == 1
    assert result["delta_recall_at_10"] == 0
    assert result["hit_gained_ids"] == ["gain"]
    assert result["hit_lost_ids"] == ["loss"]


def test_reject_population_and_gold_drift():
    a = row("bm25", "x", ["a"], ["a"])
    with pytest.raises(ValueError, match="populations"):
        compare_arms([a], "bm25", "hybrid")
    with pytest.raises(ValueError, match="gold"):
        compare_arms([a, row("hybrid", "x", ["b"], ["b"])], "bm25", "hybrid")


def test_empty_gold_is_not_zero_quality():
    result = compare_arms([row("a", "x", [], []), row("b", "x", [], [])], "a", "b")
    assert result["scored"] == 0
    assert result["delta_recall_at_10"] is None
    assert result["delta_mrr_at_10"] is None
