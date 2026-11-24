"""Matched-arm retrieval diagnostics cannot hide losses or population drift."""

import hashlib
import json

import pytest

from benchmarks.paired_retrieval import compare_arms, main


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


def test_cli_hashes_exact_input_and_preserves_source(tmp_path):
    source = tmp_path / "rows.jsonl"
    out = tmp_path / "report.json"
    records = [row("a", "x", ["a"], []), row("b", "x", ["a"], ["a"])]
    source.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    original = source.read_bytes()
    args = ["--per-query", str(source), "--baseline", "a", "--candidate", "b", "--out"]
    assert main(args + [str(out)]) == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["input_sha256"] == hashlib.sha256(original).hexdigest()
    assert result["delta_recall_at_10"] == 1.0
    with pytest.raises(SystemExit):
        main(args + [str(source)])
    assert source.read_bytes() == original
