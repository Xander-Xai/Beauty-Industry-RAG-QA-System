"""Elasticsearch writer mapping, replacement, and RBAC tests (in-memory fake)."""

from __future__ import annotations

import pytest

from offline.elasticsearch_writer import (
    REQUIRED_FIELD_TYPES,
    ElasticsearchWriter,
    document_id,
)
from offline.source_trust import managed_record
from tests.offline.fakes import FakeElasticsearchClient


def _chunks(tmp_path, name="doc.txt", text="alpha beta gamma delta epsilon zeta"):
    from offline.document_processor import DocumentProcessor

    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    # These sources are managed internal content from the operator's own data
    # root, so they declare managed provenance explicitly. The BM25 writer runs
    # the same ingestion trust gate as the vector writers, so a chunk without
    # provenance is refused rather than indexed.
    return DocumentProcessor(chunk_size=20, chunk_overlap=0).process_chunks(
        path,
        role_mask=2,
        dept_mask=4,
        doc_version_epoch="epoch_1",
        provenance=managed_record(name).to_payload(),
    )


def _writer():
    client = FakeElasticsearchClient()
    writer = ElasticsearchWriter(client, "cosmetics_docs")
    writer.ensure_index()
    return client, writer


def test_ensure_index_creates_explicit_mapping_without_recreating():
    client, writer = _writer()
    assert client.indices.exists("cosmetics_docs")
    mapping = client.store["cosmetics_docs"]["mappings"]["properties"]
    assert mapping["role_mask"]["type"] == "long"
    assert mapping["dept_mask"]["type"] == "long"
    assert mapping["doc_version_epoch"]["type"] == "keyword"
    assert mapping["content"]["type"] == "text"
    writer.ensure_index()
    assert client.store["cosmetics_docs"]["mappings"]["properties"] == mapping


def test_validate_mapping_detects_wrong_type():
    client, writer = _writer()
    client.set_mapping_type("cosmetics_docs", "role_mask", "text")
    with pytest.raises(ValueError, match="role_mask"):
        writer.validate_mapping()


def test_recreate_requires_explicit_confirmation():
    _, writer = _writer()
    with pytest.raises(ValueError, match="confirmation"):
        writer.ensure_index(recreate=True)
    writer.ensure_index(recreate=True, confirm=True)


def test_upsert_and_replace_document_scopes(tmp_path):
    client, writer = _writer()
    first = _chunks(tmp_path, "a.txt")
    doc_a = first[0].doc_id
    documents = [writer.build_document(chunk) for chunk in first]
    writer.replace_document(doc_a, "epoch_1", documents)
    assert writer.count_documents("epoch_1") == len(first)

    other = _chunks(tmp_path, "b.txt")
    doc_b = other[0].doc_id
    writer.replace_document(doc_b, "epoch_1", [writer.build_document(chunk) for chunk in other])
    total = writer.count_documents("epoch_1")

    writer.replace_document(doc_a, "epoch_1", documents[:1])
    assert writer.count_documents("epoch_1") == total - len(first) + 1
    assert document_id(other[0].chunk_id, "epoch_1") in client.store["cosmetics_docs"]["docs"]


def test_document_ids_are_epoch_versioned():
    assert document_id("chunk-1", "epoch_a") != document_id("chunk-1", "epoch_b")


def test_replace_rejects_mismatched_document_or_epoch(tmp_path):
    _, writer = _writer()
    documents = [writer.build_document(chunk) for chunk in _chunks(tmp_path)]
    with pytest.raises(ValueError, match="match the requested document and epoch"):
        writer.replace_document("other-doc", "epoch_1", documents)


def test_build_document_fails_closed_on_invalid_permissions(tmp_path):
    _, writer = _writer()
    chunk = _chunks(tmp_path)[0]
    malformed = type(chunk)(**{**chunk.__dict__, "role_mask": None})
    with pytest.raises(ValueError, match="role_mask"):
        writer.build_document(malformed)


def test_all_required_fields_present_in_mapping():
    _, writer = _writer()
    properties = writer.client.store["cosmetics_docs"]["mappings"]["properties"]
    for field, expected in REQUIRED_FIELD_TYPES.items():
        assert properties[field]["type"] == expected


def test_provenance_subfields_are_keyword_typed():
    """The trust decision must be filterable, never analyzed into text."""
    _, writer = _writer()
    provenance = writer.client.store["cosmetics_docs"]["mappings"]["properties"]["provenance"]
    assert provenance["properties"]["source_trust"]["type"] == "keyword"
    assert provenance["properties"]["approval_status"]["type"] == "keyword"
    assert provenance["properties"]["trust_class"]["type"] == "keyword"


def test_build_document_persists_provenance(tmp_path):
    _, writer = _writer()
    chunk = _chunks(tmp_path, "a.txt")[0]
    document = writer.build_document(chunk)
    assert document["provenance"] == managed_record("a.txt").to_payload()
