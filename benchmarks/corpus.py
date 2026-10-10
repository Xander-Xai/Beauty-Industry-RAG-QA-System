"""Corpus fingerprinting and golden-set correspondence checking.

Why this exists
---------------
``benchmarks/backends.py::probe_corpus`` used to answer "is the corpus usable?"
by returning ``available=False`` unconditionally, without issuing a single query.
That is honest about the current dataset (there is no committed corpus whose
chunks the golden passages map to) but it is *uninformative*: it cannot tell an
operator whether their own index would work, and it records no evidence of what
was actually inspected.

This module performs the real check instead:

1. **Fingerprint** the live index — name, document/point count and a
   deterministic SHA-256 over sampled content — so an artifact records *which*
   corpus produced it. Without this, two runs against different indexes are
   indistinguishable in their provenance.
2. **Measure correspondence** — what fraction of the golden-set passages
   actually occur in the indexed corpus. A benchmark whose corpus does not
   contain its own ground truth cannot produce a meaningful recall: every hit
   rate collapses toward zero for reasons that have nothing to do with retrieval
   quality.

It never writes to the index and never builds a corpus. Constructing an index
from the golden passages would make every configuration score recall 1.0 by
construction — that is the one shortcut this package refuses, and it is refused
here too: this module is read-only by construction (every call is a GET, a
``_count``, a ``scroll`` or a ``_search``).

Determinism
-----------
Passages are sorted before sampling and point/document ids are sorted before
hashing, so the same corpus always yields the same fingerprint and the same
correspondence verdict on any host. A fingerprint that changes between two runs
of an unchanged index is a bug here, not a property of the corpus.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from benchmarks.relevance import normalize_text

#: How many distinct golden passages to check. Sampling is over the *sorted*
#: distinct passages, so it is reproducible rather than random.
DEFAULT_CORRESPONDENCE_SAMPLE = 60

#: How many index documents/points contribute to the fingerprint digest.
DEFAULT_FINGERPRINT_SAMPLE = 2000

#: Fraction of sampled golden passages that must be present in the corpus for
#: the corpus to be usable. Anything less than complete coverage means some
#: ground truth is unretrievable by construction, which biases recall downward
#: for a reason unrelated to the retriever.
REQUIRED_CORRESPONDENCE = 1.0

CORRESPONDENCE_MET_FULL = "full"
CORRESPONDENCE_MET_SAMPLED = "sampled"


@dataclass(frozen=True)
class CorpusFingerprint:
    """What was actually inspected, recorded verbatim in the artifact."""

    system: str
    name: str
    document_count: int
    sampled_documents: int
    content_sha256: str | None
    reachable: bool
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "name": self.name,
            "document_count": self.document_count,
            "sampled_documents": self.sampled_documents,
            "content_sha256": self.content_sha256,
            "reachable": self.reachable,
            "error": self.error,
        }


@dataclass(frozen=True)
class CorrespondenceReport:
    """How much of the golden set the corpus can actually reach."""

    passages_total: int
    passages_distinct: int
    passages_checked: int
    passages_matched: int
    ratio: float
    verdict: str
    sample_hash: str
    corpus: CorpusFingerprint | None = None
    missing_examples: tuple[str, ...] = field(default=())

    def as_dict(self) -> dict[str, Any]:
        return {
            "passages_total": self.passages_total,
            "passages_distinct": self.passages_distinct,
            "passages_checked": self.passages_checked,
            "passages_matched": self.passages_matched,
            "ratio": self.ratio,
            "verdict": self.verdict,
            "sample_hash": self.sample_hash,
            "corpus": self.corpus.as_dict() if self.corpus else None,
            "missing_examples": list(self.missing_examples),
        }

    @property
    def usable(self) -> bool:
        return self.verdict == CORRESPONDENCE_MET_FULL


def distinct_passages(queries: Sequence[Any]) -> list[str]:
    """Normalized, de-duplicated golden passages across every selected query.

    Sorted so the downstream sample is reproducible rather than dependent on
    dataset ordering.
    """
    seen: dict[str, None] = {}
    for query in queries:
        for item in getattr(query, "relevant_items", ()) or ():
            text = getattr(item, "text", None)
            if not text:
                continue
            normalized = normalize_text(str(text))
            if normalized:
                seen.setdefault(normalized, None)
    return sorted(seen)


def _sample_hash(passages: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for passage in passages:
        digest.update(passage.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _fingerprint_from_hits(system: str, name: str, hits: Sequence[tuple[str, str]], total: int) -> CorpusFingerprint:
    """Digest ``(identity, content)`` pairs, order-independent.

    Sorting the pairs first means the digest depends on the corpus *contents*
    and not on the order the store happened to return them in.
    """
    ordered = sorted(hits)
    digest = hashlib.sha256()
    for identity, content in ordered:
        digest.update(identity.encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(content.encode("utf-8"))
        digest.update(b"\x1e")
    return CorpusFingerprint(
        system=system,
        name=name,
        document_count=total,
        sampled_documents=len(ordered),
        content_sha256=digest.hexdigest(),
        reachable=True,
    )


def unreachable_fingerprint(system: str, name: str, error: str) -> CorpusFingerprint:
    return CorpusFingerprint(
        system=system,
        name=name,
        document_count=0,
        sampled_documents=0,
        content_sha256=None,
        reachable=False,
        error=error,
    )


def fingerprint_elasticsearch(client: Any, index_name: str, limit: int = DEFAULT_FINGERPRINT_SAMPLE) -> CorpusFingerprint:
    """Fingerprint an Elasticsearch index by sampling its documents.

    Uses ``_search`` with ``_source: false`` plus the stored fields so the digest
    does not depend on scoring or shard ordering. Read-only.
    """
    try:
        total = int(client.count(index=index_name).get("count", 0))
    except Exception as exc:  # noqa: BLE001 - a probe must report, not raise
        return unreachable_fingerprint("elasticsearch", index_name, f"count failed: {type(exc).__name__}: {exc}")
    hits: list[tuple[str, str]] = []
    try:
        after: Any = None
        while len(hits) < limit:
            body: dict[str, Any] = {
                "size": min(500, limit - len(hits)),
                "query": {"match_all": {}},
                "_source": ["chunk_id", "doc_id", "content", "text"],
            }
            if after is not None:
                body["search_after"] = after
            response = client.search(index=index_name, body=body)
            batch = response.get("hits", {}).get("hits", [])
            if not batch:
                break
            for hit in batch:
                source = hit.get("_source") or {}
                identity = str(source.get("doc_id") or hit.get("_id"))
                content = str(source.get("content") or source.get("text") or "")
                hits.append((identity, content))
            after = batch[-1].get("sort")
            if not after:
                break
    except Exception as exc:  # noqa: BLE001
        return unreachable_fingerprint("elasticsearch", index_name, f"search failed: {type(exc).__name__}: {exc}")
    return _fingerprint_from_hits("elasticsearch", index_name, hits, total)


def fingerprint_qdrant(
    client: Any,
    collection_name: str,
    limit: int = DEFAULT_FINGERPRINT_SAMPLE,
) -> CorpusFingerprint:
    """Fingerprint a Qdrant collection by sampling its points. Read-only."""
    try:
        info = client.get_collection(collection_name)
        total = int(getattr(info, "points_count", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        return unreachable_fingerprint("qdrant", collection_name, f"get_collection failed: {type(exc).__name__}: {exc}")
    hits: list[tuple[str, str]] = []
    try:
        offset: Any = None
        while len(hits) < limit:
            body: dict[str, Any] = {"limit": min(256, limit - len(hits)), "with_payload": True, "with_vector": False}
            if offset is not None:
                body["offset"] = offset
            records, offset = client.scroll(
                collection_name=collection_name,
                scroll_filter=None,
                limit=min(256, limit - len(hits)),
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for record in records or ():
                payload = getattr(record, "payload", None) or {}
                identity = str(payload.get("doc_id") or payload.get("chunk_id") or getattr(record, "id", ""))
                content = str(payload.get("content") or payload.get("text") or "")
                hits.append((identity, content))
            if offset is None:
                break
    except Exception as exc:  # noqa: BLE001
        return unreachable_fingerprint("qdrant", collection_name, f"scroll failed: {type(exc).__name__}: {exc}")
    return _fingerprint_from_hits("qdrant", collection_name, hits, total)


def check_correspondence(
    passages_total: int,
    passages: Sequence[str],
    corpus_text: str | set[str],
    corpus: CorpusFingerprint | None = None,
    sample_size: int = DEFAULT_CORRESPONDENCE_SAMPLE,
) -> CorrespondenceReport:
    """Measure how much of the golden set the corpus actually contains.

    ``corpus_text`` is either a ``set`` of normalized passages (for a fully
    materialized sample) or the JSON text of the corpus, which is searched
    directly. The latter is what a streaming reader uses so a multi-million
    document index does not have to be held in memory.
    """
    distinct = sorted({normalize_text(passage) for passage in passages if normalize_text(passage)})
    sample = distinct if len(distinct) <= sample_size else distinct[:sample_size]
    is_full_scan = len(distinct) <= sample_size

    # `in` works for both container types: a set lookup for a fully materialized
    # sample, a substring scan over the corpus text for a streamed one.
    matched = [passage for passage in sample if passage in corpus_text]
    missing = [passage for passage in sample if passage not in corpus_text]

    ratio = (len(matched) / len(sample)) if sample else 0.0
    # A sampled scan is never enough to declare a corpus complete: the sample is
    # a subset, so "all sampled passages matched" does not prove the rest do.
    verdict = CORRESPONDENCE_MET_FULL if (is_full_scan and ratio >= REQUIRED_CORRESPONDENCE) else CORRESPONDENCE_MET_SAMPLED
    return CorrespondenceReport(
        passages_total=passages_total,
        passages_distinct=len(distinct),
        passages_checked=len(sample),
        passages_matched=len(matched),
        ratio=ratio,
        verdict=verdict,
        sample_hash=_sample_hash(sample),
        corpus=corpus,
        missing_examples=tuple(passage[:120] for passage in missing[:3]),
    )


@dataclass(frozen=True)
class CorpusInspection:
    """The combined verdict across every inspected store."""

    fingerprints: tuple[CorpusFingerprint, ...]
    report: CorrespondenceReport

    @property
    def verdict(self) -> str:
        return self.report.verdict

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "fingerprints": [fingerprint.as_dict() for fingerprint in self.fingerprints],
            "correspondence": self.report.as_dict(),
        }


def _es_client():
    """Build an Elasticsearch client from operator configuration.

    Returns ``None`` when the client cannot be constructed; the caller records
    that as an unreachable fingerprint rather than treating it as an empty index.
    """
    try:
        from common.config import get_config_dict

        config = get_config_dict().get("elasticsearch", {}) or {}
        username = os.environ.get("ELASTICSEARCH_USERNAME") or config.get("username")
        password = os.environ.get("ELASTICSEARCH_PASSWORD") or config.get("password")
        host = os.environ.get("ELASTICSEARCH_HOST") or config.get("host") or "http://localhost:9200"
        from elasticsearch import Elasticsearch

        auth = (username, password) if username and password else None
        return Elasticsearch(host, basic_auth=auth, request_timeout=5)
    except Exception:  # noqa: BLE001 - a missing client is a probe result, not a crash
        return None


def _qdrant_client():
    try:
        from qdrant_client import QdrantClient

        from common.config import get_config_dict

        config = get_config_dict()
        qdrant_config = config.get("qdrant", {}) or {}
        return QdrantClient(
            host=qdrant_config.get("host", "localhost"),
            port=int(qdrant_config.get("port", 6333)),
            timeout=5,
        )
    except Exception:  # noqa: BLE001
        return None


def inspect_correspondence(
    passages: Sequence[str],
    total_passages: int = 0,
    sample_size: int = DEFAULT_CORRESPONDENCE_SAMPLE,
    limit: int = DEFAULT_FINGERPRINT_SAMPLE,
) -> CorpusInspection:
    """Fingerprint every configured store and measure golden-set correspondence.

    Read-only end to end. Any store that cannot be reached is reported as an
    unreachable fingerprint and contributes no text, so an unreachable backend
    lowers the measured correspondence rather than being silently skipped — a
    missing store must never look like a corpus that simply lacks the passages.
    """
    fingerprints: list[CorpusFingerprint] = []
    haystack_parts: list[str] = []

    es_client = _es_client()
    if es_client is None:
        fingerprints.append(unreachable_fingerprint("elasticsearch", "<unconfigured>", "client unavailable"))
    else:
        index_name = _es_index_name()
        fingerprint = fingerprint_elasticsearch(es_client, index_name, limit=limit)
        fingerprints.append(fingerprint)
        if fingerprint.reachable:
            haystack_parts.append(_es_corpus_text(es_client, index_name, limit=limit))

    qdrant_client = _qdrant_client()
    if qdrant_client is None:
        fingerprints.append(unreachable_fingerprint("qdrant", "<unconfigured>", "client unavailable"))
    else:
        collection = _qdrant_collection_name()
        fingerprint = fingerprint_qdrant(qdrant_client, collection, limit=limit)
        fingerprints.append(fingerprint)
        if fingerprint.reachable:
            haystack_parts.append(_qdrant_corpus_text(qdrant_client, collection, limit=limit))

    corpus_text = "\n".join(haystack_parts)
    report = check_correspondence(
        passages_total=total_passages or len(passages),
        passages=passages,
        corpus_text=corpus_text,
        corpus=next((f for f in fingerprints if f.reachable), None),
        sample_size=sample_size,
    )
    return CorpusInspection(fingerprints=tuple(fingerprints), report=report)


def _es_index_name() -> str:
    try:
        from common.config import get_config_dict

        config = get_config_dict().get("elasticsearch", {}) or {}
        return str(os.environ.get("ELASTICSEARCH_INDEX") or config.get("index") or "cosmetics_docs")
    except Exception:  # noqa: BLE001
        return "cosmetics_docs"


def _qdrant_collection_name() -> str:
    try:
        from common.config import get_config_dict

        config = get_config_dict()
        text_config = ((config.get("embedding") or {}).get("text") or {})
        return str(text_config.get("collection") or "rag_text_768")
    except Exception:  # noqa: BLE001
        return "rag_text_768"


def _es_corpus_text(client: Any, index_name: str, limit: int = DEFAULT_FINGERPRINT_SAMPLE) -> str:
    """Concatenate normalized passage text from a bounded document sample."""
    chunks: list[str] = []
    try:
        after: Any = None
        while len(chunks) < limit:
            body: dict[str, Any] = {
                "size": min(500, limit - len(chunks)),
                "query": {"match_all": {}},
                "_source": ["content", "text", "chunk_text"],
            }
            if after is not None:
                body["search_after"] = after
            response = client.search(index=index_name, body=body)
            batch = response.get("hits", {}).get("hits", [])
            if not batch:
                break
            for hit in batch:
                source = hit.get("_source") or {}
                text = source.get("content") or source.get("text") or source.get("chunk_text")
                if text:
                    chunks.append(normalize_text(str(text)))
            after = batch[-1].get("sort")
            if not after:
                break
    except Exception:  # noqa: BLE001 - a partial scan must not become a false match
        return ""
    return "\n".join(chunks)


def _qdrant_corpus_text(client: Any, collection_name: str, limit: int = DEFAULT_FINGERPRINT_SAMPLE) -> str:
    chunks: list[str] = []
    try:
        offset: Any = None
        while len(chunks) < limit:
            records, offset = client.scroll(
                collection_name=collection_name,
                scroll_filter=None,
                limit=min(256, limit - len(chunks)),
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for record in records or ():
                payload = getattr(record, "payload", None) or {}
                text = payload.get("content") or payload.get("text")
                if text:
                    chunks.append(normalize_text(str(text)))
            if offset is None:
                break
    except Exception:  # noqa: BLE001
        return ""
    return "\n".join(chunks)


def report_to_json(report: CorrespondenceReport) -> str:
    return json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
