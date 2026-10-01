"""Full and incremental snapshot build, carry-forward, and seal tests."""

from __future__ import annotations

import pytest
from qdrant_client import QdrantClient

from offline.carry_forward import IncompatibleEmbeddingVersion
from offline.document_processor import DocumentProcessor
from offline.elasticsearch_writer import ElasticsearchWriter
from offline.embeddings import DeterministicTestEmbedder, DeterministicTestImageEmbedder
from offline.image_processor import DeterministicTestOCRProvider, ImageProcessor
from offline.qdrant_writer import QdrantImageWriter
from offline.snapshot_builder import IngestionSource, SnapshotBuilder
from offline.state_store import StateStore
from offline.text_ingestion import QdrantTextWriter
from tests.offline.fakes import FakeElasticsearchClient

DIMENSION = 8


def _builder(tmp_path, *, text_embedder=None):
    client = QdrantClient(":memory:")
    text_embedder = text_embedder or DeterministicTestEmbedder(dimension=DIMENSION)
    image_processor = ImageProcessor(
        DeterministicTestOCRProvider(text="ocr product label"),
        DeterministicTestImageEmbedder(dimension=DIMENSION),
        visual_weight_repeat=2,
    )
    es_client = FakeElasticsearchClient()
    es_writer = ElasticsearchWriter(es_client, "cosmetics_docs")
    es_writer.ensure_index()
    builder = SnapshotBuilder(
        processor=DocumentProcessor(chunk_size=20, chunk_overlap=0, source_root=tmp_path),
        text_embedder=text_embedder,
        image_processor=image_processor,
        text_writer=QdrantTextWriter(client, "text_col", dimension=DIMENSION),
        image_writer=QdrantImageWriter(client, "image_col", dimension=DIMENSION),
        state_store=StateStore(tmp_path / "state.sqlite3"),
        es_writer=es_writer,
    )
    return builder, client, es_writer


def _source(tmp_path, name, text, document_type="text"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return IngestionSource(
        source_id=name,
        path=str(path),
        document_type=document_type,
        role_mask=0,
        dept_mask=0,
        relative_path=name,
    )


def _epoch_points(client, collection, epoch, doc_type="text"):
    records, _ = client.scroll(collection, limit=1000, with_payload=True)
    return [
        record
        for record in records
        if (record.payload or {}).get("doc_version_epoch") == epoch
        and (record.payload or {}).get("doc_type") == doc_type
    ]


def test_full_build_writes_all_stores_and_validates(tmp_path):
    builder, client, es_writer = _builder(tmp_path)
    sources = [_source(tmp_path, "a.txt", "alpha content"), _source(tmp_path, "b.txt", "beta content")]
    result = builder.build_full(sources, "epoch_1")
    assert result.documents_processed == 2
    assert result.validation.ok
    assert _epoch_points(client, "text_col", "epoch_1")
    assert es_writer.count_documents("epoch_1") >= 2
    assert len(builder.state_store.all_states()) == 2


def test_incremental_carries_forward_and_rebuilds_changes(tmp_path):
    builder, client, es_writer = _builder(tmp_path)
    unchanged = _source(tmp_path, "keep.txt", "keep this stable document")
    modified = _source(tmp_path, "modify.txt", "original modified content")
    deleted = _source(tmp_path, "delete.txt", "content to be removed")
    builder.build_full([unchanged, modified, deleted], "epoch_1")

    keep_doc_id = builder._doc_id(unchanged)
    epoch_1_keep = {
        point.payload["chunk_id"]
        for point in _epoch_points(client, "text_col", "epoch_1")
        if point.payload["doc_id"] == keep_doc_id
    }

    # Modify one file, delete another, add a new one.
    modified_path = tmp_path / "modify.txt"
    modified_path.write_text("completely rewritten content", encoding="utf-8")
    (tmp_path / "delete.txt").unlink()
    added = _source(tmp_path, "new.txt", "brand new document")

    result = builder.build_incremental([unchanged, modified, added], "epoch_1", "epoch_2")
    assert result.changes.unchanged == ["keep.txt"]
    assert result.changes.modified == ["modify.txt"]
    assert result.changes.new == ["new.txt"]
    assert result.changes.deleted == ["delete.txt"]
    assert result.validation.ok

    epoch_2 = _epoch_points(client, "text_col", "epoch_2")
    epoch_2_by_doc = {}
    for point in epoch_2:
        epoch_2_by_doc.setdefault(point.payload["doc_id"], []).append(point)
    # Unchanged doc carried forward with same logical chunk ids.
    assert keep_doc_id in epoch_2_by_doc
    assert {point.payload["chunk_id"] for point in epoch_2_by_doc[keep_doc_id]} == epoch_1_keep
    # Modified doc rebuilt with new content.
    modified_doc_id = builder._doc_id(modified)
    assert any("rewritten" in point.payload["content"] for point in epoch_2_by_doc[modified_doc_id])
    # Deleted doc absent from the target epoch.
    assert builder._doc_id(deleted) not in epoch_2_by_doc
    # New doc present.
    assert builder._doc_id(added) in epoch_2_by_doc
    assert es_writer.count_documents("epoch_2") >= 3


def test_carry_forward_rejects_incompatible_embedding_version(tmp_path):
    builder, client, _ = _builder(tmp_path)
    source = _source(tmp_path, "keep.txt", "stable content")
    builder.build_full([source], "epoch_1")

    class NewVersionEmbedder(DeterministicTestEmbedder):
        def __init__(self):
            super().__init__(dimension=DIMENSION)
            self.embedding_version = "new-model-revision-v2"

    upgraded, _, _ = _builder(tmp_path, text_embedder=NewVersionEmbedder())
    upgraded.state_store = builder.state_store
    upgraded.text_writer.client = client
    with pytest.raises(IncompatibleEmbeddingVersion):
        upgraded.build_incremental([source], "epoch_1", "epoch_2")


def test_sealed_epoch_is_immutable(tmp_path):
    builder, client, _ = _builder(tmp_path)
    source = _source(tmp_path, "a.txt", "sealed content")
    builder.build_full([source], "epoch_1", seal=True)
    assert builder.text_writer._is_epoch_sealed("epoch_1")
    with pytest.raises(ValueError, match="is sealed"):
        builder.ingest_source(_source(tmp_path, "late.txt", "late addition"), "epoch_1")


def test_validator_fails_closed_on_missing_permission(tmp_path):
    builder, client, _ = _builder(tmp_path)
    from qdrant_client.http.models import Distance, PointStruct, VectorParams

    client.create_collection("text_col", vectors_config=VectorParams(size=DIMENSION, distance=Distance.COSINE))
    client.upsert(
        collection_name="text_col",
        points=[
            PointStruct(
                id="00000000-0000-0000-0000-0000000000bb",
                vector=[1.0] + [0.0] * (DIMENSION - 1),
                payload={
                    "doc_type": "text",
                    "doc_id": "bad",
                    "chunk_id": "bad",
                    "doc_version_epoch": "epoch_1",
                    "status": "active",
                    "embedding_version": builder.text_writer.embedding_version,
                },
            )
        ],
        wait=True,
    )
    report = builder.validator().validate("epoch_1")
    assert not report.ok
    assert any("role_mask" in error for error in report.errors)
