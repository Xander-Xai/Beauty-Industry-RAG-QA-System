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
    assert dense_parity_blockers(result, result) == []


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
    good = {"verified": True, "payload_sha256": "a"}
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


@pytest.mark.parametrize(
    "vector",
    [None, [], [0.0, 0.0], [1.0], [True, 1.0], [float("nan"), 1.0], {"named": [1.0, 0.0]}],
)
def test_persisted_vector_audit_rejects_invalid_values(vector):
    with pytest.raises(ValueError, match="invalid_persisted_vector"):
        read_vector_snapshot(_vector_client([vector]), "experiment", 2)
