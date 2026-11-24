"""Retrieval Bad Case analysis never treats no-gold cases as measured misses."""

import pytest

from benchmarks.failure_triage import analyze_failures


def _row(sample, relevant, retrieved, *, config="bm25", business_type="regulation", hit=None):
    return {
        "config": config,
        "sample_id": sample,
        "business_type": business_type,
        "relevant_items": relevant,
        "retrieved_items": retrieved,
        "hit_at_10": hit,
    }


def test_miss_late_hit_and_no_gold_are_separate_populations():
    result = analyze_failures(
        [
            _row("good", ["g"], ["g"], hit=True),
            _row("late", ["g"], ["x", "g"], hit=True),
            _row("miss", ["g"], ["x"], hit=False),
            _row("unknown", [], ["g"]),
        ]
    )
    b = result["configurations"]["bm25"]
    assert (b["rows"], b["scored"], b["excluded_no_gold"]) == (4, 3, 1)
    assert (b["top1_hits"], b["late_hits_at_2_to_10"], b["misses_at_10"]) == (1, 1, 1)
    assert b["failed_case_ids"] == ["miss"]
    assert b["late_hit_case_ids"] == ["late"]
    assert result["publication_eligibility"] == "NOT_ASSESSED"
    assert result["results_are_benchmark"] is None


@pytest.mark.parametrize(
    "extra",
    [
        {"hit_at_10": True},
        {"hit_at_10": "false"},
        {"first_relevant_rank": 1},
        {"rr": 0.5},
        {"recall_at_10": 0.1},
        {"rr": float("nan")},
        {"recall_at_10": True},
    ],
)
def test_no_gold_cannot_claim_positive_or_invalid_retrieval_metrics(extra):
    row = _row("not-reviewed", [], ["irrelevant"])
    row.update(extra)
    with pytest.raises(ValueError, match="no-gold"):
        analyze_failures([row])


def test_zero_metric_fields_on_no_gold_remain_excluded_not_misses():
    row = _row("not-reviewed", [], ["irrelevant"], hit=False)
    row.update({"first_relevant_rank": None, "rr": 0.0, "recall_at_10": 0.0})
    result = analyze_failures([row])["configurations"]["bm25"]
    assert result["scored"] == 0
    assert result["excluded_no_gold"] == 1
    assert result["misses_at_10"] == 0


def test_configs_and_business_buckets_are_kept_separate():
    result = analyze_failures(
        [
            _row("a", ["r"], [], config="bm25"),
            _row("a", ["r"], ["r"], config="dense", business_type="ingredient"),
        ]
    )
    assert result["configurations"]["bm25"]["misses_at_10"] == 1
    assert result["configurations"]["dense"]["top1_hits"] == 1


def test_duplicate_sample_identifier_within_arm_is_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        analyze_failures([_row("x", ["a"], ["a"]), _row("x", ["a"], [])])


def test_inconsistent_claimed_hit_fails_closed():
    with pytest.raises(ValueError, match="contradicts"):
        analyze_failures([_row("x", ["gold"], ["other"], hit=True)])


def test_incomplete_or_malformed_rows_are_not_silently_skipped():
    with pytest.raises(ValueError, match="missing relevant_items"):
        analyze_failures([{"sample_id": "x", "config": "bm25", "retrieved_items": []}])


def test_duplicate_retrieved_rank_is_rejected_not_promoted():
    with pytest.raises(ValueError, match="duplicate retrieved"):
        analyze_failures([_row("x", ["gold"], ["noise", "noise", "gold"], hit=True)])


def test_rank_reciprocal_rank_and_recall_must_match_identifiers():
    base = _row("c", ["gold", "second"], ["x", "gold"], hit=True)
    base.update({"first_relevant_rank": 2, "rr": 0.5, "recall_at_10": 0.5})
    assert analyze_failures([base])["configurations"]["bm25"]["late_hits_at_2_to_10"] == 1

    for field, invalid in (
        ("first_relevant_rank", 1),
        ("rr", 1.0),
        ("recall_at_10", 1.0),
        ("rr", float("nan")),
    ):
        row = {**base, field: invalid}
        with pytest.raises(ValueError, match="contradicts"):
            analyze_failures([row])


def test_rank_beyond_ten_does_not_inflate_mrr_or_recall_at_ten():
    row = _row("late", ["gold"], ["irrelevant"] * 10 + ["gold"], hit=False)
    # The CLI must refuse duplicate identities before evaluating the ranking.
    with pytest.raises(ValueError, match="duplicate retrieved"):
        analyze_failures([row])
    unique = _row("late", ["gold"], [f"noise-{i}" for i in range(10)] + ["gold"], hit=False)
    unique.update({"first_relevant_rank": 11, "rr": 0.0, "recall_at_10": 0.0})
    assert analyze_failures([unique])["configurations"]["bm25"]["misses_at_10"] == 1


def test_duplicate_gold_identity_and_boolean_rank_are_rejected():
    with pytest.raises(ValueError, match="duplicate gold"):
        analyze_failures([_row("dup", ["gold", "gold"], ["gold"], hit=True)])

    row = _row("bool", ["gold"], ["gold"], hit=True)
    row["first_relevant_rank"] = True
    with pytest.raises(ValueError, match="first_relevant_rank contradicts"):
        analyze_failures([row])
