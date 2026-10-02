"""Benchmark orchestration.

The runner is the only place that turns queries into metrics, and it always
records a :class:`~benchmarks.models.ConfigOutcome` per configuration. A
configuration either produces measured metrics, or it is reported as blocked /
skipped with a reason. There is no code path that produces metrics without a
retriever that actually returned ranked items.

Synthetic retrievers
--------------------
Tests inject a deterministic fixture retriever through ``retriever_factory``.
Any run that used one is marked ``synthetic_retriever: true`` in the artifacts
and the report states that the numbers are *not* a retrieval benchmark. The CLI
never offers such a mode, so a synthetic run cannot reach a published artifact
by accident.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from benchmarks import backends
from benchmarks.latency import new_latency_record, stage_availability, summarize_latency, timed_stage
from benchmarks.metrics import aggregate_results, score_ranking
from benchmarks.models import (
    HIT_KS,
    MRR_K,
    NDCG_K,
    RECALL_KS,
    STATUS_BLOCKED,
    STATUS_EXECUTED,
    STATUS_SKIPPED,
    BackendAvailability,
    BenchmarkQuery,
    ConfigOutcome,
    QueryBenchmarkResult,
    RetrievedItem,
)
from benchmarks.relevance import RelevanceStrategy, dedupe_preserving_rank

DEFAULT_TOP_K = 10
# Every reported cutoff must be observable. A retrieval depth below this value
# would silently understate Recall@10 / MRR@10 / NDCG@10 because results at
# ranks 6..10 could never be seen.
MIN_TOP_K = max(max(RECALL_KS), max(HIT_KS), MRR_K, NDCG_K)


class Retriever(Protocol):
    """Minimal retriever contract: question in, ranked items out."""

    name: str

    def retrieve(self, query: BenchmarkQuery, top_k: int) -> list[RetrievedItem]: ...


@dataclass
class ConfigRun:
    """Everything produced by one configuration."""

    outcome: ConfigOutcome
    results: list[QueryBenchmarkResult] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)

    @property
    def executed(self) -> bool:
        return self.outcome.produced_results


def _probe_backends(config_name: str, dataset_queries: int) -> tuple[BackendAvailability, ...]:
    manifest = backends.backend_manifest(config_name, dataset_queries)
    return tuple(
        BackendAvailability(
            name=item["backend"],
            available=bool(item["available"]),
            reason=str(item["reason"]),
            detail=item["detail"],
        )
        for item in manifest
    )


def run_configuration(
    config_name: str,
    queries: Sequence[BenchmarkQuery],
    top_k: int = DEFAULT_TOP_K,
    retriever_factory: Callable[[str], Retriever] | None = None,
    force_block_reason: str | None = None,
) -> ConfigRun:
    """Execute or block one configuration, returning its outcome and metrics."""
    if top_k < MIN_TOP_K:
        raise ValueError(
            f"top_k={top_k} is below the largest reported cutoff ({MIN_TOP_K}); "
            "retrieval depth must cover every metric cutoff so Recall@K/MRR/NDCG are not understated"
        )
    if config_name not in backends.CONFIG_DESCRIPTIONS:
        outcome = ConfigOutcome(config_name, STATUS_SKIPPED, f"unknown configuration: {config_name}")
        return ConfigRun(outcome)

    if force_block_reason:
        outcome = ConfigOutcome(config_name, STATUS_SKIPPED, force_block_reason)
        return ConfigRun(outcome)

    availability = backends.evaluate_config(config_name, len(queries))
    probed = _probe_backends(config_name, len(queries))

    if retriever_factory is None:
        if availability.available:
            # Availability without an implemented executor must still not claim
            # results; surface the missing executor instead of inventing it.
            outcome = ConfigOutcome(
                config_name,
                STATUS_BLOCKED,
                "no real retrieval executor is wired for this configuration yet",
                probed,
            )
            return ConfigRun(outcome)
        outcome = ConfigOutcome(
            config_name,
            STATUS_BLOCKED,
            f"unavailable backends: {availability.describe()}",
            probed,
        )
        return ConfigRun(outcome)

    retriever = retriever_factory(config_name)
    results: list[QueryBenchmarkResult] = []
    failures: list[dict[str, str]] = []
    for query in queries:
        latency = new_latency_record()
        # A single failed query (for example a transient backend timeout) must
        # not discard every completed measurement, so the failure is recorded and
        # the remaining queries continue.
        try:
            with timed_stage(latency, "total_retrieval_ms"):
                items = retriever.retrieve(query, top_k)
        except Exception as exc:  # noqa: BLE001 - record any retriever failure
            failures.append(
                {
                    "sample_id": query.sample_id,
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:200],
                }
            )
            continue
        deduped = dedupe_preserving_rank(items)[:top_k]
        scored = score_ranking(
            [item.key for item in query.relevant_items],
            [item.key for item in deduped],
        )
        # Only end-to-end retrieval is timed. Per-stage values stay None unless a
        # real executor supplies them; copying the total into every stage would
        # fabricate per-stage timings that were never measured.
        results.append(
            QueryBenchmarkResult(
                sample_id=query.sample_id,
                question=query.question,
                business_type=query.business_type,
                difficulty=query.difficulty,
                config=config_name,
                relevant_keys=tuple(item.key for item in query.relevant_items),
                retrieved_keys=tuple(item.key for item in deduped),
                first_relevant_rank=scored["first_relevant_rank"],
                recall_at=scored["recall_at"],
                hit_at=scored["hit_at"],
                reciprocal_rank=scored["reciprocal_rank"],
                ndcg_at_10=scored["ndcg_at_10"],
                latency_ms=latency,
            )
        )
    if failures and not results:
        # Every sample failed: report the configuration as failed, never as a
        # measurement.
        outcome = ConfigOutcome(
            config_name,
            STATUS_BLOCKED,
            f"all {len(failures)} sampled queries failed during retrieval",
            probed,
        )
        return ConfigRun(outcome, [], failures)
    outcome = ConfigOutcome(config_name, STATUS_EXECUTED, "synthetic fixture retriever (not a benchmark)", probed)
    return ConfigRun(outcome, results, failures)


def summarize_run(runs: Sequence[ConfigRun]) -> dict[str, Any]:
    """Aggregate every executed configuration, keeping blocked ones visible."""
    per_config: dict[str, Any] = {}
    latency: dict[str, Any] = {}
    for run in runs:
        entry = run.outcome.as_dict()
        if run.failures:
            entry["failures"] = run.failures
            entry["failure_count"] = len(run.failures)
        if run.executed:
            entry["metrics"] = aggregate_results(run.results)
            entry["latency"] = summarize_latency([result.latency_ms for result in run.results])
            entry["stage_availability"] = stage_availability([r.latency_ms for r in run.results])
            latency[run.outcome.config_name] = entry["latency"]
        per_config[run.outcome.config_name] = entry
    executed = [run.outcome.config_name for run in runs if run.executed]
    return {
        "configs": per_config,
        "executed_configs": executed,
        "blocked_configs": [run.outcome.config_name for run in runs if not run.executed],
        "latency": latency,
        "any_results": bool(executed),
    }


def write_per_query_jsonl(path: Path, runs: Sequence[ConfigRun]) -> int:
    """Write per-query results for executed configurations only."""
    import json

    written = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for run in runs:
            if not run.executed:
                continue
            for result in run.results:
                handle.write(json.dumps(result.to_json_dict(), ensure_ascii=False) + "\n")
                written += 1
    return written


def relevance_strategy(rows: Sequence[dict[str, Any]]) -> RelevanceStrategy:
    from benchmarks.dataset import detect_relevance_level

    level = detect_relevance_level(rows)
    if level == "level1_stable_id":
        return RelevanceStrategy(level, "ground truth matched on stable document/chunk identifiers")
    return RelevanceStrategy(
        level,
        "no stable id in dataset; ground truth matched by NFKC + whitespace-normalized exact passage text",
    )
