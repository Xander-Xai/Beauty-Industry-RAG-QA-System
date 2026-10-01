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

    class Model:
        def encode(self, texts, **kwargs):
            return [[0.1, 0.2] for _ in texts]

    adapter._model = Model()
    assert adapter.embed_texts(["hello"]) == [[0.1, 0.2]]
    adapter._model.encode = lambda texts, **kwargs: [[0.1] for _ in texts]
    with pytest.raises(ValueError, match="2 finite values"):
        adapter.embed_texts(["hello"])


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


def test_qdrant_filter_includes_active_status_and_epoch():
    from auth.bitmask_rbac import build_qdrant_filter

    qdrant_filter = build_qdrant_filter(0, 0, "v2")
    actual = {(item.key, item.match.value) for item in qdrant_filter.must}
    assert actual == {("status", "active"), ("doc_version_epoch", "v2")}
