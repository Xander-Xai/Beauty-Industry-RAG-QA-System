"""Metric correctness tests: perfect, partial, no-hit, multiple-relevant."""

from __future__ import annotations

import math

import pytest

from benchmarks.metrics import (
    first_relevant_rank,
    hit_at_k,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    score_ranking,
)

RELEVANT = ["a", "b", "c"]


def test_perfect_ranking_scores_one():
    assert recall_at_k(RELEVANT, ["a", "b", "c"], 10) == 1.0
    assert hit_at_k(RELEVANT, ["a", "b", "c"], 1) is True
    assert reciprocal_rank(RELEVANT, ["a", "b", "c"]) == 1.0
    assert ndcg_at_k(RELEVANT, ["a", "b", "c"], 10) == pytest.approx(1.0)


def test_partial_recall_counts_only_top_k():
    # one of three relevant items inside the top 1
    assert recall_at_k(RELEVANT, ["a", "x", "y"], 1) == pytest.approx(1 / 3)
    assert recall_at_k(RELEVANT, ["a", "x", "y", "b"], 10) == pytest.approx(2 / 3)


def test_no_hit_scores_zero_but_not_none():
    assert recall_at_k(RELEVANT, ["x", "y", "z"], 10) == 0.0
    assert hit_at_k(RELEVANT, ["x", "y", "z"], 1) is False
    assert reciprocal_rank(RELEVANT, ["x", "y", "z"]) == 0.0
    assert ndcg_at_k(RELEVANT, ["x", "y", "z"], 10) == 0.0


def test_multiple_relevant_items_are_all_denominator():
    scored = score_ranking(RELEVANT, ["x", "a", "y", "b", "z", "c"])
    # rank 1 is a distractor, so nothing is retrieved within the top 1
    assert scored["recall_at"][1] == 0.0
    assert scored["recall_at"][3] == pytest.approx(1 / 3)
    assert scored["recall_at"][5] == pytest.approx(2 / 3)
    assert scored["recall_at"][10] == 1.0
    assert scored["first_relevant_rank"] == 2


def test_empty_ground_truth_is_none_not_perfect():
    assert recall_at_k([], ["a"], 10) is None
    assert hit_at_k([], ["a"], 10) is None
    assert reciprocal_rank([], ["a"]) is None
    assert ndcg_at_k([], ["a"], 10) is None


def test_mrr_uses_first_relevant_rank():
    assert reciprocal_rank(RELEVANT, ["x", "y", "a"]) == pytest.approx(1 / 3)
    assert reciprocal_rank(RELEVANT, ["x"] * 10 + ["a"]) == 0.0


def test_ndcg_binary_matches_manual_computation():
    relevant = {"b"}
    retrieved = ["x", "b", "y", "z"]
    dcg = 1.0 / math.log2(3)
    idcg = 1.0 / math.log2(2)
    assert ndcg_at_k(relevant, retrieved, 10) == pytest.approx(dcg / idcg)


def test_ndcg_uses_binary_relevance_only():
    # A duplicated relevant key must not be credited twice and must never push
    # NDCG above 1.0. With "a" at rank 1 and "b" first seen at rank 3, DCG is
    # 1/log2(2) + 1/log2(4) against the binary ideal.
    score = ndcg_at_k({"a", "b"}, ["a", "a", "b"], 10)
    expected = (1.0 / math.log2(2) + 1.0 / math.log2(4)) / (1.0 / math.log2(2) + 1.0 / math.log2(3))
    assert score == pytest.approx(expected)
    assert 0.0 < score <= 1.0
    # Removing the duplicate restores the ideal.
    assert ndcg_at_k({"a", "b"}, ["a", "b"], 10) == pytest.approx(1.0)


def test_first_relevant_rank_reports_none_when_absent():
    assert first_relevant_rank(RELEVANT, ["x", "y"]) is None
