"""Regression tests for issues found during repository reconciliation (Codex review)."""

from __future__ import annotations

import json

import pytest
from qdrant_client import QdrantClient

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


def _builder(tmp_path):
    client = QdrantClient(":memory:")
    es_writer = ElasticsearchWriter(FakeElasticsearchClient(), "cosmetics_docs")
    es_writer.ensure_index()
    return (
        SnapshotBuilder(
            processor=DocumentProcessor(chunk_size=40, chunk_overlap=0, source_root=tmp_path),
            text_embedder=DeterministicTestEmbedder(dimension=DIMENSION),
            image_processor=ImageProcessor(
                DeterministicTestOCRProvider(text="label text"),
                DeterministicTestImageEmbedder(dimension=DIMENSION),
                visual_weight_repeat=2,
            ),
            text_writer=QdrantTextWriter(client, "text_col", dimension=DIMENSION),
            image_writer=QdrantImageWriter(client, "image_col", dimension=DIMENSION),
            state_store=StateStore(tmp_path / "state.sqlite3"),
            es_writer=es_writer,
        ),
        client,
    )


def _source(path, source_id, role=0, dept=0, document_type="text"):
    return IngestionSource(
        source_id=source_id,
        path=str(path),
        document_type=document_type,
        role_mask=role,
        dept_mask=dept,
        relative_path=source_id,
    )


def _active_image_points(client, collection, epoch, doc_id):
    if not client.collection_exists(collection):
        return []
    records, _ = client.scroll(collection, limit=1000, with_payload=True)
    return [
        record
        for record in records
        if (record.payload or {}).get("doc_type") == "image"
        and record.payload.get("doc_id") == doc_id
        and record.payload.get("doc_version_epoch") == epoch
        and record.payload.get("status") == "active"
    ]


def _write_scanned_pdf(path):
    import pymupdf
    from PIL import Image

    image = Image.new("RGB", (120, 60), "white")
    image_path = path.parent / "scan.png"
    image.save(image_path)
    document = pymupdf.open()
    page = document.new_page(width=140, height=80)
    page.insert_image(pymupdf.Rect(5, 5, 135, 75), filename=str(image_path))
    document.save(path)
    document.close()


def _write_text_pdf(path):
    import pymupdf

    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Text-only regulatory document body.")
    document.save(path)
    document.close()


def test_failed_build_does_not_commit_source_state(tmp_path):
    builder, _ = _builder(tmp_path)
    good = tmp_path / "good.txt"
    good.write_text("good content", encoding="utf-8")
    sources = [_source(good, "good.txt"), _source(tmp_path / "missing.txt", "missing.txt")]

    with pytest.raises(ValueError):
        builder.build_full(sources, "epoch_1")

    assert builder.state_store.get("good.txt") is None


def test_reingest_without_images_clears_stale_image_points(tmp_path):
    builder, client = _builder(tmp_path)
    path = tmp_path / "doc.pdf"
    _write_scanned_pdf(path)
    source = _source(path, "doc.pdf")
    doc_id = builder._doc_id(source)

    builder.ingest_source(source, "epoch_1")
    assert _active_image_points(client, "image_col", "epoch_1", doc_id)

    _write_text_pdf(path)
    builder.ingest_source(source, "epoch_1")
    assert _active_image_points(client, "image_col", "epoch_1", doc_id) == []


def test_validator_rejects_unexpected_documents(tmp_path):
    from qdrant_client.http.models import PointStruct

    builder, client = _builder(tmp_path)
    path = tmp_path / "a.txt"
    path.write_text("content", encoding="utf-8")
    source = _source(path, "a.txt")
    builder.build_full([source], "epoch_1")

    client.upsert(
        "text_col",
        points=[
            PointStruct(
                id="00000000-0000-0000-0000-0000000000dd",
                vector=[1.0] + [0.0] * (DIMENSION - 1),
                payload={
                    "doc_type": "text",
                    "doc_id": "ghost",
                    "chunk_id": "ghost",
                    "content": "ghost",
                    "status": "active",
                    "doc_version_epoch": "epoch_1",
                    "embedding_version": builder.text_writer.embedding_version,
                    "role_mask": 0,
                    "dept_mask": 0,
                },
            )
        ],
        wait=True,
    )
    report = builder.validator(expected_doc_ids={builder._doc_id(source)}).validate("epoch_1")
    assert not report.ok
    assert any("outside the expected set" in error for error in report.errors)


