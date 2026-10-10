"""Metrics over queries with several relevant documents/chunks, and graded relevance.

This is the regression guard for the defect where a row carrying a stable
identifier kept only its **first** ground-truth passage: a query with four
supporting passages was scored against one, turning Recall@5 from ``found / 4``
into a guaranteed 1.0. Every test here uses more than one relevant item unless it
is specifically about the single-item case.
"""

from __future__ import annotations

import json
import math

import pytest

from benchmarks.dataset import AmbiguousRelevanceIdentityError, DatasetError, load_queries
from benchmarks.metrics import (
    aggregate_results,
    grades_from_relevant,
    hit_at_k,
    ndcg_at_k,
    ndcg_graded,
    recall_at_k,
    score_ranking,
)
from benchmarks.models import (
    GRADE_THRESHOLD_RELEVANT,
    RELEVANCE_HIGHLY_RELEVANT,
    QueryBenchmarkResult,
    RelevantItem,
)

FOUR = ["a", "b", "c", "d"]


# ── binary metrics over several relevant passages ───────────────────────────


def test_recall_is_a_fraction_of_all_ground_truth():
    assert recall_at_k(FOUR, ["a"], 5) == 0.25
    assert recall_at_k(FOUR, ["a", "b"], 5) == 0.5
    assert recall_at_k(FOUR, FOUR, 5) == 1.0
    assert recall_at_k(FOUR, ["x", "y"], 5) == 0.0


def test_recall_at_1_can_never_be_perfect_with_several_passages():
    assert recall_at_k(FOUR, FOUR, 1) == 0.25


def test_hit_is_true_when_any_ground_truth_is_retrieved():
    assert hit_at_k(FOUR, ["x", "c"], 5) is True
    assert hit_at_k(FOUR, ["x", "y"], 5) is False


def test_ndcg_penalizes_ranking_only_the_last_of_several_passages():
    best = ndcg_at_k(FOUR, FOUR)
    worst = ndcg_at_k(FOUR, ["d", "x", "x", "x"])
    assert best == 1.0
    assert worst < best
    # Only the first position is a hit: DCG = 1/log2(2) = 1, and the ideal
    # ordering of four passages sums to 1 + 1/log2(3) + 1/log2(4) + 1/log2(5).
    idcg = sum(1.0 / math.log2(index + 1) for index in range(1, 5))
    assert worst == pytest.approx(1.0 / idcg)


def test_ndcg_stays_bounded_when_a_key_is_repeated():
    """A duplicate hit must not be credited twice and push NDCG above 1.0."""
    assert ndcg_at_k(FOUR, ["a", "a", "a", "a", "a"], 10) <= 1.0


def test_score_ranking_counts_every_ground_truth_passage():
    scored = score_ranking(FOUR, ["a"])
    assert scored["relevant_count"] == 4
    assert scored["recall_at"][5] == 0.25
    assert scored["hit_at"][5] is True


def test_perfect_retrieval_scores_one_across_the_board():
    scored = score_ranking(FOUR, FOUR)
    assert scored["recall_at"][10] == 1.0
    assert scored["hit_at"][10] is True
    assert scored["reciprocal_rank"] == 1.0
    assert scored["ndcg_at_10"] == 1.0


# ── aggregation must average fractional recall, not just 0 or 1 ──────────────


def _result(sample_id, relevant, retrieved):
    scored = score_ranking(relevant, retrieved)
    return QueryBenchmarkResult(
        sample_id=sample_id,
        question=f"q{sample_id}",
        business_type="regulation",
        difficulty="medium",
        config="bm25",
        relevant_keys=tuple(relevant),
        retrieved_keys=tuple(retrieved),
        first_relevant_rank=scored["first_relevant_rank"],
        recall_at=scored["recall_at"],
        hit_at=scored["hit_at"],
        reciprocal_rank=scored["reciprocal_rank"],
        ndcg_at_10=scored["ndcg_at_10"],
        latency_ms={"total_retrieval_ms": 1.0},
    )


def test_aggregate_averages_fractional_recall_across_queries():
    """The old aggregation fixtures all had one relevant item, so this never ran."""
    results = [
        _result("0000", FOUR, ["a"]),  # 1/4
        _result("0001", FOUR, FOUR),  # 4/4
    ]
    block = aggregate_results(results)["overall"]
    assert block["sample_count"] == 2
    assert block["recall_at_5"] == pytest.approx(0.625)


# ── graded relevance ────────────────────────────────────────────────────────


def test_grades_default_to_highly_relevant_for_unlabelled_items():
    """A dataset with no grades keeps exactly the binary behaviour it had."""
    assert grades_from_relevant([RelevantItem(key="a", text="t")]) == {"a": RELEVANCE_HIGHLY_RELEVANT}


def test_not_relevant_grades_are_excluded_from_scoring():
    items = [
        RelevantItem(key="a", text="t", relevance=GRADE_THRESHOLD_RELEVANT),
        RelevantItem(key="b", text="t", relevance=0),
    ]
    assert set(grades_from_relevant(items)) == {"a"}


def test_graded_ndcg_rewards_the_better_passage_first():
    """With two passages of different quality, order must matter."""
    grades = {"partial": 1, "full": 2}
    full_first = ndcg_graded(grades, ["full", "partial"])
    partial_first = ndcg_graded(grades, ["partial", "full"])
    assert full_first > partial_first
    assert full_first == pytest.approx(1.0)


