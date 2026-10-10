"""Deterministic retrieval metrics.

Every function here is a pure function of ranked identifier lists. There is no
LLM judge, no embedding similarity, no fuzzy threshold and no randomness, so the
same inputs always produce the same numbers.

Definitions (binary relevance by default, graded when grades are supplied):

* ``Recall@K``  = |relevant ∩ top-K| / |relevant|
  A query with several relevant passages can therefore never score 1.0 at K=1.
* ``HitRate@K`` = 1.0 when at least one relevant passage appears in top-K else 0.0
* ``MRR@K``     = 1 / rank of the first relevant passage within top-K, else 0.0
* ``NDCG@K``    = DCG@K / IDCG@K
                  DCG@K  = Σ_{i=1..K} gain(rel_i) / log2(i + 1)
                  IDCG@K = Σ_{i=1..min(K, |relevant|)} gain(sorted gains) / log2(i + 1)

``Recall`` / ``HitRate`` / ``MRR`` stay binary by design: they answer "was any
supporting passage retrieved". ``NDCG`` is the only metric that consumes graded
relevance, and it keeps binary behaviour when every grade is equal — a dataset
with no grades produces exactly the numbers it produced before.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from benchmarks.models import (
    GRADE_THRESHOLD_RELEVANT,
    HIT_KS,
    MRR_K,
    NDCG_K,
    RECALL_KS,
    RELEVANCE_HIGHLY_RELEVANT,
    RelevantItem,
    QueryBenchmarkResult,
)


def _unique_preserving_order(keys: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def grades_from_relevant(
    relevant_items: Sequence[RelevantItem] | Sequence[Mapping[str, Any]] | None,
) -> dict[str, int]:
    """Map relevance key -> grade for a query's ground truth.

    Accepts :class:`~benchmarks.models.RelevantItem` objects or plain mappings
    so a caller can score a stored JSON row without rebuilding the dataclasses.
    Keys below :data:`~benchmarks.models.GRADE_THRESHOLD_RELEVANT` are omitted:
    a passage an annotator marked not-relevant is not retrievable evidence.
    """
    grades: dict[str, int] = {}
    for item in relevant_items or ():
        if isinstance(item, RelevantItem):
            key, grade = item.key, item.relevance
        elif isinstance(item, Mapping):
            key, grade = item.get("key"), item.get("relevance", RELEVANCE_HIGHLY_RELEVANT)
        else:  # a bare key string: binary, maximally relevant
            key, grade = item, RELEVANCE_HIGHLY_RELEVANT
        if not key:
            continue
        if not isinstance(grade, int) or isinstance(grade, bool) or grade < GRADE_THRESHOLD_RELEVANT:
            continue
        grades[str(key)] = grade
    return grades


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
    return ndcg_graded({key: RELEVANCE_HIGHLY_RELEVANT for key in relevant_keys}, retrieved_keys, k=k)


def ndcg_graded(
    grades: Mapping[str, int],
    retrieved_keys: Sequence[str],
    k: int = NDCG_K,
) -> float | None:
    """Graded NDCG@K over ``key -> grade``.

    Grades below :data:`~benchmarks.models.GRADE_THRESHOLD_RELEVANT` are ignored.
    The ideal ranking sorts the true grades descending, so a query whose best
    evidence is only partially relevant cannot reach 1.0 by returning the one
    passage that happens to exist — which is the property binary NDCG loses as
    soon as the dataset carries grades.

    With a constant grade this reduces exactly to :func:`ndcg_at_k`.
    """
    relevant = {key: grade for key, grade in grades.items() if grade >= GRADE_THRESHOLD_RELEVANT}
    if not relevant:
        return None
    dcg = 0.0
    credited: set[str] = set()
    for index, key in enumerate(retrieved_keys[:k], start=1):
        grade = relevant.get(key)
        if grade is not None and key not in credited:
            credited.add(key)
            dcg += float(grade) / math.log2(index + 1)
    ideal_hits = min(len(relevant), k)
    ideal_gains = sorted((float(grade) for grade in relevant.values()), reverse=True)[:ideal_hits]
    idcg = sum(gain / math.log2(index + 1) for index, gain in enumerate(ideal_gains, start=1))
    if idcg == 0:
        return None
    return dcg / idcg


def score_ranking(
    relevant_keys: Sequence[str] | Sequence[RelevantItem],
    retrieved_keys: Sequence[str],
) -> dict[str, Any]:
    """Compute the full metric set for one ranked result list.

    ``relevant_keys`` may be plain keys or :class:`RelevantItem` objects. With
    bare keys the result is exactly the historical binary computation; with
    graded items, ``ndcg_at_10`` becomes graded while Recall/Hit/MRR stay
    binary.
    """
    grades = grades_from_relevant(relevant_keys)
    relevant = _unique_preserving_order(grades)
    retrieved = _unique_preserving_order(retrieved_keys)
    distinct_grades = len(set(grades.values()))
    return {
        "first_relevant_rank": first_relevant_rank(relevant, retrieved),
        "recall_at": {k: recall_at_k(relevant, retrieved, k) for k in RECALL_KS},
        "hit_at": {k: hit_at_k(relevant, retrieved, k) for k in HIT_KS},
        "reciprocal_rank": reciprocal_rank(relevant, retrieved),
        "ndcg_at_10": ndcg_graded(grades, retrieved),
        "graded_relevance": distinct_grades > 1,
        "relevant_count": len(relevant),
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


def aggregate_results(
    results: Sequence[QueryBenchmarkResult],
    available_buckets: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Aggregate per-query results into overall and per-bucket blocks.

    ``available_buckets`` comes from the dataset's measured field coverage. A
    breakdown whose source field is absent is omitted entirely rather than built
    from the loader's ``"unknown"`` placeholder, which would present an invented
    all-unknown table as a real result. Small buckets keep their real
    ``sample_count`` instead of being hidden.
    """
    supported = set(available_buckets) if available_buckets is not None else {"overall", "business_type", "difficulty"}
    aggregate: dict[str, Any] = {"overall": _metric_block(results)}
    for attribute in ("business_type", "difficulty"):
        if attribute in supported:
            aggregate[f"by_{attribute}"] = _group(results, attribute)
    return aggregate