def test_permission_change_forces_reprocessing(tmp_path):
    builder, client = _builder(tmp_path)
    path = tmp_path / "policy.txt"
    path.write_text("policy content", encoding="utf-8")
    builder.build_full([_source(path, "policy.txt", role=0, dept=0)], "epoch_1")

    result = builder.build_incremental([_source(path, "policy.txt", role=2, dept=4)], "epoch_1", "epoch_2")
    assert result.changes.modified == ["policy.txt"]

    records, _ = client.scroll("text_col", limit=1000, with_payload=True)
    epoch_2 = [
        point
        for point in records
        if (point.payload or {}).get("doc_type") == "text" and point.payload.get("doc_version_epoch") == "epoch_2"
    ]
    assert epoch_2
    assert all(point.payload["role_mask"] == 2 and point.payload["dept_mask"] == 4 for point in epoch_2)


def test_bm25_returns_source_doc_id_and_chunk_id():
    from retrieval.bm25_retriever import BM25Retriever

    retriever = BM25Retriever.__new__(BM25Retriever)
    retriever.enabled = True
    retriever._es_version = (8, 0)

    class FakeES:
        def search(self, index, body):
            return {
                "hits": {
                    "hits": [
                        {
                            "_id": "hashed-id",
                            "_score": 1.0,
                            "_source": {
                                "doc_id": "real-doc",
                                "content": "content",
                                "chunk_id": "chunk-1",
                                "role_mask": 0,
                                "dept_mask": 0,
                            },
                        }
                    ]
                }
            }

    retriever._es_client = FakeES()
    hits = retriever.search("query", 0, 0)
    assert hits[0]["doc_id"] == "real-doc"
    assert hits[0]["metadata"]["chunk_id"] == "chunk-1"


def test_elasticsearch_documents_for_epoch_paginates():
    client = FakeElasticsearchClient()
    writer = ElasticsearchWriter(client, "cosmetics_docs", page_size=2)
    writer.ensure_index()
    for index in range(5):
        writer.upsert_documents(
            [
                {
                    "doc_id": "doc",
                    "chunk_id": f"chunk-{index}",
                    "chunk_index": index,
                    "content": "content",
                    "source_path": "doc.txt",
                    "doc_type": "text",
                    "embedding_type": "bge",
                    "embedding_version": "v1",
                    "role_mask": 0,
                    "dept_mask": 0,
                    "status": "active",
                    "doc_version_epoch": "epoch_1",
                    "metadata": {},
                }
            ]
        )
    documents = writer.documents_for_epoch("epoch_1")
    assert len(documents) == 5


def test_airflow_default_args_use_baseoperator_timeout_keys():
    import dags.knowledge_base_dags as dags

    assert "retry_delay" in dags.DEFAULT_ARGS
    assert "execution_timeout" in dags.DEFAULT_ARGS
    assert "retry_delay_minutes" not in dags.DEFAULT_ARGS
    assert "execution_timeout_minutes" not in dags.DEFAULT_ARGS


def test_airflow_tasks_return_serializable_dicts(monkeypatch):
    import dags.knowledge_base_dags as dags
    import offline.scheduler as scheduler_module

    class FakeScheduler:
        def run_incremental_update(self):
            return {"epoch": "e1"}

        def run_full_rebuild(self):
            return {"epoch": "e2"}

    monkeypatch.setattr(scheduler_module, "OfflineScheduler", FakeScheduler)
    incremental = dags._task_incremental_update()
    full = dags._task_full_rebuild()
    assert incremental == {"epoch": "e1"}
    assert full == {"epoch": "e2"}
    json.dumps(incremental)
    json.dumps(full)