def test_graded_ndcg_does_not_award_a_perfect_score_for_partial_evidence():
    """Retrieving only the partially-relevant passage cannot reach 1.0."""
    assert ndcg_graded({"partial": 1, "full": 2}, ["partial"]) < 1.0


def test_graded_ndcg_reduces_to_binary_when_grades_are_constant():
    grades = {"a": 2, "b": 2, "c": 2}
    assert ndcg_graded(grades, ["a", "b", "c"]) == ndcg_at_k(["a", "b", "c"], ["a", "b", "c"])


def test_graded_ndcg_ignores_below_threshold_grades():
    assert ndcg_graded({"a": 1, "b": 0}, ["a"]) == pytest.approx(1.0)


def test_score_ranking_flags_a_graded_query():
    graded = [RelevantItem(key="a", text="t", relevance=1), RelevantItem(key="b", text="t", relevance=2)]
    assert score_ranking(graded, ["a", "b"])["graded_relevance"] is True
    assert score_ranking(FOUR, FOUR)["graded_relevance"] is False


def test_graded_relevance_survives_the_runner(tmp_path):
    """End-to-end: a graded dataset must not be flattened to binary by the loader."""
    row = {
        "sample_id": "0000",
        "question": "q",
        "business_type": "regulation",
        "difficulty": "easy",
        "visual_required": False,
        "complexity_label": "simple",
        "corpus_version": "v1",
        "corpus_sha256": "a" * 64,
        "contexts": ["t1", "t2"],
        "annotations": [
            {"doc_id": "d1", "chunk_id": "c0", "text": "t1", "relevance": 2},
            {"doc_id": "d1", "chunk_id": "c1", "text": "t2", "relevance": 1},
        ],
        "annotation": {
            "annotator": "a",
            "method": "m",
            "annotated_at": "2026-10-09",
            "source": "human",
            "review_status": "REVIEWED",
            "reviewed_by": "b",
            "reviewed_at": "2026-10-10",
        },
    }
    path = tmp_path / "graded.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    query = load_queries(path)[0]
    assert [item.key for item in query.relevant_items] == ["d1::c0", "d1::c1"]
    assert [item.relevance for item in query.relevant_items] == [2, 1]

    # Ranking the strong passage first must beat ranking it last.
    strong_first = score_ranking(list(query.relevant_items), ["d1::c0", "d1::c1"])["ndcg_at_10"]
    strong_last = score_ranking(list(query.relevant_items), ["d1::c1", "d1::c0"])["ndcg_at_10"]
    assert strong_first > strong_last
    assert strong_first == pytest.approx(1.0)


# ── the multi-context defect itself ─────────────────────────────────────────


def test_a_row_level_identifier_cannot_stand_in_for_several_passages(tmp_path):
    """The reported defect: one flat id must not reduce four passages to one.

    Previously this returned exactly one relevant item keyed by the row-level id,
    so any retrieval that found that one passage scored Recall@5 = 1.0 regardless
    of the three other ground-truth passages.
    """
    row = {
        "question": "q",
        "business_type": "regulation",
        "difficulty": "medium",
        "doc_id": "doc-A",
        "contexts": ["p1", "p2", "p3", "p4"],
    }
    path = tmp_path / "ambiguous.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(AmbiguousRelevanceIdentityError) as excinfo:
        load_queries(path)
    message = str(excinfo.value)
    assert "doc-A" in message
    assert "4" in message


def test_a_single_context_with_a_row_identifier_still_works(tmp_path):
    """Backward compatibility for the unambiguous single-passage shape."""
    row = {
        "question": "q",
        "business_type": "regulation",
        "difficulty": "medium",
        "doc_id": "doc-A",
        "contexts": ["only passage"],
    }
    path = tmp_path / "single.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    query = load_queries(path)[0]
    assert [item.key for item in query.relevant_items] == ["doc-A"]


def test_text_only_rows_keep_every_passage(tmp_path):
    """The committed golden set shape: no ids, so every passage must be scored."""
    row = {"question": "q", "business_type": "regulation", "difficulty": "medium", "contexts": FOUR}
    path = tmp_path / "text.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    query = load_queries(path)[0]
    assert len(query.relevant_items) == 4


def test_annotations_keep_every_passage(tmp_path):
    row = {
        "question": "q",
        "business_type": "regulation",
        "difficulty": "medium",
        "contexts": FOUR,
        "annotations": [{"doc_id": "d1", "chunk_id": f"c{index}", "text": text} for index, text in enumerate(FOUR)],
    }
    path = tmp_path / "ann.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    query = load_queries(path)[0]
    assert len(query.relevant_items) == 4
    # Same document, four chunks -> four distinct keys.
    assert len({item.key for item in query.relevant_items}) == 4


def test_per_context_mappings_are_supported(tmp_path):
    row = {
        "question": "q",
        "business_type": "regulation",
        "difficulty": "medium",
        "contexts": [
            {"doc_id": "d1", "chunk_id": "c0", "text": "p0"},
            {"doc_id": "d2", "chunk_id": "c1", "text": "p1"},
        ],
    }
    path = tmp_path / "ctx.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    query = load_queries(path)[0]
    assert [item.key for item in query.relevant_items] == ["d1::c0", "d2::c1"]


def test_ambiguous_identity_is_a_dataset_error():
    """It must surface through the normal exception type the CLI already handles."""
    assert issubclass(AmbiguousRelevanceIdentityError, DatasetError)
