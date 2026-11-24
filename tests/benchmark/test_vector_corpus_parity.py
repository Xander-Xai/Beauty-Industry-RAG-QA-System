"""Synthetic payload controls, not evidence of real vector retrieval."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from benchmarks.source_approval import _index_content_fingerprint
from benchmarks.vector_corpus_parity import compare_payloads, dense_parity_blockers, read_vector_snapshot


def _rows():
    return [
        {
            "doc_id": "doc",
            "chunk_id": "chunk",
            "doc_version_epoch": "epoch",
            "content": "approved text",
            "role_mask": 0,
            "dept_mask": 0,
            "status": "active",
            "provenance": {"source_id": "source", "approved_content_hash": "fixture"},
        }
    ]


def _approval(rows):
    return {"verified": True, "document_count": len(rows), "content_sha256": _index_content_fingerprint(rows)}


def test_exact_parity_and_stable_receipt():
    rows = _rows()
    result = compare_payloads(rows, deepcopy(rows), _approval(rows))
    assert result["verified"]
    assert dense_parity_blockers(result, result) == ["dense_vector_fingerprint_not_verified"]


@pytest.mark.parametrize("field", ["content", "role_mask", "status", "provenance", "doc_version_epoch"])
def test_same_identity_does_not_authorize_changed_payload(field):
    rows = _rows()
    vectors = deepcopy(rows)
    vectors[0][field] = "different"
    with pytest.raises(ValueError, match="payload_mismatch|invalid_payload"):
        compare_payloads(rows, vectors, _approval(rows))


def test_missing_extra_duplicate_and_unapproved_are_rejected():
    rows = _rows()
    for vectors in ([], rows + rows, rows + [{**rows[0], "chunk_id": "extra"}]):
        with pytest.raises(ValueError):
            compare_payloads(rows, vectors, _approval(rows))
    with pytest.raises(ValueError, match="approval_missing"):
        compare_payloads(rows, rows, {"verified": False})


def test_publication_blockers_require_both_matching_receipts():
    good = {"verified": True, "payload_sha256": "a", "vector_sha256": "b"}
    assert dense_parity_blockers(good, {"verified": False}) == ["dense_corpus_parity_not_verified"]
    assert dense_parity_blockers(good, {**good, "payload_sha256": "b"}) == ["dense_corpus_changed_during_run"]


def _vector_client(vectors):
    client = MagicMock()
    records = [
        SimpleNamespace(id=i, payload={**_rows()[0], "chunk_id": str(i)}, vector=vector)
        for i, vector in enumerate(vectors)
    ]
    client.count.return_value = SimpleNamespace(count=len(records))
    client.scroll.return_value = (records, None)
    return client


def test_persisted_vector_fingerprint_detects_vector_only_change():
    first = _vector_client([[1.0, 0.0]])
    second = _vector_client([[0.0, 1.0]])
    rows, before = read_vector_snapshot(first, "experiment", 2)
    same_rows, after = read_vector_snapshot(second, "experiment", 2)
    assert rows == same_rows
    assert before != after
    assert read_vector_snapshot(first, "experiment", 2)[1] == before
    assert first.scroll.call_args.kwargs["with_vectors"] is True
    receipt = {"verified": True, "payload_sha256": "same", "vector_sha256": before}
    assert dense_parity_blockers(receipt, {**receipt, "vector_sha256": after}) == ["dense_corpus_changed_during_run"]


def test_vector_fingerprint_tolerates_float32_roundtrip_noise():
    first = _vector_client([[0.6, 0.8]])
    reopened = _vector_client([[0.6000000238418579, 0.800000011920929]])
    drifted = _vector_client([[0.8, 0.6]])
    assert read_vector_snapshot(first, "experiment", 2) == read_vector_snapshot(reopened, "experiment", 2)
    assert read_vector_snapshot(first, "experiment", 2)[1] != read_vector_snapshot(drifted, "experiment", 2)[1]


@pytest.mark.parametrize(
    "vector",
    [None, [], [0.0, 0.0], [1.0], [True, 1.0], [float("nan"), 1.0], [3.0, 4.0], {"named": [1.0, 0.0]}],
)
def test_persisted_vector_audit_rejects_invalid_values(vector):
    with pytest.raises(ValueError, match="invalid_persisted_vector"):
        read_vector_snapshot(_vector_client([vector]), "experiment", 2)


def test_disk_backed_qdrant_roundtrip_detects_vector_only_update(tmp_path):
    """Real local Qdrant engine, no BGE or remote database required."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, PointVectors, VectorParams

    location = str(tmp_path / "qdrant")
    collection = "rag_text_exp_roundtrip"
    payload = _rows()[0]
    client = QdrantClient(path=location)
    try:
        client.create_collection(collection, vectors_config=VectorParams(size=2, distance=Distance.COSINE))
        client.upsert(collection, [PointStruct(id=1, vector=[0.6, 0.8], payload=payload)], wait=True)
        rows, original = read_vector_snapshot(client, collection, 2)
        assert rows == [payload]
    finally:
        client.close()

    client = QdrantClient(path=location)
    try:
        assert read_vector_snapshot(client, collection, 2) == (rows, original)
        client.update_vectors(collection, [PointVectors(id=1, vector=[0.8, 0.6])], wait=True)
        changed_rows, changed = read_vector_snapshot(client, collection, 2)
        assert changed_rows == rows
        assert changed != original
        parity = compare_payloads(rows, changed_rows, _approval(rows))
        assert dense_parity_blockers({**parity, "vector_sha256": original}, {**parity, "vector_sha256": changed}) == [
            "dense_corpus_changed_during_run"
        ]
    finally:
        client.close()


def test_nonunit_persisted_values_are_not_silently_normalized():
    unit = _vector_client([[0.6, 0.8]])
    assert read_vector_snapshot(unit, "experiment", 2)[1]
    with pytest.raises(ValueError, match="invalid_persisted_vector_norm"):
        read_vector_snapshot(_vector_client([[3.0, 4.0]]), "experiment", 2)
