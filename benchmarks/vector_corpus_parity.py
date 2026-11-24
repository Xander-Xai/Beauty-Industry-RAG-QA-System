"""Full ES/Qdrant payload parity for an isolated dense benchmark.

Read-only, bounded, fail closed. The existing ES source approval is necessary,
not sufficient: the actual vector collection must carry the same chunk
identities, contents, access fields and provenance. This audits payloads and stored vector fingerprints,
not proof of embedding-model identity or a transactional snapshot.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from benchmarks.source_approval import _index_content_fingerprint

MAX_DOCUMENTS = 10_000
_FIELDS = ("doc_id", "chunk_id", "doc_version_epoch", "content", "role_mask", "dept_mask", "status", "provenance")


def _canonical(documents: list[dict]) -> dict[tuple[str, str], str]:
    result = {}
    for row in documents:
        if any(key not in row for key in _FIELDS):
            raise ValueError("missing_payload_fields")
        identity = (row["doc_id"], row["chunk_id"])
        if any(not isinstance(value, str) or not value for value in identity) or identity in result:
            raise ValueError("invalid_or_duplicate_identity")
        if not isinstance(row["content"], str) or not row["content"] or not isinstance(row["provenance"], dict):
            raise ValueError("invalid_payload")
        result[identity] = json.dumps({key: row[key] for key in _FIELDS}, sort_keys=True, ensure_ascii=False)
    return result


def compare_payloads(es_rows: list[dict], vector_rows: list[dict], approval: dict) -> dict:
    """Require a complete population bound to the approved ES identity digest."""
    if approval.get("verified") is not True:
        raise ValueError("es_approval_missing")
    if not 0 < len(es_rows) == approval.get("document_count") <= MAX_DOCUMENTS:
        raise ValueError("es_count_mismatch")
    if _index_content_fingerprint(es_rows) != approval.get("content_sha256"):
        raise ValueError("es_approved_identity_mismatch")
    left, right = _canonical(es_rows), _canonical(vector_rows)
    if left.keys() != right.keys():
        raise ValueError("vector_population_mismatch")
    if left != right:
        raise ValueError("vector_payload_mismatch")
    digest = hashlib.sha256()
    for identity in sorted(left):
        digest.update(left[identity].encode("utf-8"))
        digest.update(b"\n")
    return {"verified": True, "document_count": len(left), "payload_sha256": digest.hexdigest()}


def _read_es(client: Any, index: str) -> list[dict]:
    count = client.count(index=index)["count"]
    if type(count) is not int or not 0 < count <= MAX_DOCUMENTS:
        raise ValueError("es_count_out_of_bounds")
    rows = []
    after = None
    while len(rows) < count:
        body = {"size": min(256, count - len(rows)), "query": {"match_all": {}}, "sort": [{"chunk_id": "asc"}]}
        if after is not None:
            body["search_after"] = after
        hits = client.search(index=index, body=body)["hits"]["hits"]
        if not hits or len(hits) > count - len(rows):
            raise ValueError("incomplete_es_scan")
        rows.extend(hit["_source"] for hit in hits)
        cursor = hits[-1].get("sort")
        if not cursor or cursor == after:
            raise ValueError("invalid_es_cursor")
        after = cursor
    if client.count(index=index)["count"] != count:
        raise ValueError("es_count_changed")
    return rows


def _read_vectors(
    client: Any, collection: str, *, dimension: int | None = None, hashes: dict | None = None
) -> list[dict]:
    if hashes is not None and (type(dimension) is not int or dimension <= 0 or hashes):
        raise ValueError("invalid_vector_audit_configuration")
    count = client.count(collection_name=collection, exact=True).count
    if type(count) is not int or not 0 < count <= MAX_DOCUMENTS:
        raise ValueError("vector_count_out_of_bounds")
    rows = []
    offset = None
    while True:
        records, cursor = client.scroll(
            collection_name=collection, limit=256, offset=offset, with_payload=True, with_vectors=hashes is not None
        )
        for record in records:
            payload = record.payload or {}
            if hashes is not None:
                identity = (payload.get("doc_id"), payload.get("chunk_id"))
                if any(not isinstance(value, str) or not value for value in identity) or identity in hashes:
                    raise ValueError("invalid_or_duplicate_vector_identity")
                vector = record.vector
                if (
                    not isinstance(vector, list)
                    or len(vector) != dimension
                    or any(type(value) not in (int, float) or not math.isfinite(value) for value in vector)
                ):
                    raise ValueError("invalid_persisted_vector")
                norm = math.hypot(*vector)
                if not math.isfinite(norm) or norm == 0:
                    raise ValueError("invalid_persisted_vector_norm")
                hashes[identity] = hashlib.sha256(
                    json.dumps([str(record.id), vector], separators=(",", ":"), allow_nan=False).encode("utf-8")
                ).hexdigest()
            rows.append(payload)
        if len(rows) > count or (cursor is not None and (not records or cursor == offset)):
            raise ValueError("invalid_vector_scan")
        if cursor is None:
            break
        offset = cursor
    if len(rows) != count or client.count(collection_name=collection, exact=True).count != count:
        raise ValueError("vector_count_changed")
    return rows


def read_vector_snapshot(client: Any, collection: str, dimension: int) -> tuple[list[dict], str]:
    """Fingerprint actual unnamed dense vectors, bound to chunk and point IDs."""
    hashes: dict = {}
    rows = _read_vectors(client, collection, dimension=dimension, hashes=hashes)
    digest = hashlib.sha256()
    for identity in sorted(hashes):
        digest.update(json.dumps([*identity, hashes[identity]], ensure_ascii=False).encode("utf-8"))
        digest.update(b"\n")
    return rows, digest.hexdigest()


def gather_dense_parity(approval: dict) -> dict:
    """Inspect the same configured clients as production retrieval; never publish on error."""
    if approval.get("verified") is not True:
        return {"verified": False, "reason": "es_approval_missing"}
    try:
        from common.config import get_config_dict
        from models.embedding_service import EmbeddingService
        from retrieval.bm25_retriever import BM25Retriever

        config = get_config_dict()
        index = config["elasticsearch"]["index"]
        collection = config["embedding"]["text"]["collection"]
        if index != approval.get("index_name"):
            raise ValueError("configured_es_index_mismatch")
        es = BM25Retriever()
        if not es.enabled or es.es_client is None:
            raise ValueError("es_unavailable")
        vectors = EmbeddingService().qdrant_client
        try:
            dimension = config["embedding"]["text"]["dimension"]
            rows, vector_sha = read_vector_snapshot(vectors, collection, dimension)
            result = compare_payloads(_read_es(es.es_client, index), rows, approval)
            return {**result, "index": index, "collection": collection, "vector_sha256": vector_sha}
        finally:
            vectors.close()
    except Exception as exc:
        return {"verified": False, "reason": "dense_parity_failed", "error_type": type(exc).__name__}


def dense_parity_blockers(before: dict, after: dict) -> list[str]:
    if before.get("verified") is not True or after.get("verified") is not True:
        return ["dense_corpus_parity_not_verified"]
    if not before.get("vector_sha256") or not after.get("vector_sha256"):
        return ["dense_vector_fingerprint_not_verified"]
    if before != after or not before.get("payload_sha256"):
        return ["dense_corpus_changed_during_run"]
    return []
