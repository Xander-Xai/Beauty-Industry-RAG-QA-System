"""Deterministic, reproducible retrieval benchmark harness.

This package measures *retrieval* quality only. It never calls the RAGAS LLM
evaluator, and it never invents results: every configuration reports an explicit
status so an unavailable backend can never be mistaken for a measured one.

Design rules that are enforced rather than documented:

* Metrics are pure functions over ranked identifiers (no LLM judge, no embedding
  similarity threshold, no fuzzy matching).
* Relevance matching uses stable identifiers when the dataset provides them, and
  otherwise a normalized exact text match. Nothing weaker than that.
* A configuration whose backend is unavailable is reported as
  ``SKIPPED_BACKEND_UNAVAILABLE`` / ``BLOCKED_MODEL_UNAVAILABLE`` and contributes
  no metric values.
* Unavailable provenance fields are written as ``None``/``"unavailable"``; they
  are never guessed.
"""

from benchmarks.latency import summarize_latency
from benchmarks.metrics import (
    aggregate_results,
    hit_at_k,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)
from benchmarks.models import (
    BackendAvailability,
    BenchmarkQuery,
    BenchmarkRunMetadata,
    ConfigOutcome,
    QueryBenchmarkResult,
    RelevantItem,
    RetrievedItem,
)

__all__ = [
    "BackendAvailability",
    "BenchmarkQuery",
    "BenchmarkRunMetadata",
    "ConfigOutcome",
    "QueryBenchmarkResult",
    "RelevantItem",
    "RetrievedItem",
    "aggregate_results",
    "hit_at_k",
    "ndcg_at_k",
    "reciprocal_rank",
    "recall_at_k",
    "summarize_latency",
]
