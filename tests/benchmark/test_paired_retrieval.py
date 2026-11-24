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


def test_graded_pair_recomputes_ndcg_without_flattening_relevance():
    a = row("bm25", "x", ["partial", "full"], ["partial", "full"])
    b = row("hybrid", "x", ["partial", "full"], ["full", "partial"])
    a["relevant_grades"] = b["relevant_grades"] = {"partial": 1, "full": 2}
    result = compare_arms([a, b], "bm25", "hybrid")
    assert result["delta_recall_at_10"] == 0
    assert result["delta_mrr_at_10"] == 0
    assert result["delta_ndcg_at_10"] > 0
    assert result["ndcg_grade_verified_samples"] == 1


@pytest.mark.parametrize("field", ["question", "difficulty", "relevant_grades"])
def test_pair_rejects_changed_query_or_grade_identity(field):
    a = row("bm25", "x", ["a"], ["a"])
    b = row("hybrid", "x", ["a"], ["a"])
    a[field] = {"a": 1} if field == "relevant_grades" else "old"
    b[field] = {"a": 2} if field == "relevant_grades" else "new"
    with pytest.raises(ValueError, match="differs|differ"):
        compare_arms([a, b], "bm25", "hybrid")


def test_legacy_rows_keep_ndcg_unknown_instead_of_assuming_binary_labels():
    a = row("bm25", "x", ["a"], ["a"])
    b = row("hybrid", "x", ["a"], ["a"])
    result = compare_arms([a, b], "bm25", "hybrid")
    assert result["delta_ndcg_at_10"] is None
    assert result["ndcg_grade_verified_samples"] == 0


@pytest.mark.parametrize("grades", [{"a": True}, {"a": 0}, {"a": 3}, {"other": 2}])
def test_pair_rejects_invalid_grade_contract(grades):
    a = row("bm25", "x", ["a"], ["a"])
    b = row("hybrid", "x", ["a"], ["a"])
    a["relevant_grades"] = b["relevant_grades"] = grades
    with pytest.raises(ValueError, match="invalid relevance grades"):
        compare_arms([a, b], "bm25", "hybrid")


def test_equal_recall_can_hide_top_rank_regression():
    a = row("bm25", "x", ["a"], ["a", "b"])
    b = row("hybrid", "x", ["a"], ["b", "a"])
    a["relevant_grades"] = b["relevant_grades"] = {"a": 2}
    result = compare_arms([a, b], "bm25", "hybrid")
    assert result["delta_recall_at_10"] == 0
    assert result["delta_mrr_at_10"] == -0.5
    assert result["delta_hit_at_1"] == -1.0
    assert result["mrr_regressed_ids"] == ["x"]
    assert result["ndcg_regressed_ids"] == ["x"]
    assert result["hit_lost_ids"] == []


def test_cli_rejects_existing_output_and_hardlink(tmp_path):
    source = tmp_path / "rows.jsonl"
    out = tmp_path / "existing.json"
    alias = tmp_path / "alias.jsonl"
    records = [row("a", "x", ["a"], []), row("b", "x", ["a"], ["a"])]
    source.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    original = source.read_bytes()
    out.write_text("previous evidence", encoding="utf-8")
    alias.hardlink_to(source)
    args = ["--per-query", str(source), "--baseline", "a", "--candidate", "b", "--out"]
    for target in (out, alias):
        with pytest.raises(SystemExit):
            main(args + [str(target)])
    assert out.read_text(encoding="utf-8") == "previous evidence"
    assert source.read_bytes() == original
