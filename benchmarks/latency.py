"""Retrieval / rerank latency measurement.

Uses :func:`time.perf_counter`, a monotonic clock, so durations are unaffected by
wall-clock adjustments. A stage that did not run is recorded as ``None`` and is
excluded from the summary — never filled in with ``0.0``, which would imply the
stage executed and was instantaneous.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from benchmarks.models import LATENCY_STAGES


def new_latency_record() -> dict[str, float | None]:
    """All known stages, unset (``None``)."""
    return {stage: None for stage in LATENCY_STAGES}


def record_stage(record: dict[str, float | None], stage: str, duration_ms: float) -> None:
    if stage not in record:
        raise KeyError(f"unknown latency stage: {stage}")
    record[stage] = duration_ms


@contextmanager
def timed_stage(record: dict[str, float | None], stage: str) -> Iterator[None]:
    """Time a stage with the monotonic clock and store it on ``record``."""
    if stage not in record:
        raise KeyError(f"unknown latency stage: {stage}")
    start = time.perf_counter()
    try:
        yield
    finally:
        record[stage] = (time.perf_counter() - start) * 1000.0


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        raise ValueError("percentile of empty series")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = fraction * (len(sorted_values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def summarize_latency(records: list[dict[str, float | None]]) -> dict[str, dict[str, Any]]:
    """Summarize each stage across queries.

    Returns ``{stage: {count, mean, p50, p90, p95, p99, min, max}}``. Stages with
    no observation are reported with ``count == 0`` and null statistics so a
    non-executed stage is visibly absent instead of looking fast.
    """
    summary: dict[str, dict[str, Any]] = {}
    for stage in LATENCY_STAGES:
        values = sorted(record[stage] for record in records if record.get(stage) is not None)
        if not values:
            summary[stage] = {
                "count": 0,
                "mean": None,
                "p50": None,
                "p90": None,
                "p95": None,
                "p99": None,
                "min": None,
                "max": None,
            }
            continue
        summary[stage] = {
            "count": len(values),
            "mean": sum(values) / len(values),
            "p50": _percentile(values, 0.50),
            "p90": _percentile(values, 0.90),
            "p95": _percentile(values, 0.95),
            "p99": _percentile(values, 0.99),
            "min": values[0],
            "max": values[-1],
        }
    return summary


def stage_availability(records: list[dict[str, float | None]]) -> dict[str, int]:
    """How many queries actually executed each stage."""
    return {stage: sum(1 for record in records if record.get(stage) is not None) for stage in LATENCY_STAGES}
