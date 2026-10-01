"""OCR/image processing, CLIP embedding contract, and image writer lifecycle tests."""

from __future__ import annotations

import io

import pytest
from PIL import Image
from qdrant_client import QdrantClient

from offline.document_processor import ExtractedImage
from offline.embeddings import CLIPImageEmbedder, DeterministicTestImageEmbedder
from offline.image_processor import (
    DeterministicTestOCRProvider,
    ImageProcessor,
    OCRBlock,
    image_identity,
)
from offline.qdrant_writer import QdrantImageWriter


def _png_bytes(color="white", size=(64, 32)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def _extracted(index=0, page_number=None) -> ExtractedImage:
    return ExtractedImage(
        image_id=f"image-{index}",
        data=_png_bytes(),
        mime_type="image/png",
        page_number=page_number,
        image_index=index,
        metadata={"format": "image"},
    )


def _processor(dimension=8, **provider_kwargs) -> ImageProcessor:
    return ImageProcessor(
        DeterministicTestOCRProvider(**provider_kwargs),
        DeterministicTestImageEmbedder(dimension=dimension),
        visual_weight_repeat=3,
    )


def test_visual_weight_repeats_core_blocks_only():
    processor = _processor()
    core = OCRBlock("CORE", 0.95, (10, 10, 90, 40), font_size=30, is_center=True, is_large=True)
    edge = OCRBlock("EDGE", 0.5, (0, 0, 10, 5), font_size=5, is_center=False, is_large=False)
    text = processor.apply_visual_weights([core, edge])
    assert text == "CORE CORE CORE EDGE"


def test_image_processor_builds_authorized_record():
    processor = _processor(dimension=8)
    record = processor.process_image(
        image=_extracted(page_number=1),
        doc_id="doc",
        role_mask=2,
        dept_mask=4,
        doc_version_epoch="epoch_1",
        source_path="/data/scan.png",
    )
    assert record.role_mask == 2 and record.dept_mask == 4
    assert record.embedding_type == "image_clip"
    assert len(record.embedding) == 8
    assert record.status == "active"
    assert record.image_uri.endswith("#image=0")
    with pytest.raises(ValueError, match="uint32"):
        processor.process_image(
            image=_extracted(),
            doc_id="doc",
            role_mask=-1,
            dept_mask=0,
            doc_version_epoch="epoch_1",
            source_path="/data/scan.png",
        )


def test_standalone_image_identity_is_stable(tmp_path):
    path = tmp_path / "product.png"
    path.write_bytes(_png_bytes())
    processor = _processor()
    first = processor.process_standalone_image(
        path, role_mask=0, dept_mask=0, doc_version_epoch="epoch_1", doc_id="doc"
    )
    second = processor.process_standalone_image(
        path, role_mask=0, dept_mask=0, doc_version_epoch="epoch_1", doc_id="doc"
    )
    assert first.image_id == second.image_id
    assert first.image_id == image_identity("doc", __import__("hashlib").sha256(path.read_bytes()).hexdigest(), 0)


def test_clip_adapter_is_lazy_and_validates_config():
    assert CLIPImageEmbedder("configured-clip", dimension=8).embed_images([]) == []
    adapter = CLIPImageEmbedder("configured-clip", dimension=8, model_revision="clip-rev-1")
    assert adapter.embedding_version == "clip-rev-1:clip-image-v1"
    with pytest.raises(ValueError):
        CLIPImageEmbedder("", dimension=8)
    with pytest.raises(ValueError):
        CLIPImageEmbedder("clip", dimension=0)


def test_deterministic_image_embedder_is_stable():
    embedder = DeterministicTestImageEmbedder(dimension=16)
    data = _png_bytes()
    assert embedder.embed_images([data]) == embedder.embed_images([data])
    assert embedder.embed_images([data])[0] != embedder.embed_images([_png_bytes("black")])[0]
    assert len(embedder.embed_images([data])[0]) == 16


def _write_image(client, collection, epoch, role=0, dept=0, dimension=8, index=0, page=None):
    processor = _processor(dimension=dimension)
    record = processor.process_image(
        image=_extracted(index=index, page_number=page),
        doc_id="doc",
        role_mask=role,
        dept_mask=dept,
        doc_version_epoch=epoch,
        source_path="/data/scan.png",
    )
    writer = QdrantImageWriter(client, collection, dimension=dimension)
    writer.replace_document("doc", epoch, [record], [record.embedding])
    return record


def test_image_writer_round_trip_and_rbac_metadata():
    import models.embedding_service as embedding_module
    from models.embedding_service import EmbeddingService

    client = QdrantClient(":memory:")
    record = _write_image(client, "rag_image_test", "epoch_1", role=2, dept=4)

    saved = embedding_module.config
    try:
        embedding_module.config = {
            **saved,
            "embedding": {**saved["embedding"], "image_clip": {"collection": "rag_image_test"}},
        }
        reader = EmbeddingService.__new__(EmbeddingService)
        reader._qdrant_client = client
        import numpy as np

        from auth.bitmask_rbac import build_qdrant_image_filter

        hits = reader.search_qdrant_image(
            np.array(record.embedding), top_k=5, qdrant_filter=build_qdrant_image_filter(2, 4, "epoch_1")
        )
    finally:
        embedding_module.config = saved

    assert len(hits) == 1
    assert hits[0]["doc_id"] == "doc"
    assert hits[0]["metadata"]["role_mask"] == 2
    assert hits[0]["metadata"]["dept_mask"] == 4


def test_legacy_image_points_without_epoch_remain_retrievable():
    from qdrant_client.http.models import Distance, PointStruct, VectorParams

    client = QdrantClient(":memory:")
    client.create_collection("legacy_image", vectors_config=VectorParams(size=8, distance=Distance.COSINE))
    client.upsert(
        collection_name="legacy_image",
        points=[
            PointStruct(
                id="00000000-0000-0000-0000-0000000000aa",
                vector=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                payload={"doc_id": "legacy", "content": "legacy", "role_mask": 0, "dept_mask": 0, "status": "active"},
            )
        ],
        wait=True,
    )
    _write_image(client, "legacy_image", "epoch_1", index=1)
    points, _ = client.scroll("legacy_image", limit=10, with_payload=True)
    active_ids = {point.id for point in points if (point.payload or {}).get("status") == "active"}
    assert "00000000-0000-0000-0000-0000000000aa" in active_ids
    assert len(active_ids) == 2


def test_image_writer_epoch_coexistence_idempotence_and_seal():
    client = QdrantClient(":memory:")
    _write_image(client, "image_epochs", "epoch_a")
    _write_image(client, "image_epochs", "epoch_b")
    records, _ = client.scroll("image_epochs", limit=100, with_payload=True)
    image_points = [point for point in records if (point.payload or {}).get("doc_type") == "image"]
    assert {point.payload["doc_version_epoch"] for point in image_points} == {"epoch_a", "epoch_b"}
    epoch_a_ids = {point.id for point in image_points if point.payload["doc_version_epoch"] == "epoch_a"}

    _write_image(client, "image_epochs", "epoch_a")
    records, _ = client.scroll("image_epochs", limit=100, with_payload=True)
    assert {
        point.id
        for point in records
        if (point.payload or {}).get("doc_type") == "image" and point.payload["doc_version_epoch"] == "epoch_a"
    } == epoch_a_ids

    writer = QdrantImageWriter(client, "image_epochs", dimension=8)
    writer.seal_epoch("epoch_a")
    with pytest.raises(ValueError, match="is sealed"):
        _write_image(client, "image_epochs", "epoch_a", index=5)


def test_image_filter_epoch_isolation_legacy_default_and_rbac():
    import numpy as np
    from qdrant_client.http.models import Distance, PointStruct, VectorParams

    import models.embedding_service as embedding_module
    from auth.bitmask_rbac import build_qdrant_image_filter
    from common.models import RecallResult
    from models.embedding_service import EmbeddingService
    from retrieval.parallel_recall import ParallelRecallManager

    client = QdrantClient(":memory:")
    client.create_collection("img_epoch_filter", vectors_config=VectorParams(size=8, distance=Distance.COSINE))
    base = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    client.upsert(
        "img_epoch_filter",
        points=[
            PointStruct(
                id="00000000-0000-0000-0000-0000000000c1",
                vector=base,
                payload={
                    "doc_id": "legacy",
                    "image_id": "legacy",
                    "content": "legacy",
                    "status": "active",
                    "role_mask": 0,
                    "dept_mask": 0,
                },
            ),
            PointStruct(
                id="00000000-0000-0000-0000-0000000000c2",
                vector=base,
                payload={
                    "doc_id": "doc-a",
                    "image_id": "a",
                    "content": "a",
                    "status": "active",
                    "role_mask": 0,
                    "dept_mask": 0,
                    "doc_version_epoch": "epoch_a",
                },
            ),
            PointStruct(
                id="00000000-0000-0000-0000-0000000000c3",
                vector=base,
                payload={
                    "doc_id": "doc-b",
                    "image_id": "b",
                    "content": "b",
                    "status": "active",
                    "role_mask": 2,
                    "dept_mask": 4,
                    "doc_version_epoch": "epoch_b",
                },
            ),
        ],
        wait=True,
    )
    reader = EmbeddingService.__new__(EmbeddingService)
    reader._qdrant_client = client
    saved = embedding_module.config
    try:
        embedding_module.config = {
            **saved,
            "embedding": {**saved["embedding"], "image_clip": {"collection": "img_epoch_filter"}},
        }

        def doc_ids(epoch, role=0, dept=0):
            hits = reader.search_qdrant_image(
                np.array(base), top_k=10, qdrant_filter=build_qdrant_image_filter(role, dept, epoch)
            )
            return [hit["doc_id"] for hit in hits]

        assert doc_ids("epoch_a") == ["doc-a"]
        assert doc_ids("epoch_b") == ["doc-b"]
        # Legacy points without an epoch are only visible in the default epoch.
        assert doc_ids("default") == ["legacy"]
    finally:
        embedding_module.config = saved

    manager = ParallelRecallManager()
    restricted = RecallResult(
        doc_id="doc-b", content="b", score=1.0, source="clip_visual", metadata={"role_mask": 2, "dept_mask": 4}
    )
    assert manager._apply_rbac_filter([restricted], 0, 0) == []
    assert manager._apply_rbac_filter([restricted], 2, 4) == [restricted]


def test_image_writer_rejects_mixed_embedding_version():
    class OtherVersionEmbedder:
        dimension = 8
        embedding_type = "image_clip"
        embedding_version = "other-clip-v9"

        def embed_images(self, images):
            return [[0.0] * self.dimension for _ in images]

    from offline.image_processor import ImageProcessor

    processor = ImageProcessor(
        DeterministicTestOCRProvider(text="x"),
        OtherVersionEmbedder(),
        visual_weight_repeat=3,
    )
    client = QdrantClient(":memory:")
    _write_image(client, "image_version", "epoch_1")
    record = processor.process_image(
        image=_extracted(index=1),
        doc_id="other-doc",
        role_mask=0,
        dept_mask=0,
        doc_version_epoch="epoch_1",
        source_path="/data/scan.png",
    )
    writer = QdrantImageWriter(client, "image_version", dimension=8, embedding_version="other-clip-v9")
    with pytest.raises(ValueError, match="embedding version changed within epoch"):
        writer.replace_document("other-doc", "epoch_1", [record], [record.embedding])
