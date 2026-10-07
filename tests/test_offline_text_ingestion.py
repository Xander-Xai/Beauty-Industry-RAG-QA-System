"""Phase 1 text ingestion contract and local Qdrant integration tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from qdrant_client import QdrantClient

from offline.source_trust import managed_record
from offline.text_ingestion import (
    DeterministicTestEmbedder,
    DocumentProcessor,
    QdrantTextWriter,
    TextChunk,
    TextIngestionService,
)

#: These fixtures are managed internal corpus content ingested from the operator's
#: own data root, so they declare managed provenance explicitly: the text writer
#: runs the ingestion trust gate and refuses a chunk with no provenance.
_MANAGED = managed_record("doc.txt").to_payload()


def _text_records(client, collection):
    records, _ = client.scroll(collection, limit=1000, with_payload=True)
    return [record for record in records if (record.payload or {}).get("doc_type") == "text"]


def test_processor_handles_short_bom_overlap_stability_and_changed_content(tmp_path):
    path = tmp_path / "guide.txt"
    path.write_text("\ufeffAlpha beta gamma delta", encoding="utf-8")
    processor = DocumentProcessor(chunk_size=10, chunk_overlap=3)
    first = processor.process(path, role_mask=0, dept_mask=0, doc_version_epoch="phase_1", provenance=_MANAGED)
    repeated = processor.process(path, role_mask=0, dept_mask=0, doc_version_epoch="phase_1", provenance=_MANAGED)
    assert [item.chunk_id for item in first] == [item.chunk_id for item in repeated]
    assert [item.text for item in first][1].startswith(first[0].text[-3:])
    assert all(item.role_mask == item.dept_mask == 0 for item in first)
    path.write_text("Alpha beta gamma changed", encoding="utf-8")
    changed = processor.process(path, role_mask=0, dept_mask=0, doc_version_epoch="phase_1", provenance=_MANAGED)
    assert changed[0].content_hash != first[0].content_hash
    assert changed[0].chunk_id != first[0].chunk_id


def test_processor_empty_utf8_and_unsupported_or_malformed_input(tmp_path):
    processor = DocumentProcessor()
    empty = tmp_path / "empty.txt"
    empty.write_text(" \n", encoding="utf-8")
    assert processor.process(empty, role_mask=0, dept_mask=0, doc_version_epoch="default", provenance=_MANAGED) == []
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="cannot read UTF-8"):
        processor.process(bad, role_mask=0, dept_mask=0, doc_version_epoch="default", provenance=_MANAGED)
    pdf = tmp_path / "unsupported.pdf"
    pdf.write_text("not a pdf", encoding="utf-8")
    with pytest.raises(ValueError, match="only .txt"):
        processor.process(pdf, role_mask=0, dept_mask=0, doc_version_epoch="default", provenance=_MANAGED)


def test_document_identity_is_stable_across_data_root_mounts_and_file_moves(tmp_path):
    first_root = tmp_path / "mount-a" / "data"
    second_root = tmp_path / "mount-b" / "data"
    first_root.mkdir(parents=True)
    second_root.mkdir(parents=True)
    first = first_root / "guides" / "policy.txt"
    second = second_root / "guides" / "policy.txt"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("same source", encoding="utf-8")
    second.write_text("same source", encoding="utf-8")

    processor_a = DocumentProcessor(source_root=first_root)
    processor_b = DocumentProcessor(source_root=second_root)
    _, first_id = processor_a.document_identity(first)
    _, second_id = processor_b.document_identity(second)
    _, moved_id = processor_a.document_identity(first, source_id="policy:ingredient-safety")
    first.rename(first_root / "renamed.txt")
    _, renamed_id = processor_a.document_identity(first_root / "renamed.txt", source_id="policy:ingredient-safety")

    assert first_id == second_id
    assert moved_id == renamed_id
    with pytest.raises(ValueError, match="outside the configured data root"):
        processor_a.document_identity(second)


def test_document_size_limits_reject_before_embedding_or_qdrant_write(tmp_path):
    client = QdrantClient(":memory:")

    class SpyEmbedder:
        dimension = 16

        def __init__(self):
            self.called = False

        def embed_texts(self, texts):
            self.called = True
            return [[0.0] * self.dimension for _ in texts]

    embedder = SpyEmbedder()
    writer = QdrantTextWriter(client, "size_limit", dimension=16)
    service = TextIngestionService(DocumentProcessor(max_document_bytes=8), embedder, writer)
    source = _write_source(tmp_path, "oversized.txt", "123456789")
    with pytest.raises(ValueError, match="8-byte ingestion limit"):
        service.ingest(source, role_mask=0, dept_mask=0, doc_version_epoch="epoch_1", provenance=_MANAGED)
    assert not embedder.called
    assert not client.collection_exists("size_limit")

    chunk_limited = DocumentProcessor(chunk_size=2, chunk_overlap=0, max_chunks=2)
    with pytest.raises(ValueError, match="2-chunk ingestion limit"):
        chunk_limited.process(source, role_mask=0, dept_mask=0, doc_version_epoch="epoch_1", provenance=_MANAGED)


@pytest.mark.parametrize("role,dept", [(-1, 0), (0, 2**32), (True, 0), (0, "1")])
def test_invalid_permission_masks_fail_before_ingestion(tmp_path, role, dept):
    path = tmp_path / "restricted.txt"
    path.write_text("restricted content", encoding="utf-8")
    with pytest.raises(ValueError, match="uint32"):
        DocumentProcessor().process(
            path, role_mask=role, dept_mask=dept, doc_version_epoch="default", provenance=_MANAGED
        )


def test_deterministic_embedder_contract_and_empty_input():
    embedder = DeterministicTestEmbedder(dimension=32)
    assert embedder.embed_texts([]) == []
    assert embedder.embed_texts(["same words"]) == embedder.embed_texts(["same words"])
    assert embedder.embed_texts(["same words"])[0] == embedder.embed_texts(["words same"])[0]
    assert len(embedder.embed_texts(["content"])[0]) == 32


def test_bge_adapter_is_lazy_and_checks_model_output():
    from offline.text_ingestion import BGETextEmbedder

    adapter = BGETextEmbedder("configured-bge", dimension=2)
    assert adapter.embed_texts([]) == []

    class SharedOnlineEmbeddingService:
        def encode_texts_batch(self, texts):
            return [[0.1, 0.2] for _ in texts]

    adapter._embedding_service = SharedOnlineEmbeddingService()
    assert adapter.embed_texts(["hello"]) == [[0.1, 0.2]]
    adapter._embedding_service.encode_texts_batch = lambda texts: [[0.1] for _ in texts]
    with pytest.raises(ValueError, match="2 finite values"):
        adapter.embed_texts(["hello"])


def test_bge_adapter_bounds_model_batch_size():
    from offline.text_ingestion import BGETextEmbedder

    class SharedOnlineEmbeddingService:
        def __init__(self):
            self.batch_lengths = []

        def encode_texts_batch(self, texts):
            self.batch_lengths.append(len(texts))
            return [[0.1, 0.2] for _ in texts]

    adapter = BGETextEmbedder("configured-bge", dimension=2, batch_size=2)
    adapter._embedding_service = SharedOnlineEmbeddingService()
    vectors = adapter.embed_texts(["one", "two", "three", "four", "five"])
    assert len(vectors) == 5
    assert adapter._embedding_service.batch_lengths == [2, 2, 1]


def test_online_mean_pooling_ignores_padding_tokens():
    from models.embedding_service import EmbeddingService

    class Tensor:
        def __init__(self, values):
            self.values = np.asarray(values)

        @property
        def dtype(self):
            return self.values.dtype

        def unsqueeze(self, axis):
            return Tensor(np.expand_dims(self.values, axis))

        def to(self, dtype=None):
            return Tensor(self.values.astype(dtype))

        def sum(self, dim):
            return Tensor(self.values.sum(axis=dim))

        def clamp(self, min):
            return Tensor(np.maximum(self.values, min))

        def __mul__(self, other):
            return Tensor(self.values * other.values)

        def __truediv__(self, other):
            return Tensor(self.values / other.values)

        def tolist(self):
            return self.values.tolist()

    hidden = Tensor([[[2.0], [4.0], [100.0]], [[6.0], [8.0], [10.0]]])
    attention_mask = Tensor([[1, 1, 0], [1, 1, 1]])
    pooled = EmbeddingService._mean_pool(hidden, attention_mask)
    assert pooled.tolist() == [[3.0], [8.0]]


def test_cli_rejects_unimplemented_historical_modes(monkeypatch):
    import sys

    from run_offline import main

    monkeypatch.setattr(sys, "argv", ["run_offline.py", "--mode", "full"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_cli_seals_requested_epoch(monkeypatch):
    import sys

    import offline.snapshot_builder
    from run_offline import main

    sealed = []

    class FakeBuilder:
        def seal_epoch(self, epoch, *, validate=True):
            sealed.append((epoch, validate))

    monkeypatch.setattr(offline.snapshot_builder, "configured_snapshot_builder", lambda: FakeBuilder())
    monkeypatch.setattr(sys, "argv", ["run_offline.py", "seal-epoch", "--epoch", "phase_1"])

    assert main() == 0
    assert sealed == [("phase_1", True)]


def test_configured_service_seals_current_epoch_on_startup(monkeypatch, tmp_path):
    import qdrant_client

    from common import config as common_config
    from offline.text_ingestion import configured_text_ingestion_service

    client = QdrantClient(":memory:")
    configured = {
        "embedding": {"text": {"collection": "startup_seal", "dimension": 16, "model_path": "unused"}},
        "qdrant": {"host": "localhost", "port": 6333, "grpc_port": 6334},
        "knowledge_base": {"data_dir": str(tmp_path / "data")},
        "knowledge_version_epoch": "active_v1",
    }
    monkeypatch.setattr(common_config, "get_config_dict", lambda: configured)
    monkeypatch.setattr(qdrant_client, "QdrantClient", lambda **kwargs: client)

    service = configured_text_ingestion_service()

    assert service.writer._is_epoch_sealed("active_v1")


def test_qdrant_writer_rejects_missing_permission_and_wrong_vectors(tmp_path):
    chunk = DocumentProcessor().process(
        _write_source(tmp_path, "restricted.txt", "restricted policy"),
        role_mask=2,
        dept_mask=4,
        doc_version_epoch="phase_1",
    )[0]
    client = QdrantClient(":memory:")
    writer = QdrantTextWriter(client, "text_test", dimension=16)
    malformed = TextChunk(**{**chunk.__dict__, "role_mask": None})
    with pytest.raises(ValueError, match="role_mask"):
        writer.upsert([malformed], [[0.0] * 16])
    with pytest.raises(ValueError, match="16 finite values"):
        writer.upsert([chunk], [[0.0] * 15])


def _write_source(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def test_txt_to_local_qdrant_query_authorization_and_idempotence(tmp_path):
    from auth.bitmask_rbac import build_qdrant_filter
    from common.models import RecallResult
    from models.embedding_service import EmbeddingService
    from retrieval.parallel_recall import ParallelRecallManager

    client = QdrantClient(":memory:")
    writer = QdrantTextWriter(client, "rag_text_16", dimension=16)
    service = TextIngestionService(
        DocumentProcessor(chunk_size=100, chunk_overlap=10),
        DeterministicTestEmbedder(dimension=16),
        writer,
    )
    source = _write_source(tmp_path, "restricted.txt", "secret formula collagen moisturizer")
    chunks = service.ingest(source, role_mask=2, dept_mask=4, doc_version_epoch="phase_1", provenance=_MANAGED)
    assert len(chunks) == 1
    query_vector = DeterministicTestEmbedder(16).embed_texts(["secret formula"])[0]
    reader = EmbeddingService.__new__(EmbeddingService)
    reader._qdrant_client = client
    hits = reader.search_qdrant_text(
        np.array(query_vector),
        collection_name="rag_text_16",
        top_k=10,
        qdrant_filter=build_qdrant_filter(2, 4, "phase_1"),
    )
    assert len(hits) == 1
    hit = hits[0]
    assert hit["content"] == chunks[0].text
    assert hit["metadata"]["role_mask"] == 2 and hit["metadata"]["dept_mask"] == 4
    assert hit["metadata"]["status"] == "active"
    assert hit["metadata"]["doc_version_epoch"] == "phase_1"
    assert hit["metadata"]["chunk_id"] == chunks[0].chunk_id

    candidate = RecallResult(
        doc_id=hit["doc_id"],
        content=hit["content"],
        score=hit["score"],
        source="dense_bge",
        metadata=hit["metadata"],
    )
    manager = ParallelRecallManager()
    assert manager._apply_rbac_filter([candidate], 2, 4) == [candidate]
    assert manager._apply_rbac_filter([candidate], 1, 2) == []
    assert manager._apply_rbac_filter([candidate.model_copy(update={"metadata": {"role_mask": 2}})], 2, 4) == []

    repeated = service.ingest(source, role_mask=2, dept_mask=4, doc_version_epoch="phase_1", provenance=_MANAGED)
    assert [item.chunk_id for item in chunks] == [item.chunk_id for item in repeated]
    assert len(_text_records(client, "rag_text_16")) == 1

    with pytest.raises(ValueError, match="uint32"):
        service.ingest(source, role_mask=2**32, dept_mask=4, doc_version_epoch="phase_1", provenance=_MANAGED)


def test_same_content_coexists_and_remains_queryable_across_epochs(tmp_path):
    from auth.bitmask_rbac import build_qdrant_filter
    from models.embedding_service import EmbeddingService

    client = QdrantClient(":memory:")
    embedder = DeterministicTestEmbedder(16)
    writer = QdrantTextWriter(client, "epoch_test", dimension=16)
    service = TextIngestionService(DocumentProcessor(), embedder, writer)
    source = _write_source(tmp_path, "stable.txt", "shared collagen moisturizer content")

    epoch_a = service.ingest(source, role_mask=0, dept_mask=0, doc_version_epoch="epoch_a", provenance=_MANAGED)
    points_a = _text_records(client, "epoch_test")
    ids_a = {point.id for point in points_a}
    assert len(ids_a) == len(epoch_a)

    epoch_b = service.ingest(source, role_mask=0, dept_mask=0, doc_version_epoch="epoch_b", provenance=_MANAGED)
    points_b = _text_records(client, "epoch_test")
    ids_by_epoch = {
        epoch: {point.id for point in points_b if point.payload["doc_version_epoch"] == epoch}
        for epoch in ("epoch_a", "epoch_b")
    }
    assert ids_by_epoch["epoch_a"] == ids_a
    assert ids_by_epoch["epoch_b"]
    assert ids_by_epoch["epoch_a"].isdisjoint(ids_by_epoch["epoch_b"])
    assert len(points_b) == len(epoch_a) + len(epoch_b)

    reader = EmbeddingService.__new__(EmbeddingService)
    reader._qdrant_client = client
    query = np.array(embedder.embed_texts(["shared collagen moisturizer"])[0])
    for epoch in ("epoch_a", "epoch_b"):
        hits = reader.search_qdrant_text(
            query,
            collection_name="epoch_test",
            top_k=10,
            qdrant_filter=build_qdrant_filter(0, 0, epoch),
        )
        assert hits
        assert {hit["metadata"]["doc_version_epoch"] for hit in hits} == {epoch}

    repeated_b = service.ingest(source, role_mask=0, dept_mask=0, doc_version_epoch="epoch_b", provenance=_MANAGED)
    points_after_repeat = _text_records(client, "epoch_test")
    assert [chunk.chunk_id for chunk in repeated_b] == [chunk.chunk_id for chunk in epoch_b]
    assert len(points_after_repeat) == len(epoch_a) + len(epoch_b)
    assert {point.id for point in points_after_repeat if point.payload["doc_version_epoch"] == "epoch_a"} == ids_a


def test_staging_epoch_replacement_preserves_other_epochs_and_sealed_epoch_is_immutable(tmp_path):
    client = QdrantClient(":memory:")
    service = TextIngestionService(
        DocumentProcessor(chunk_size=12, chunk_overlap=0),
        DeterministicTestEmbedder(16),
        QdrantTextWriter(client, "replace_test", dimension=16),
    )
    source = _write_source(tmp_path, "mutable.txt", "old content that spans multiple chunks")
    old_chunks = service.ingest(source, role_mask=2, dept_mask=4, doc_version_epoch="phase_1", provenance=_MANAGED)
    assert len(old_chunks) > 1
    original_ids = {record.id for record in _text_records(client, "replace_test")}

    source.write_text("new content", encoding="utf-8")
    phase_1 = service.ingest(source, role_mask=2, dept_mask=4, doc_version_epoch="phase_1", provenance=_MANAGED)
    phase_1_records = _text_records(client, "replace_test")
    phase_1_ids = {record.id for record in phase_1_records}
    assert len(phase_1) == 1
    assert len(phase_1_ids) == 1
    assert original_ids.isdisjoint(phase_1_ids)
    assert {record.payload["content"] for record in phase_1_records} == {"new content"}

    phase_2 = service.ingest(source, role_mask=2, dept_mask=4, doc_version_epoch="phase_2", provenance=_MANAGED)
    assert len(phase_2) == 1
    records = _text_records(client, "replace_test")
    phase_2_ids = {record.id for record in records if record.payload["doc_version_epoch"] == "phase_2"}
    assert {record.id for record in records if record.payload["doc_version_epoch"] == "phase_1"} == phase_1_ids
    assert phase_1_ids.isdisjoint(phase_2_ids)

    source.write_text("replacement for B", encoding="utf-8")
    replacement_b = service.ingest(source, role_mask=2, dept_mask=4, doc_version_epoch="phase_2", provenance=_MANAGED)
    assert replacement_b
    records = _text_records(client, "replace_test")
    assert {record.id for record in records if record.payload["doc_version_epoch"] == "phase_1"} == phase_1_ids
    replacement_b_ids = {record.id for record in records if record.payload["doc_version_epoch"] == "phase_2"}
    assert {record.payload["content"] for record in records if record.payload["doc_version_epoch"] == "phase_2"} == {
        chunk.text for chunk in replacement_b
    }
    assert phase_2_ids.isdisjoint(replacement_b_ids)

    source.write_text(" \n", encoding="utf-8")
    assert service.ingest(source, role_mask=2, dept_mask=4, doc_version_epoch="phase_2", provenance=_MANAGED) == []
    records = _text_records(client, "replace_test")
    assert {record.payload["doc_version_epoch"] for record in records} == {"phase_1"}

    service.writer.seal_epoch("phase_2")
    another_source = _write_source(tmp_path, "late-addition.txt", "late addition")
    with pytest.raises(ValueError, match="is sealed"):
        service.ingest(another_source, role_mask=0, dept_mask=0, doc_version_epoch="phase_2", provenance=_MANAGED)


def test_legacy_default_document_cannot_be_restricted_in_place(tmp_path):
    from qdrant_client.http.models import Distance, PointStruct, VectorParams

    client = QdrantClient(":memory:")
    client.create_collection("legacy_default", vectors_config=VectorParams(size=16, distance=Distance.COSINE))
    source = _write_source(tmp_path, "legacy.txt", "legacy public policy")
    processor = DocumentProcessor()
    _, doc_id = processor.document_identity(source)
    client.upsert(
        collection_name="legacy_default",
        points=[
            PointStruct(
                id=1,
                vector=DeterministicTestEmbedder(16).embed_texts(["legacy public policy"])[0],
                payload={
                    "doc_id": doc_id,
                    "chunk_index": 0,
                    "content": "legacy public policy",
                    "role_mask": 0,
                    "dept_mask": 0,
                    "status": "active",
                },
            )
        ],
        wait=True,
    )
    service = TextIngestionService(
        processor,
        DeterministicTestEmbedder(16),
        QdrantTextWriter(client, "legacy_default", dimension=16),
    )

    service.ingest(source, role_mask=0, dept_mask=0, doc_version_epoch="default", provenance=_MANAGED)
    assert len(_text_records(client, "legacy_default")) == 1
    service.writer.seal_epoch("default")
    with pytest.raises(ValueError, match="is sealed"):
        service.ingest(source, role_mask=8, dept_mask=0, doc_version_epoch="default", provenance=_MANAGED)
    active_points = [
        record
        for record in client.scroll("legacy_default", limit=10, with_payload=True)[0]
        if record.payload.get("doc_id") == doc_id and record.payload.get("status") == "active"
    ]
    assert len(active_points) == 1
    assert active_points[0].payload["role_mask"] == 0


def test_same_content_with_changed_embedding_vector_requires_new_epoch(tmp_path):
    client = QdrantClient(":memory:")

    class MutableEmbedder:
        dimension = 16
        embedding_version = "same-declared-revision"

        def __init__(self):
            self.marker = 0.25

        def embed_texts(self, texts):
            return [[self.marker, 1.0 - self.marker] + [0.0] * 14 for _ in texts]

    embedder = MutableEmbedder()
    writer = QdrantTextWriter(client, "embedding_version", dimension=16)
    service = TextIngestionService(DocumentProcessor(), embedder, writer)
    source = _write_source(tmp_path, "same.txt", "same content")
    service.ingest(source, role_mask=0, dept_mask=0, doc_version_epoch="phase_1", provenance=_MANAGED)

    embedder.marker = 0.75
    with pytest.raises(ValueError, match="stored embedding vectors differ"):
        service.ingest(source, role_mask=0, dept_mask=0, doc_version_epoch="phase_1", provenance=_MANAGED)

    # Changing the declared model revision also requires a distinct epoch.
    embedder.embedding_version = "new-model-revision"
    revised_service = TextIngestionService(DocumentProcessor(), embedder, writer)
    with pytest.raises(ValueError, match="embedding version changed"):
        revised_service.ingest(source, role_mask=0, dept_mask=0, doc_version_epoch="phase_1", provenance=_MANAGED)
    assert revised_service.ingest(source, role_mask=0, dept_mask=0, doc_version_epoch="phase_2", provenance=_MANAGED)


def test_same_content_retry_accepts_qdrant_cosine_normalization(tmp_path):
    client = QdrantClient(":memory:")

    class UnnormalizedEmbedder:
        dimension = 16
        embedding_version = "unnormalized-model-v1"

        def embed_texts(self, texts):
            return [[3.0, 4.0] + [0.0] * 14 for _ in texts]

    embedder = UnnormalizedEmbedder()
    writer = QdrantTextWriter(client, "cosine_normalized_retry", dimension=16)
    service = TextIngestionService(DocumentProcessor(), embedder, writer)
    source = _write_source(tmp_path, "cosine.txt", "same content")

    first = service.ingest(source, role_mask=0, dept_mask=0, doc_version_epoch="phase_1", provenance=_MANAGED)
    retry = service.ingest(source, role_mask=0, dept_mask=0, doc_version_epoch="phase_1", provenance=_MANAGED)

    assert retry == first
    records, _ = client.scroll("cosine_normalized_retry", limit=10, with_payload=True, with_vectors=True)
    stored = next(record.vector for record in records if (record.payload or {}).get("doc_type") == "text")
    assert stored == pytest.approx([0.6, 0.8] + [0.0] * 14)


def test_epoch_rejects_different_embedding_revision_across_documents(tmp_path):
    client = QdrantClient(":memory:")

    class VersionedEmbedder:
        dimension = 16

        def __init__(self, version, marker):
            self.embedding_version = version
            self.marker = marker

        def embed_texts(self, texts):
            return [[self.marker, 1.0] + [0.0] * 14 for _ in texts]

    first_source = _write_source(tmp_path, "first.txt", "first document")
    second_source = _write_source(tmp_path, "second.txt", "second document")
    first_service = TextIngestionService(
        DocumentProcessor(),
        VersionedEmbedder("model-v1", 0.25),
        QdrantTextWriter(client, "epoch_embedding_version", dimension=16),
    )
    second_service = TextIngestionService(
        DocumentProcessor(),
        VersionedEmbedder("model-v2", 0.75),
        QdrantTextWriter(client, "epoch_embedding_version", dimension=16),
    )

    first_service.ingest(first_source, role_mask=0, dept_mask=0, doc_version_epoch="phase_1", provenance=_MANAGED)
    with pytest.raises(ValueError, match="embedding version changed within epoch"):
        second_service.ingest(second_source, role_mask=0, dept_mask=0, doc_version_epoch="phase_1", provenance=_MANAGED)

    points, _ = client.scroll("epoch_embedding_version", limit=100, with_payload=True)
    text_points = [point for point in points if point.payload.get("doc_type") == "text"]
    assert {point.payload["embedding_version"] for point in text_points} == {"model-v1"}


def test_default_epoch_does_not_mix_untyped_legacy_vectors(tmp_path):
    from qdrant_client.http.models import Distance, PointStruct, VectorParams

    client = QdrantClient(":memory:")
    client.create_collection("legacy_epoch_version", vectors_config=VectorParams(size=16, distance=Distance.COSINE))
    client.upsert(
        "legacy_epoch_version",
        points=[
            PointStruct(
                id="00000000-0000-0000-0000-000000000001",
                vector=[1.0] + [0.0] * 15,
                payload={"doc_id": "legacy-document", "status": "active", "role_mask": 0, "dept_mask": 0},
            )
        ],
        wait=True,
    )
    source = _write_source(tmp_path, "new.txt", "new model document")
    service = TextIngestionService(
        DocumentProcessor(),
        DeterministicTestEmbedder(16),
        QdrantTextWriter(client, "legacy_epoch_version", dimension=16),
    )

    with pytest.raises(ValueError, match="embedding version is missing or mixed within epoch"):
        service.ingest(source, role_mask=0, dept_mask=0, doc_version_epoch="default", provenance=_MANAGED)

    points, _ = client.scroll("legacy_epoch_version", limit=10, with_payload=True)
    assert [point.id for point in points] == ["00000000-0000-0000-0000-000000000001"]


def test_default_epoch_replaces_untyped_legacy_points_for_same_document(tmp_path):
    from qdrant_client.http.models import Distance, PointStruct, VectorParams

    client = QdrantClient(":memory:")
    client.create_collection(
        "legacy_source_replacement", vectors_config=VectorParams(size=16, distance=Distance.COSINE)
    )
    source = _write_source(tmp_path, "legacy-source.txt", "replacement content")
    _, doc_id = DocumentProcessor().document_identity(source)
    legacy_id = "00000000-0000-0000-0000-000000000002"
    client.upsert(
        "legacy_source_replacement",
        points=[
            PointStruct(
                id=legacy_id,
                vector=[1.0] + [0.0] * 15,
                payload={
                    "doc_id": doc_id,
                    "chunk_index": 0,
                    "content": "old public content",
                    "role_mask": 0,
                    "dept_mask": 0,
                    "status": "active",
                },
            )
        ],
        wait=True,
    )
    service = TextIngestionService(
        DocumentProcessor(),
        DeterministicTestEmbedder(16),
        QdrantTextWriter(client, "legacy_source_replacement", dimension=16),
    )

    service.ingest(source, role_mask=8, dept_mask=0, doc_version_epoch="default", provenance=_MANAGED)

    points = _text_records(client, "legacy_source_replacement")
    assert len(points) == 1
    assert points[0].payload["doc_id"] == doc_id
    assert points[0].payload["role_mask"] == 8
    assert legacy_id not in {point.id for point in points}


def test_document_epoch_replacement_uses_injected_lock(tmp_path):
    from contextlib import contextmanager

    lock_calls = []

    @contextmanager
    def replacement_lock(doc_id, epoch):
        lock_calls.append((doc_id, epoch))
        yield

    client = QdrantClient(":memory:")
    service = TextIngestionService(
        DocumentProcessor(),
        DeterministicTestEmbedder(16),
        QdrantTextWriter(client, "locked_test", dimension=16, replacement_lock=replacement_lock),
    )
    source = _write_source(tmp_path, "locked.txt", "serialized replacement")
    chunks = service.ingest(source, role_mask=0, dept_mask=0, doc_version_epoch="phase_1", provenance=_MANAGED)
    assert lock_calls == [(chunks[0].doc_id, "phase_1")]


def test_epoch_sealing_waits_for_inflight_epoch_writer(tmp_path):
    import threading

    from offline.text_ingestion import _local_epoch_lock

    client = QdrantClient(":memory:")
    writer = QdrantTextWriter(client, "seal_race", dimension=16)
    writer.ensure_collection()
    lock_held = threading.Event()
    release_writer = threading.Event()
    seal_finished = threading.Event()

    def hold_write_lock():
        with _local_epoch_lock("phase_race", shared=True):
            lock_held.set()
            release_writer.wait(timeout=2)

    def seal():
        writer.seal_epoch("phase_race")
        seal_finished.set()

    writer_thread = threading.Thread(target=hold_write_lock)
    writer_thread.start()
    assert lock_held.wait(timeout=1)
    sealer_thread = threading.Thread(target=seal)
    sealer_thread.start()
    assert not seal_finished.wait(timeout=0.05)
    release_writer.set()
    writer_thread.join(timeout=2)
    sealer_thread.join(timeout=2)

    assert seal_finished.is_set()
    assert writer._is_epoch_sealed("phase_race")


def test_default_replacement_lock_serializes_local_writers():
    import threading
    import time

    from offline.text_ingestion import _local_replacement_lock

    state_lock = threading.Lock()
    active_writers = 0
    max_active_writers = 0

    def replace():
        nonlocal active_writers, max_active_writers
        with _local_replacement_lock("same-document", "same-epoch"):
            with state_lock:
                active_writers += 1
                max_active_writers = max(max_active_writers, active_writers)
            time.sleep(0.03)
            with state_lock:
                active_writers -= 1

    writers = [threading.Thread(target=replace) for _ in range(2)]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join()
    assert max_active_writers == 1


@pytest.mark.parametrize(
    "first,second",
    [(("doc_a", "epoch_1"), ("doc_b", "epoch_1")), (("doc_a", "epoch_1"), ("doc_a", "epoch_2"))],
)
def test_replacement_lock_allows_different_documents_or_epochs(first, second):
    import threading

    from offline.text_ingestion import _local_replacement_lock

    rendezvous = threading.Barrier(2)
    errors = []

    def replace(doc_id, epoch):
        try:
            with _local_replacement_lock(doc_id, epoch):
                rendezvous.wait(timeout=1)
        except Exception as exc:
            errors.append(exc)

    first_thread = threading.Thread(target=replace, args=first)
    second_thread = threading.Thread(target=replace, args=second)
    first_thread.start()
    second_thread.start()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert not errors
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()


def test_replacement_lock_can_use_configured_shared_directory(monkeypatch, tmp_path):
    from offline.text_ingestion import _local_replacement_lock

    monkeypatch.setenv("OFFLINE_INGESTION_LOCK_DIR", str(tmp_path))
    with _local_replacement_lock("document", "epoch"):
        lock_files = list(tmp_path.glob("*.lock"))
    assert len(lock_files) == 1


def test_concurrent_writers_tolerate_collection_create_race(tmp_path):
    import threading

    import httpx
    from qdrant_client.http.exceptions import UnexpectedResponse

    class CreateRaceClient:
        def __init__(self, client):
            self.client = client
            self.observed_missing = threading.Barrier(2)
            self.create_lock = threading.Lock()

        def __getattr__(self, name):
            return getattr(self.client, name)

        def collection_exists(self, collection_name):
            existed = self.client.collection_exists(collection_name)
            self.observed_missing.wait(timeout=5)
            return existed

        def create_collection(self, **kwargs):
            with self.create_lock:
                if self.client.collection_exists(kwargs["collection_name"]):
                    raise UnexpectedResponse(
                        status_code=409,
                        reason_phrase="Conflict",
                        content=b'{"status":{"error":"Collection already exists"}}',
                        headers=httpx.Headers(),
                    )
                return self.client.create_collection(**kwargs)

    qdrant = QdrantClient(":memory:")
    raced_client = CreateRaceClient(qdrant)
    embedder = DeterministicTestEmbedder(16)
    writer = QdrantTextWriter(raced_client, "fresh_race", dimension=16)
    service = TextIngestionService(DocumentProcessor(), embedder, writer)
    failures = []

    def ingest(name):
        try:
            service.ingest(
                _write_source(tmp_path, f"{name}.txt", f"document {name}"),
                role_mask=0,
                dept_mask=0,
                doc_version_epoch="epoch_1",
                provenance=_MANAGED,
            )
        except Exception as exc:  # surfaced after joining both concurrent writers
            failures.append(exc)

    writers = [threading.Thread(target=ingest, args=(name,)) for name in ("alpha", "beta")]
    for thread in writers:
        thread.start()
    for thread in writers:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in writers)
    assert failures == []
    assert qdrant.collection_exists("fresh_race")
    assert len(_text_records(qdrant, "fresh_race")) == 2


def test_collection_schema_mismatch_fails_clearly():
    from qdrant_client.http.models import Distance, VectorParams

    client = QdrantClient(":memory:")
    client.create_collection("wrong_dimension", vectors_config=VectorParams(size=8, distance=Distance.COSINE))
    with pytest.raises(ValueError, match="dimension"):
        QdrantTextWriter(client, "wrong_dimension", dimension=16).ensure_collection()

    client.create_collection("wrong_distance", vectors_config=VectorParams(size=16, distance=Distance.DOT))
    with pytest.raises(ValueError, match="distance must be cosine"):
        QdrantTextWriter(client, "wrong_distance", dimension=16).ensure_collection()


def test_qdrant_environment_overrides_connection_config(monkeypatch):
    import qdrant_client

    import models.embedding_service as embedding_module
    from common.config import _apply_env_overrides

    monkeypatch.setenv("QDRANT_HOST", "127.0.0.1")
    monkeypatch.setenv("QDRANT_PORT", "7333")
    monkeypatch.setenv("QDRANT_GRPC_PORT", "7334")
    overridden = _apply_env_overrides({"qdrant": {"host": "qdrant", "port": 6333, "grpc_port": 6334}})
    assert overridden["qdrant"] == {"host": "127.0.0.1", "port": 7333, "grpc_port": 7334}
    created_clients = []

    def fake_qdrant_client(**kwargs):
        created_clients.append(kwargs)
        return kwargs

    monkeypatch.setattr(embedding_module, "config", overridden)
    monkeypatch.setattr(qdrant_client, "QdrantClient", fake_qdrant_client)
    reader = embedding_module.EmbeddingService.__new__(embedding_module.EmbeddingService)
    reader._qdrant_client = None
    assert reader.qdrant_client == {
        "host": "127.0.0.1",
        "port": 7333,
        "grpc_port": 7334,
        "prefer_grpc": True,
    }
    assert created_clients == [reader.qdrant_client]

    monkeypatch.delenv("QDRANT_HOST")
    monkeypatch.delenv("QDRANT_PORT")
    monkeypatch.delenv("QDRANT_GRPC_PORT")
    fallback = _apply_env_overrides({"qdrant": {"host": "qdrant", "port": 6333, "grpc_port": 6334}})
    assert fallback["qdrant"] == {"host": "qdrant", "port": 6333, "grpc_port": 6334}


def test_compose_app_uses_internal_qdrant_hostname():
    import yaml

    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    app_environment = compose["services"]["app"]["environment"]
    assert "QDRANT_HOST=qdrant" in app_environment
    assert compose["services"]["qdrant"]["ports"] == [
        "127.0.0.1:6333:6333",
        "127.0.0.1:6334:6334",
    ]


def test_qdrant_text_search_paginates_until_unique_documents_are_filled():
    from qdrant_client.http.models import Distance, PointStruct, VectorParams

    from models.embedding_service import EmbeddingService

    client = QdrantClient(":memory:")
    client.create_collection("unique_docs", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
    client.upsert(
        collection_name="unique_docs",
        points=[
            PointStruct(id=index, vector=[1.0, 0.0], payload={"doc_id": "long_doc", "content": f"chunk {index}"})
            for index in range(3)
        ]
        + [PointStruct(id=4, vector=[0.0, 1.0], payload={"doc_id": "other_doc", "content": "other"})],
        wait=True,
    )
    reader = EmbeddingService.__new__(EmbeddingService)
    reader._qdrant_client = client

    hits = reader.search_qdrant_text(np.array([1.0, 0.0]), collection_name="unique_docs", top_k=2)

    assert [hit["doc_id"] for hit in hits] == ["long_doc", "other_doc"]
    assert len(hits) == 2


def test_qdrant_filter_includes_active_status_and_epoch():
    from auth.bitmask_rbac import build_qdrant_filter

    qdrant_filter = build_qdrant_filter(0, 0, "v2")
    actual = {(item.key, item.match.value) for item in qdrant_filter.must}
    assert actual == {("status", "active")}
    assert [(item.key, item.match.value) for item in qdrant_filter.should] == [("doc_version_epoch", "v2")]
