"""Golden-set v2 data contract and fail-closed validation.

Why this exists
---------------
The committed golden set (`tests/evaluation/golden_set.jsonl`) carries only
``question`` / ``answer`` / ``contexts`` / ``ground_truth`` / ``business_type`` /
``difficulty``. It has **no** stable document identity (``doc_id`` / ``chunk_id``),
no ``corpus_version``, no ``visual_required`` and no ``complexity_label``
(measured 0 / 301 — see ``docs/benchmark-data-quality.md`` and issue #86).

Without stable identity and provenance, a retrieval benchmark can only fall back
to normalized-exact-text matching, which silently degrades to recall ≈ 0 when the
offline chunker's window phase or the knowledge epoch shifts — a retrieval
*collapse* becomes indistinguishable from a relevance *alignment* failure. This
module makes that state explicit and refuses to treat such a dataset as evidence.

The contract, in one sentence: **a row is evidence only when every one of its
annotated passages resolves to a real indexed ``(doc_id, chunk_id)`` in a named
``corpus_version``, and ``visual_required`` / ``complexity_label`` carry a
recorded human/refference decision.**

Nothing here guesses a missing label or fabricates an identifier. A row that
cannot satisfy a clause is marked ``INVALID`` with a machine-readable reason, and
:func:`require_attributable` raises rather than let an invalid dataset be scored
into an official-looking percentage.

Not used anywhere: an LLM judge, embedding-similarity thresholds, fuzzy matching,
or a retriever's own output as ground truth. A resolver only answers "does this
``(doc_id, chunk_id)`` exist in the named corpus version" — it must never decide
relevance from a retrieval result.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.annotation import (
    ANNOTATION_SOURCES,
    REVIEW_STATUS_REVIEWED,
    REVIEW_STATUSES,
)
from benchmarks.models import RELEVANCE_GRADES

CONTRACT_VERSION = "golden-set-contract/v2"

STATUS_VALID = "VALID"
STATUS_INVALID = "INVALID"

VALID_BUSINESS_TYPES = ("ingredient", "regulation", "formula", "image", "general", "product")
VALID_DIFFICULTIES = ("easy", "medium", "hard")
VALID_COMPLEXITY = ("simple", "complex")

#: A resolver answers one question only: does ``(doc_id, chunk_id)`` exist in the
#: index for ``corpus_version``? It must not return relevance.
IdentifierResolver = Callable[[str, str, str], bool]

_CORPUS_VERSION_RE = re.compile(r"^[A-Za-z0-9_.\-]{2,64}$")


class DatasetContractError(RuntimeError):
    """Raised when a dataset cannot support an attributable benchmark."""


@dataclass(frozen=True)
class RowVerdict:
    """Per-row contract verdict with machine-readable reasons."""

    sample_id: str
    status: str
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"sample_id": self.sample_id, "status": self.status, "reasons": list(self.reasons)}


@dataclass(frozen=True)
class ContractReport:
    """Dataset-level contract result plus measured field coverage."""

    contract_version: str
    total: int
    valid_count: int
    invalid_count: int
    verdicts: tuple[RowVerdict, ...]
    coverage: dict[str, Any] = field(default_factory=dict)

    @property
    def valid_fraction(self) -> float:
        return self.valid_count / self.total if self.total else 0.0

    def invalid_reasons(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for verdict in self.verdicts:
            if verdict.status == STATUS_INVALID:
                for reason in verdict.reasons:
                    counts[reason] = counts.get(reason, 0) + 1
        return counts

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "total": self.total,
            "valid_count": self.valid_count,
            "invalid_count": self.invalid_count,
            "valid_fraction": self.valid_fraction,
            "coverage": self.coverage,
            "invalid_reasons": self.invalid_reasons(),
            "verdicts": [v.as_dict() for v in self.verdicts],
        }


def dataset_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def load_contract_rows(path: str | Path) -> list[dict[str, Any]]:
    """Load a JSONL dataset, rejecting non-object lines early."""
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetContractError(f"{path}:{line_number}: invalid JSON ({exc})") from exc
            if not isinstance(decoded, Mapping):
                raise DatasetContractError(f"{path}:{line_number}: each line must be a JSON object")
            rows.append(dict(decoded))
    if not rows:
        raise DatasetContractError(f"{path}: dataset is empty")
    return rows


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_row(row: Mapping[str, Any], resolver: IdentifierResolver | None = None) -> RowVerdict:
    """Validate one row against the v2 contract. Never guesses a missing field."""
    sample_id = str(row.get("sample_id") or row.get("id") or "<unidentified>")
    reasons: list[str] = []

    if not _is_nonempty_str(row.get("sample_id")):
        reasons.append("missing_sample_id")
    if not _is_nonempty_str(row.get("question")):
        reasons.append("missing_question")

    business_type = row.get("business_type")
    if business_type not in VALID_BUSINESS_TYPES:
        reasons.append("invalid_business_type")
    difficulty = row.get("difficulty")
    if difficulty not in VALID_DIFFICULTIES:
        reasons.append("invalid_difficulty")

    # visual_required must be an explicit boolean decision, never derived.
    if not isinstance(row.get("visual_required"), bool):
        reasons.append("missing_visual_required_label")
    if row.get("complexity_label") not in VALID_COMPLEXITY:
        reasons.append("missing_complexity_label")

    corpus_version = row.get("corpus_version")
    if not _is_nonempty_str(corpus_version) or not _CORPUS_VERSION_RE.match(str(corpus_version)):
        reasons.append("missing_or_invalid_corpus_version")

    annotations = row.get("annotations")
    if not isinstance(annotations, list) or not annotations:
        reasons.append("missing_annotations")
    else:
        for position, annotation in enumerate(annotations):
            if not isinstance(annotation, Mapping):
                reasons.append(f"annotation_{position}_not_an_object")
                continue
            for required in ("doc_id", "chunk_id"):
                if not _is_nonempty_str(annotation.get(required)):
                    reasons.append(f"annotation_{position}_missing_{required}")
            if not _is_nonempty_str(annotation.get("text")):
                reasons.append(f"annotation_{position}_missing_text")
        # Identifier resolution is only attempted when the shape is sound, so a
        # missing-id row is reported as such rather than as an index mismatch.
        if resolver is not None and not any(r.startswith("annotation_") for r in reasons):
            for position, annotation in enumerate(annotations):
                doc_id = str(annotation["doc_id"])
                chunk_id = str(annotation["chunk_id"])
                if not resolver(doc_id, chunk_id, str(corpus_version)):
                    reasons.append(f"annotation_{position}_unresolved:{doc_id}:{chunk_id}")

    provenance = row.get("annotation")
    if not isinstance(provenance, Mapping):
        reasons.append("missing_annotation_provenance")
    else:
        for required in ("annotator", "method", "annotated_at", "reviewed_by"):
            if not _is_nonempty_str(provenance.get(required)):
                reasons.append(f"missing_annotation_{required}")
        # Review state is mandatory and has no default. Without it a record
        # generated by a model is indistinguishable from one a person checked,
        # so an unverified candidate could score as ground truth. See
        # `benchmarks/annotation.py` for the full lifecycle.
        review_status = provenance.get("review_status")
        if not _is_nonempty_str(review_status):
            reasons.append("missing_annotation_review_status")
        elif review_status not in REVIEW_STATUSES:
            reasons.append("invalid_annotation_review_status")
        elif review_status != REVIEW_STATUS_REVIEWED:
            # A draft is a real annotation state, not an error — but it must
            # never be scored, so this row is refused from official runs.
            reasons.append(f"annotation_not_reviewed:{review_status}")
        # Required for the same reason as review_status: without the origin of a
        # label, an LLM proposal and a human decision cannot be told apart later.
        source = provenance.get("source")
        if not _is_nonempty_str(source):
            reasons.append("missing_annotation_source")
        elif source not in ANNOTATION_SOURCES:
            reasons.append("invalid_annotation_source")

    # Relevance grades are optional; when present they must be a real level.
    for position, annotation in enumerate(row.get("annotations") or []):
        if not isinstance(annotation, Mapping):
            continue
        grade = annotation.get("relevance")
        if grade is None:
            continue
        if isinstance(grade, bool) or not isinstance(grade, int) or grade not in RELEVANCE_GRADES:
            reasons.append(f"annotation_{position}_invalid_relevance_grade")

    status = STATUS_VALID if not reasons else STATUS_INVALID
    return RowVerdict(sample_id=sample_id, status=status, reasons=tuple(reasons))


def validate_dataset(
    rows: Sequence[Mapping[str, Any]],
    resolver: IdentifierResolver | None = None,
    min_valid_fraction: float = 1.0,
) -> ContractReport:
    """Validate every row and compute coverage; do not raise here.

    :func:`require_attributable` is the raising gate. Keeping validation and the
    gate separate lets a caller report why a dataset is unusable.
    """
    verdicts = tuple(validate_row(row, resolver=resolver) for row in rows)
    valid_count = sum(1 for verdict in verdicts if verdict.status == STATUS_VALID)
    coverage = _coverage(rows)
    return ContractReport(
        contract_version=CONTRACT_VERSION,
        total=len(rows),
        valid_count=valid_count,
        invalid_count=len(rows) - valid_count,
        verdicts=verdicts,
        coverage=coverage,
    )


def _coverage(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    fields = (
        "sample_id",
        "question",
        "visual_required",
        "complexity_label",
        "corpus_version",
        "annotations",
        "annotation",
    )
    counts = {field: sum(1 for row in rows if row.get(field) not in (None, "", [])) for field in fields}
    business_type: dict[str, int] = {}
    for row in rows:
        key = str(row.get("business_type") or "unspecified")
        business_type[key] = business_type.get(key, 0) + 1
    return {
        "sample_count": total,
        "field_coverage": counts,
        "business_type": business_type,
        "visual_required_true": sum(1 for row in rows if row.get("visual_required") is True),
        "visual_required_false": sum(1 for row in rows if row.get("visual_required") is False),
    }


def require_attributable(report: ContractReport, min_valid_fraction: float = 1.0) -> None:
    """Raise unless the dataset is attributable enough to be scored.

    This is the fail-closed gate: an evaluation pipeline calls it *before*
    computing any percentage, so a degraded dataset cannot silently produce an
    official-looking number.
    """
    if report.total == 0:
        raise DatasetContractError("dataset is empty; nothing can be scored")
    if report.valid_count == 0:
        counts = report.invalid_reasons()
        raise DatasetContractError(
            f"0/{report.total} samples satisfy the {CONTRACT_VERSION} contract (reasons: {counts}); refusing to score"
        )
    if report.valid_fraction < min_valid_fraction:
        raise DatasetContractError(
            f"only {report.valid_count}/{report.total} samples are contract-valid "
            f"({report.valid_fraction:.3f} < required {min_valid_fraction:.3f}); refusing to score"
        )


def qdrant_resolver(client: Any, collection_name: str) -> IdentifierResolver:
    """Build a resolver against a real Qdrant collection.

    The resolver checks existence only. It does **not** run a search, so a
    retriever's ranking can never influence ground truth. Points are written by
    the offline pipeline with a ``doc_id`` payload; ``chunk_id`` is the logical
    chunk identity.
    """

    def _resolve(doc_id: str, chunk_id: str, corpus_version: str) -> bool:
        from qdrant_client.http.models import FieldCondition, Filter, MatchValue

        records, _ = client.scroll(
            collection_name=collection_name,
            scroll_filter=Filter(
                must=[
                    FieldCondition(key="doc_id", match=MatchValue(value=doc_id)),
                    FieldCondition(key="chunk_id", match=MatchValue(value=chunk_id)),
                    FieldCondition(key="doc_version_epoch", match=MatchValue(value=corpus_version)),
                ]
            ),
            limit=1,
            with_payload=False,
        )
        return bool(records)

    return _resolve


def elasticsearch_resolver(client: Any, index_name: str) -> IdentifierResolver:
    """Build an existence-only resolver against a real Elasticsearch index."""

    def _resolve(doc_id: str, chunk_id: str, corpus_version: str) -> bool:
        response = client.count(
            index=index_name,
            query={
                "bool": {
                    "must": [
                        {"term": {"doc_id": doc_id}},
                        {"term": {"chunk_id": chunk_id}},
                        {"term": {"doc_version_epoch": corpus_version}},
                    ]
                }
            },
        )
        return int(response.get("count", 0)) > 0

    return _resolve


def report_to_json(report: ContractReport) -> str:
    return json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
