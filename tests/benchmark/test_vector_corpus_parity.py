"""Synthetic payload controls, not evidence of real vector retrieval."""
from copy import deepcopy

import pytest

from benchmarks.source_approval import _index_content_fingerprint
from benchmarks.vector_corpus_parity import compare_payloads, dense_parity_blockers


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
