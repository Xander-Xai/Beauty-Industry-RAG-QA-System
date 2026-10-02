"""Golden-set loading for the retrieval benchmark.

Only fields that actually exist in the dataset are read. Missing metadata is
never inferred: ``visual_required`` and a stored complexity label are absent from
the current golden set, so the loader does not emit them and the benchmark does
not produce those breakdowns.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from benchmarks.models import BenchmarkQuery, RelevantItem
from benchmarks.relevance import STABLE_ID_FIELDS, relevant_items_from_texts, stable_id_from

DEFAULT_DATASET_PATH = "tests/evaluation/golden_set.jsonl"

# Fields that would be required for Level-1 (stable id) relevance matching.
_STABLE_ID_KEYS = tuple(field for field in STABLE_ID_FIELDS)

# Labels reported by the loader so downstream artifacts can state honestly which
# breakdowns exist and which do not.
KNOWN_UNAVAILABLE_BUCKETS = ("visual_required", "complexity")


class DatasetError(RuntimeError):
    """Raised when the dataset cannot support a trustworthy benchmark."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise DatasetError(f"{path}:{line_number}: invalid JSON ({exc})") from exc
    if not rows:
        raise DatasetError(f"{path}: dataset is empty")
    return rows


def detect_relevance_level(rows: Sequence[dict[str, Any]]) -> str:
    """Whether ground truth can be matched by stable id (``level1``) or only by text."""
    if not rows:
        return "level2_normalized_exact_text"
    if any(row.get(_STABLE_ID_KEYS) for row in rows):
        return "level1_stable_id"
    return "level2_normalized_exact_text"


def coverage(rows: Sequence[dict[str, Any]], keys: Sequence[str]) -> dict[str, int]:
    return {key: sum(1 for row in rows if row.get(key) not in (None, "", [])) for key in keys}


def load_queries(
    path: str | Path = DEFAULT_DATASET_PATH,
    limit: int | None = None,
    sample_ids: Sequence[str] | None = None,
) -> list[BenchmarkQuery]:
    """Load benchmark queries with deterministic sample ids.

    ``sample_id`` is derived from the 1-based line number because the dataset has
    no identifier column. The same dataset therefore always yields the same ids.
    """
    rows = load_rows(path)
    wanted = set(sample_ids) if sample_ids else None
    queries: list[BenchmarkQuery] = []
    for index, row in enumerate(rows):
        sample_id = f"{index:04d}"
        if wanted is not None and sample_id not in wanted:
            continue
        question = str(row.get("question") or "").strip()
        if not question:
            raise DatasetError(f"{path}: sample {sample_id} has no question")
        contexts = row.get("contexts")
        if not isinstance(contexts, list) or not contexts:
            raise DatasetError(f"{path}: sample {sample_id} has no contexts (ground truth)")
        stable = stable_id_from(row)
        if stable:
            relevant: list[RelevantItem] = [RelevantItem(key=stable, text=str(contexts[0]))]
        else:
            relevant = relevant_items_from_texts([str(item) for item in contexts])
        queries.append(
            BenchmarkQuery(
                sample_id=sample_id,
                question=question,
                business_type=str(row.get("business_type") or "unknown"),
                difficulty=str(row.get("difficulty") or "unknown"),
                relevant_items=tuple(relevant),
                raw=row,
            )
        )
        if limit is not None and len(queries) >= limit:
            break
    if not queries:
        raise DatasetError(f"{path}: no samples selected")
    return queries


def sample_ids_hash(queries: Sequence[BenchmarkQuery]) -> str:
    digest = hashlib.sha256()
    for query in queries:
        digest.update(query.sample_id.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def bucket_coverage(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Field coverage used to state which breakdowns are supported."""
    keys = ("question", "answer", "ground_truth", "contexts", "business_type", "difficulty") + KNOWN_UNAVAILABLE_BUCKETS
    counts = coverage(rows, keys)
    return {
        "sample_count": len(rows),
        "field_coverage": counts,
        "available_buckets": ["overall", "business_type", "difficulty"],
        "unavailable_buckets": [key for key in KNOWN_UNAVAILABLE_BUCKETS if counts.get(key, 0) == 0],
        "relevance_level": detect_relevance_level(rows),
    }
