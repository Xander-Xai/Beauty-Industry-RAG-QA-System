"""Golden-set loading for the retrieval benchmark.

Only fields that actually exist in the dataset are read. Missing metadata is
never inferred: ``visual_required`` and a stored complexity label are absent from
the current golden set, so the loader does not emit them and the benchmark does
not produce those breakdowns.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
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
                decoded = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetError(f"{path}:{line_number}: invalid JSON ({exc})") from exc
            # A bare list or string decodes fine but has no fields; rejecting it
            # here keeps the failure a DatasetError instead of a later AttributeError.
            if not isinstance(decoded, Mapping):
                raise DatasetError(
                    f"{path}:{line_number}: each line must be a JSON object, got {type(decoded).__name__}"
                )
            rows.append(decoded)
    if not rows:
        raise DatasetError(f"{path}: dataset is empty")
    return rows


def relevance_strategies(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Count rows by the relevance strategy each row will actually use.

    A dataset where only some rows carry a stable id is genuinely mixed, and
    ``load_queries`` falls back to normalized-text keys for the rows without one.
    Reporting a single strategy would overstate how much ground truth is matched
    by identifier.
    """
    with_id = 0
    without_id = 0
    for row in rows:
        has_id = any(row.get(field) not in (None, "") for field in _STABLE_ID_KEYS)
        if has_id:
            with_id += 1
        else:
            without_id += 1
    return {"level1_stable_id": with_id, "level2_normalized_exact_text": without_id}


def detect_relevance_level(rows: Sequence[dict[str, Any]]) -> str:
    """Overall relevance strategy, naming mixed datasets explicitly."""
    if not rows:
        return "level2_normalized_exact_text"
    counts = relevance_strategies(rows)
    if counts["level1_stable_id"] == 0:
        return "level2_normalized_exact_text"
    if counts["level2_normalized_exact_text"] == 0:
        return "level1_stable_id"
    return "mixed(level1_stable_id+level2_normalized_exact_text)"


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
    if limit is not None and limit <= 0:
        raise DatasetError(f"limit must be a positive integer, got {limit}")
    rows = load_rows(path)
    wanted = set(sample_ids) if sample_ids else None
    queries: list[BenchmarkQuery] = []
    seen_ids: set[str] = set()
    for index, row in enumerate(rows):
        sample_id = f"{index:04d}"
        if wanted is not None and sample_id not in wanted:
            continue
        seen_ids.add(sample_id)
        question = str(row.get("question") or "").strip()
        if not question:
            raise DatasetError(f"{path}: sample {sample_id} has no question")
        contexts = row.get("contexts")
        if not isinstance(contexts, list) or not contexts:
            raise DatasetError(f"{path}: sample {sample_id} has no contexts (ground truth)")
        # A non-string or blank entry would either become the bogus ground truth
        # "None" or raise an uncaught ValueError deep inside normalization.
        for position, item in enumerate(contexts):
            if not isinstance(item, str) or not item.strip():
                raise DatasetError(
                    f"{path}: sample {sample_id} context {position} must be a non-empty string, "
                    f"got {type(item).__name__}"
                )
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
    if wanted is not None:
        # A requested id that does not exist is a user error, not a smaller
        # experiment; silently dropping it would misreport the run's scope.
        missing = sorted(wanted - seen_ids)
        if missing:
            raise DatasetError(f"{path}: requested sample id(s) not present: {', '.join(missing)}")
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
    # Only advertise a breakdown the selected rows can actually support; load_queries
    # substitutes "unknown", so listing a zero-coverage field would present an
    # invented all-unknown table as supported metadata.
    available = ["overall"]
    available += [name for name in ("business_type", "difficulty") if counts.get(name, 0) > 0]
    return {
        "sample_count": len(rows),
        "field_coverage": counts,
        "available_buckets": available,
        "unavailable_buckets": [key for key in KNOWN_UNAVAILABLE_BUCKETS if counts.get(key, 0) == 0],
        "relevance_level": detect_relevance_level(rows),
        "relevance_strategy_counts": relevance_strategies(rows),
    }
