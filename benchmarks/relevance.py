"""Deterministic relevance matching.

Two levels are supported, in this order of preference:

``Level 1 — stable identifier``
    Used when the dataset exposes ``doc_id`` / ``chunk_id`` / ``source_id`` that
    can also be read off a retrieval result. This is exact and language
    independent. Identity is read through :func:`relevance_key`, which qualifies
    ``chunk_id`` with ``doc_id`` so that two chunks of the same document stay
    distinguishable (see :func:`relevance_key`).

``Level 2 — normalized exact text``
    Used when no shared stable identifier exists (the current situation for
    ``tests/evaluation/golden_set.jsonl``, which has no identifiers at all).
    The match is normalized exact text only:
    Unicode NFKC normalization, whitespace collapsing and trimming.

Deliberately not used anywhere in this package: LLM judges, embedding similarity
thresholds, fuzzy/edit-distance thresholds and hand-written id mappings. Those
can inflate recall and are not reproducible ground truth, so they are rejected
rather than tuned.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from benchmarks.models import (
    GRADE_THRESHOLD_RELEVANT,
    RELEVANCE_GRADE_NAMES,
    RELEVANCE_GRADES,
    RELEVANCE_HIGHLY_RELEVANT,
    RELEVANCE_NOT_RELEVANT,
    RELEVANCE_PARTIALLY_RELEVANT,
    RelevantItem,
    RetrievedItem,
)

_WHITESPACE_RE = re.compile(r"\s+")

# Identifier fields, in priority order, that would be treated as a stable
# ground-truth identity if a dataset ever provided them.
STABLE_ID_FIELDS = ("doc_id", "chunk_id", "source_id")

#: Separator for the composite ``doc_id`` + ``chunk_id`` identity. Chosen so a
#: composite key can never collide with a bare id that happens to contain it.
KEY_SEPARATOR = "::"

__all__ = [
    "GRADE_THRESHOLD_RELEVANT",
    "KEY_SEPARATOR",
    "RELEVANCE_GRADES",
    "RELEVANCE_GRADE_NAMES",
    "RELEVANCE_HIGHLY_RELEVANT",
    "RELEVANCE_NOT_RELEVANT",
    "RELEVANCE_PARTIALLY_RELEVANT",
    "STABLE_ID_FIELDS",
    "RelevanceStrategy",
    "dedupe_preserving_rank",
    "has_stable_identity",
    "keys_from_texts",
    "normalize_text",
    "relevance_key",
    "relevant_items_from_annotations",
    "relevant_items_from_contexts",
    "relevant_items_from_texts",
    "retrieve_keys",
    "stable_id_from",
    "text_key",
]


def normalize_text(text: str) -> str:
    """Deterministic normalization used for exact text relevance matching.

    NFKC + whitespace collapse + trim. No case folding (the corpus is Chinese
    and case folding would be ambiguous across languages), no stemming, no
    punctuation removal — only whitespace and Unicode compatibility changes.
    """
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def text_key(text: str) -> str:
    """Relevance key for a passage when no stable identifier is available."""
    return normalize_text(text)


def stable_id_from(payload: Mapping) -> str | None:
    """Return the first available stable identifier in a mapping, if any.

    Kept for backward compatibility with the existing level-1 tests and with
    payloads that expose exactly one identifier field. New code that must decide
    *which passage* something is should use :func:`relevance_key` instead — this
    function prefers ``doc_id``, which is coarse: every chunk of one document
    shares it.
    """
    for field in STABLE_ID_FIELDS:
        value = payload.get(field)
        if value not in (None, ""):
            return str(value)
    return None


def relevance_key(payload: Mapping) -> str | None:
    """Canonical identity of a passage, used by **both** sides of a comparison.

    A document is chunked, so ``doc_id`` alone does not identify a passage: every
    chunk of ``doc-1`` would share the key ``doc-1``, and a retriever returning
    one chunk would score as a hit for every annotated chunk of that document.
    The key therefore prefers the most specific identifier available and
    qualifies ``chunk_id`` with ``doc_id`` when both are present, giving
    ``"doc-1::chunk-3"``.

    Ground truth (:func:`relevant_items_from_annotations`) and retrieved items
    must both call this function; if they disagree on what "the same passage"
    means, every metric silently reads zero rather than failing loudly.
    """
    if not isinstance(payload, Mapping):
        return None
    doc_id = payload.get("doc_id")
    chunk_id = payload.get("chunk_id")
    if chunk_id not in (None, ""):
        if doc_id not in (None, ""):
            return f"{doc_id}{KEY_SEPARATOR}{chunk_id}"
        return str(chunk_id)
    if doc_id not in (None, ""):
        return str(doc_id)
    source_id = payload.get("source_id")
    if source_id not in (None, ""):
        return str(source_id)
    return None


@dataclass(frozen=True)
class RelevanceStrategy:
    """How ground truth was matched against retrieved items."""

    level: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level, "detail": self.detail}


def retrieve_keys(items: list[RetrievedItem]) -> list[str]:
    """Ordered relevance keys for a ranked retrieval result."""
    return [item.key for item in items]


def keys_from_texts(texts: list[str]) -> list[str]:
    """Ordered relevance keys for ground-truth passages given as raw text."""
    keys: list[str] = []
    seen: set[str] = set()
    for text in texts:
        key = text_key(text)
        if not key or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


def relevant_items_from_texts(texts: list[str]) -> list[RelevantItem]:
    """Build :class:`~benchmarks.models.RelevantItem` objects from raw texts.

    Every passage becomes its own item: a query with four ground-truth passages
    must be scored against four passages, not against whichever one came first.
    """
    items: list[RelevantItem] = []
    seen: set[str] = set()
    for text in texts:
        key = text_key(text)
        if not key or key in seen:
            continue
        seen.add(key)
        items.append(RelevantItem(key=key, text=text))
    if not items:
        raise ValueError("ground truth produced no usable passage after normalization")
    return items


def relevant_items_from_contexts(contexts: Sequence) -> list[RelevantItem]:
    """Build ground-truth items from a ``contexts`` list of any supported shape.

    ``contexts`` entries may be either

    * a plain passage ``str`` — level 2, matched by normalized exact text; or
    * a mapping carrying ``doc_id`` / ``chunk_id`` / ``text`` (and an optional
      ``relevance`` grade) — level 1, matched by :func:`relevance_key`.

    Mixed entries are allowed; each entry is keyed by the strongest identity it
    actually carries, and duplicates are collapsed keeping the first occurrence
    so a passage repeated in the list is counted once.
    """
    items: list[RelevantItem] = []
    seen: set[str] = set()
    for position, entry in enumerate(contexts or []):
        if isinstance(entry, Mapping):
            key = relevance_key(entry)
            text = str(entry.get("text") or "")
            grade = entry.get("relevance", RELEVANCE_HIGHLY_RELEVANT)
        else:
            key = text_key(str(entry))
            text = str(entry)
            grade = RELEVANCE_HIGHLY_RELEVANT
        if not key or key in seen:
            continue
        if not isinstance(grade, int) or isinstance(grade, bool) or grade not in RELEVANCE_GRADES:
            raise ValueError(
                f"context {position}: relevance grade {grade!r} is not one of {list(RELEVANCE_GRADES)}"
            )
        if grade < GRADE_THRESHOLD_RELEVANT:
            continue
        seen.add(key)
        items.append(RelevantItem(key=key, text=text, relevance=grade))
    if not items:
        raise ValueError("ground truth produced no usable passage after normalization")
    return items


def relevant_items_from_annotations(annotations: Sequence) -> list[RelevantItem]:
    """Build ground-truth items from ``golden-set-contract/v2`` annotations.

    The v2 contract stores identity in ``annotations[{doc_id, chunk_id, text}]``,
    not at the row top level. Without this the contract could report a row as
    attributable while the relevance layer still matched it by normalized text —
    which is exactly the level-2 fallback the contract exists to prevent.

    Every annotation becomes its own relevant item. A query commonly has several
    supporting passages (1081 ground-truth passages across 301 rows, 262
    distinct ones — see ``docs/benchmark-data-quality.md``); collapsing them to
    the first one would inflate Recall@5 from ``found / passages`` to a
    guaranteed 1.0.

    Identity uses :func:`relevance_key`, the same function a retrieval executor
    must use, so ground truth and retriever output agree on what "the same
    passage" means.

    An optional ``relevance`` grade (see :data:`RELEVANCE_GRADES`) defaults to
    :data:`RELEVANCE_HIGHLY_RELEVANT`, which reproduces the binary behaviour a
    grade-free dataset had. Grades below :data:`GRADE_THRESHOLD_RELEVANT` are
    recorded by the annotator but are not relevant passages, so they are
    excluded here rather than counted as retrievable evidence.
    """
    items: list[RelevantItem] = []
    seen: set[str] = set()
    for annotation in annotations or []:
        if not isinstance(annotation, Mapping):
            continue
        key = relevance_key(annotation)
        if not key or key in seen:
            continue
        grade = annotation.get("relevance", RELEVANCE_HIGHLY_RELEVANT)
        if not isinstance(grade, int) or isinstance(grade, bool) or grade not in RELEVANCE_GRADES:
            # An unknown grade is not silently coerced to "relevant": that would
            # add evidence the annotator never asserted.
            continue
        if grade < GRADE_THRESHOLD_RELEVANT:
            continue
        seen.add(key)
        items.append(
            RelevantItem(key=key, text=str(annotation.get("text") or ""), relevance=grade)
        )
    return items


def has_stable_identity(row) -> bool:
    """True when the row carries a stable id the relevance layer can match on.

    Covers both the v2 ``annotations`` location and a flat top-level
    ``doc_id`` / ``chunk_id`` / ``source_id``.
    """
    annotations = row.get("annotations") if isinstance(row, Mapping) else None
    if isinstance(annotations, list) and any(
        isinstance(annotation, Mapping) and relevance_key(annotation) for annotation in annotations
    ):
        return True
    return bool(relevance_key(row)) if isinstance(row, Mapping) else False


def dedupe_preserving_rank(items: list[RetrievedItem]) -> list[RetrievedItem]:
    """Drop duplicate relevance keys, keeping the best (earliest) rank."""
    seen: set[str] = set()
    deduped: list[RetrievedItem] = []
    for item in items:
        if not item.key or item.key in seen:
            continue
        seen.add(item.key)
        deduped.append(item)
    for position, item in enumerate(deduped, start=1):
        deduped[position - 1] = RetrievedItem(
            rank=position,
            key=item.key,
            score=item.score,
            source=item.source,
            text=item.text,
        )
    return deduped
