"""Deterministic relevance matching.

Two levels are supported, in this order of preference:

``Level 1 — stable identifier``
    Used when the dataset exposes ``doc_id`` / ``chunk_id`` / ``source_id`` that
    can also be read off a retrieval result. This is exact and language
    independent.

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
from dataclasses import dataclass

from benchmarks.models import RetrievedItem

_WHITESPACE_RE = re.compile(r"\s+")

# Identifier fields, in priority order, that would be treated as a stable
# ground-truth identity if a dataset ever provided them.
STABLE_ID_FIELDS = ("doc_id", "chunk_id", "source_id")


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


def stable_id_from(payload: dict) -> str | None:
    """Return the first available stable identifier in a mapping, if any."""
    for field in STABLE_ID_FIELDS:
        value = payload.get(field)
        if value not in (None, ""):
            return str(value)
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


def relevant_items_from_texts(texts: list[str]) -> list:
    """Build :class:`~benchmarks.models.RelevantItem` objects from raw texts."""
    from benchmarks.models import RelevantItem

    items = []
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
