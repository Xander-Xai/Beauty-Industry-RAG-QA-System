"""Deterministic retrieval metrics.

Every function here is a pure function of ranked identifier lists. There is no
LLM judge, no embedding similarity, no fuzzy threshold and no randomness, so the
same inputs always produce the same numbers.

Definitions (binary relevance only — the dataset carries no graded relevance):

* ``Recall@K``  = |relevant ∩ top-K| / |relevant|
  A query with several relevant passages can therefore never score 1.0 at K=1.
* ``HitRate@K`` = 1.0 when at least one relevant passage appears in top-K else 0.0
* ``MRR@K``     = 1 / rank of the first relevant passage within top-K, else 0.0
* ``NDCG@K``    = DCG@K / IDCG@K with binary gains
                  DCG@K  = Σ_{i=1..K} rel_i / log2(i + 1)
                  IDCG@K = Σ_{i=1..min(K, |relevant|)} 1 / log2(i + 1)
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any

from benchmarks.models import HIT_KS, MRR_K, NDCG_K, RECALL_KS, QueryBenchmarkResult


def _unique_preserving_order(keys: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def recall_at_k(relevant_keys: Sequence[str], retrieved_keys: Sequence[str], k: int) -> float | None:
    """Fraction of ground-truth passages retrieved within the top ``k`` results.

    Returns ``None`` when the query has no ground truth, so an empty ground truth
    can never be silently reported as a perfect score.
    """
    relevant = set(relevant_keys)
    if not relevant:
        return None
    top_k = set(retrieved_keys[:k])
    return len(relevant & top_k) / len(relevant)


def hit_at_k(relevant_keys: Sequence[str], retrieved_keys: Sequence[str], k: int) -> bool | None:
    """Whether at least one ground-truth passage is retrieved within top ``k``."""
    if not relevant_keys:
        return None
    return bool(set(relevant_keys) & set(retrieved_keys[:k]))


def first_relevant_rank(relevant_keys: Sequence[str], retrieved_keys: Sequence[str]) -> int | None:
    """1-based rank of the first ground-truth hit, or ``None`` when never retrieved."""
    relevant = set(relevant_keys)
    for index, key in enumerate(retrieved_keys, start=1):
        if key in relevant:
            return index
    return None


def reciprocal_rank(relevant_keys: Sequence[str], retrieved_keys: Sequence[str], k: int = MRR_K) -> float | None:
    """Reciprocal rank of the first ground-truth hit within top ``k``."""
    if not relevant_keys:
        return None
    rank = first_relevant_rank(relevant_keys, retrieved_keys)
    if rank is None or rank > k:
        return 0.0
    return 1.0 / rank


def ndcg_at_k(relevant_keys: Sequence[str], retrieved_keys: Sequence[str], k: int = NDCG_K) -> float | None:
    """Binary-relevance NDCG@K.

    Each relevant passage contributes gain once, at its best rank, so a repeated
    key in the ranking cannot inflate DCG and push NDCG above 1.0.
    """
    relevant = set(relevant_keys)
    if not relevant:
        return None
    dcg = 0.0
    credited: set[str] = set()
    for index, key in enumerate(retrieved_keys[:k], start=1):
        if key in relevant and key not in credited:
            credited.add(key)
            dcg += 1.0 / math.log2(index + 1)
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
    if idcg == 0:
        return None
    return dcg / idcg


def score_ranking(
    relevant_keys: Sequence[str],
    retrieved_keys: Sequence[str],
) -> dict[str, Any]:
    """Compute the full metric set for one ranked result list."""
    relevant = _unique_preserving_order(relevant_keys)
    retrieved = _unique_preserving_order(retrieved_keys)
    return {
        "first_relevant_rank": first_relevant_rank(relevant, retrieved),
        "recall_at": {k: recall_at_k(relevant, retrieved, k) for k in RECALL_KS},
        "hit_at": {k: hit_at_k(relevant, retrieved, k) for k in HIT_KS},
        "reciprocal_rank": reciprocal_rank(relevant, retrieved),
        "ndcg_at_10": ndcg_at_k(relevant, retrieved),
    }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _metric_block(results: Sequence[QueryBenchmarkResult]) -> dict[str, Any]:
    recalls: dict[str, float | None] = {}
    for k in RECALL_KS:
        values = [r.recall_at[k] for r in results if r.recall_at.get(k) is not None]
        recalls[f"recall_at_{k}"] = _mean(values)
    hits: dict[str, float | None] = {}
    for k in HIT_KS:
        values = [1.0 if r.hit_at.get(k) else 0.0 for r in results if r.hit_at.get(k) is not None]
        hits[f"hit_rate_at_{k}"] = _mean(values)
    reciprocal = [r.reciprocal_rank for r in results if r.reciprocal_rank is not None]
    ndcg = [r.ndcg_at_10 for r in results if r.ndcg_at_10 is not None]
    return {
        "sample_count": len(results),
        "scored_sample_count": len(reciprocal),
        **recalls,
        **hits,
        "mrr_at_10": _mean(reciprocal),
        "ndcg_at_10": _mean(ndcg),
    }


def _group(results: Sequence[QueryBenchmarkResult], attribute: str) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[QueryBenchmarkResult]] = {}
    for result in results:
        buckets.setdefault(getattr(result, attribute), []).append(result)
    return {name: _metric_block(bucket) for name, bucket in sorted(buckets.items())}


def aggregate_results(results: Sequence[QueryBenchmarkResult]) -> dict[str, Any]:
    """Aggregate per-query results into overall and per-bucket blocks.

    Only the buckets the dataset actually supports are produced: ``overall``,
    ``business_type`` and ``difficulty``. Buckets are never invented, and small
    buckets keep their real ``sample_count`` instead of being hidden.
    """
    return {
        "overall": _metric_block(results),
        "by_business_type": _group(results, "business_type"),
        "by_difficulty": _group(results, "difficulty"),
    }
