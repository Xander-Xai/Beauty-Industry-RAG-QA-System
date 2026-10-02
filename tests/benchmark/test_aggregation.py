"""Bucket aggregation over overall / business_type / difficulty."""

from __future__ import annotations

import pytest

from benchmarks.metrics import aggregate_results, score_ranking
from benchmarks.runner import run_configuration, summarize_run
from tests.benchmark.conftest import FixtureRetriever


def _result(sample_id, business_type, difficulty, relevant, retrieved):
    scored = score_ranking(relevant, retrieved)
    return {
        "sample_id": sample_id,
        "business_type": business_type,
        "difficulty": difficulty,
        "relevant": relevant,
        "retrieved": retrieved,
        "scored": scored,
    }


def _to_query_result(payload):
    from benchmarks.models import QueryBenchmarkResult

    scored = payload["scored"]
    return QueryBenchmarkResult(
        sample_id=payload["sample_id"],
        question="q",
        business_type=payload["business_type"],
        difficulty=payload["difficulty"],
        config="fixture",
        relevant_keys=tuple(payload["relevant"]),
        retrieved_keys=tuple(payload["retrieved"]),
        first_relevant_rank=scored["first_relevant_rank"],
        recall_at=scored["recall_at"],
        hit_at=scored["hit_at"],
        reciprocal_rank=scored["reciprocal_rank"],
        ndcg_at_10=scored["ndcg_at_10"],
        latency_ms={"total_retrieval_ms": 1.0},
    )


def test_aggregate_overall_and_buckets():
    results = [
        _to_query_result(_result("0000", "regulation", "easy", ["a"], ["a"])),
        _to_query_result(_result("0001", "regulation", "hard", ["b"], ["x"])),
        _to_query_result(_result("0002", "ingredient", "hard", ["c"], ["c"])),
    ]
    agg = aggregate_results(results)
    assert agg["overall"]["sample_count"] == 3
    assert set(agg["by_business_type"]) == {"regulation", "ingredient"}
    assert set(agg["by_difficulty"]) == {"easy", "hard"}
    # regulation bucket keeps its own sample_count even though it is small
    assert agg["by_business_type"]["regulation"]["sample_count"] == 2
    assert agg["by_business_type"]["ingredient"]["sample_count"] == 1


def test_small_buckets_are_not_hidden():
    results = [
        _to_query_result(_result("0000", "regulation", "easy", ["a"], ["a"])),
        _to_query_result(_result("0001", "rare_type", "rare_diff", ["b"], ["b"])),
    ]
    agg = aggregate_results(results)
    assert "rare_type" in agg["by_business_type"]
    assert agg["by_business_type"]["rare_type"]["sample_count"] == 1


def test_run_configuration_with_fixture_retriever_scores(make_query):
    queries = [make_query("0000", ["alpha passage", "beta passage"])]
    retriever = FixtureRetriever({"0000": ["beta passage"]})
    run = run_configuration("bm25", queries, retriever_factory=lambda name: retriever)
    assert run.executed
    result = run.results[0]
    # beta first -> recall@1 = 1/2, hit@1 true
    assert result.recall_at[1] == pytest.approx(0.5)
    assert result.hit_at[1] is True


def test_summarize_run_marks_synthetic_and_keeps_blocked(mini_dataset, make_query):
    from benchmarks.dataset import load_queries

    queries = load_queries(mini_dataset)
    retriever = FixtureRetriever({"0000": ["alpha passage", "beta passage"]})
    executed = run_configuration("bm25", queries, retriever_factory=lambda name: retriever)
    blocked = run_configuration("hybrid_rrf_biencoder_crossencoder", queries)
    summary = summarize_run([executed, blocked])
    assert summary["any_results"] is True
    assert summary["executed_configs"] == ["bm25"]
    assert summary["blocked_configs"] == ["hybrid_rrf_biencoder_crossencoder"]
    # synthetic reason is recorded, not hidden
    assert "synthetic" in summary["configs"]["bm25"]["reason"]
