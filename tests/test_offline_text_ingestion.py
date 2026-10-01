"""Phase 1 text ingestion contract and local Qdrant integration tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from qdrant_client import QdrantClient

from offline.text_ingestion import (
    DeterministicTestEmbedder,
    DocumentProcessor,
    QdrantTextWriter,
    TextChunk,
    TextIngestionService,
)


def test_processor_handles_short_bom_overlap_stability_and_changed_content(tmp_path):
    path = tmp_path / "guide.txt"
    path.write_text("\ufeffAlpha beta gamma delta", encoding="utf-8")
    processor = DocumentProcessor(chunk_size=10, chunk_overlap=3)
    first = processor.process(path, role_mask=0, dept_mask=0, doc_version_epoch="phase_1")
    repeated = processor.process(path, role_mask=0, dept_mask=0, doc_version_epoch="phase_1")
    assert [item.chunk_id for item in first] == [item.chunk_id for item in repeated]
    assert [item.text for item in first][1].startswith(first[0].text[-3:])
    assert all(item.role_mask == item.dept_mask == 0 for item in first)
    path.write_text("Alpha beta gamma changed", encoding="utf-8")
    changed = processor.process(path, role_mask=0, dept_mask=0, doc_version_epoch="phase_1")
    assert changed[0].content_hash != first[0].content_hash
    assert changed[0].chunk_id != first[0].chunk_id


def test_processor_empty_utf8_and_unsupported_or_malformed_input(tmp_path):
    processor = DocumentProcessor()
    empty = tmp_path / "empty.txt"
    empty.write_text(" \n", encoding="utf-8")
    assert processor.process(empty, role_mask=0, dept_mask=0, doc_version_epoch="default") == []
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="cannot read UTF-8"):
        processor.process(bad, role_mask=0, dept_mask=0, doc_version_epoch="default")
    pdf = tmp_path / "unsupported.pdf"
    pdf.write_text("not a pdf", encoding="utf-8")
    with pytest.raises(ValueError, match="only .txt"):
        processor.process(pdf, role_mask=0, dept_mask=0, doc_version_epoch="default")


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
        service.ingest(source, role_mask=0, dept_mask=0, doc_version_epoch="epoch_1")
    assert not embedder.called
    assert not client.collection_exists("size_limit")

    chunk_limited = DocumentProcessor(chunk_size=2, chunk_overlap=0, max_chunks=2)
    with pytest.raises(ValueError, match="2-chunk ingestion limit"):
        chunk_limited.process(source, role_mask=0, dept_mask=0, doc_version_epoch="epoch_1")


@pytest.mark.parametrize("role,dept", [(-1, 0), (0, 2**32), (True, 0), (0, "1")])
def test_invalid_permission_masks_fail_before_ingestion(tmp_path, role, dept):
    path = tmp_path / "restricted.txt"
    path.write_text("restricted content", encoding="utf-8")
    with pytest.raises(ValueError, match="uint32"):
        DocumentProcessor().process(path, role_mask=role, dept_mask=dept, doc_version_epoch="default")


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
    chunks = service.ingest(source, role_mask=2, dept_mask=4, doc_version_epoch="phase_1")
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

    repeated = service.ingest(source, role_mask=2, dept_mask=4, doc_version_epoch="phase_1")
    assert [item.chunk_id for item in chunks] == [item.chunk_id for item in repeated]
    assert client.count("rag_text_16", exact=True).count == 1

    with pytest.raises(ValueError, match="uint32"):
        service.ingest(source, role_mask=2**32, dept_mask=4, doc_version_epoch="phase_1")


def test_same_content_coexists_and_remains_queryable_across_epochs(tmp_path):
    from auth.bitmask_rbac import build_qdrant_filter
    from models.embedding_service import EmbeddingService

    client = QdrantClient(":memory:")
    embedder = DeterministicTestEmbedder(16)
    writer = QdrantTextWriter(client, "epoch_test", dimension=16)
    service = TextIngestionService(DocumentProcessor(), embedder, writer)
    source = _write_source(tmp_path, "stable.txt", "shared collagen moisturizer content")

    epoch_a = service.ingest(source, role_mask=0, dept_mask=0, doc_version_epoch="epoch_a")
    points_a, _ = client.scroll("epoch_test", limit=10, with_payload=True)
    ids_a = {point.id for point in points_a}
    assert len(ids_a) == len(epoch_a)

    epoch_b = service.ingest(source, role_mask=0, dept_mask=0, doc_version_epoch="epoch_b")
    points_b, _ = client.scroll("epoch_test", limit=10, with_payload=True)
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

    repeated_b = service.ingest(source, role_mask=0, dept_mask=0, doc_version_epoch="epoch_b")
    points_after_repeat, _ = client.scroll("epoch_test", limit=10, with_payload=True)
    assert [chunk.chunk_id for chunk in repeated_b] == [chunk.chunk_id for chunk in epoch_b]
    assert len(points_after_repeat) == len(epoch_a) + len(epoch_b)
    assert {point.id for point in points_after_repeat if point.payload["doc_version_epoch"] == "epoch_a"} == ids_a


def test_reingestion_removes_old_or_emptied_source_chunks(tmp_path):
    client = QdrantClient(":memory:")
    service = TextIngestionService(
        DocumentProcessor(chunk_size=12, chunk_overlap=0),
        DeterministicTestEmbedder(16),
        QdrantTextWriter(client, "replace_test", dimension=16),
    )
    source = _write_source(tmp_path, "mutable.txt", "old content that spans multiple chunks")
    old_chunks = service.ingest(source, role_mask=2, dept_mask=4, doc_version_epoch="phase_1")
    assert len(old_chunks) > 1
    assert client.count("replace_test", exact=True).count == len(old_chunks)

    source.write_text("new content", encoding="utf-8")
    new_chunks = service.ingest(source, role_mask=2, dept_mask=4, doc_version_epoch="phase_1")
    assert len(new_chunks) == 1
    assert client.count("replace_test", exact=True).count == 1
    records, _ = client.scroll("replace_test", limit=10, with_payload=True)
    assert [record.payload["content"] for record in records] == ["new content"]

    # Replacing B must not archive or delete A, even for the same logical source.
    epoch_a = service.ingest(source, role_mask=2, dept_mask=4, doc_version_epoch="epoch_a")
    epoch_a_records, _ = client.scroll("replace_test", limit=10, with_payload=True)
    epoch_a_ids = {record.id for record in epoch_a_records if record.payload["doc_version_epoch"] == "epoch_a"}
    assert len(epoch_a_ids) == len(epoch_a)
    source.write_text("replacement for B", encoding="utf-8")
    service.ingest(source, role_mask=2, dept_mask=4, doc_version_epoch="phase_1")
    records, _ = client.scroll("replace_test", limit=10, with_payload=True)
    assert {record.payload["doc_version_epoch"] for record in records} == {"epoch_a", "phase_1"}
    assert {record.id for record in records if record.payload["doc_version_epoch"] == "epoch_a"} == {*epoch_a_ids}

    source.write_text(" \n", encoding="utf-8")
    assert service.ingest(source, role_mask=2, dept_mask=4, doc_version_epoch="phase_1") == []
    records, _ = client.scroll("replace_test", limit=10, with_payload=True)
    assert {record.payload["doc_version_epoch"] for record in records} == {"epoch_a"}


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
    chunks = service.ingest(source, role_mask=0, dept_mask=0, doc_version_epoch="phase_1")
    assert lock_calls == [(chunks[0].doc_id, "phase_1")]


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
    assert qdrant.count("fresh_race", exact=True).count == 2


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
    assert actual == {("status", "active"), ("doc_version_epoch", "v2")}
