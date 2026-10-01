"""Epoch-aware Qdrant image writer.

Text ingestion keeps its Phase 1 writer in ``offline.text_ingestion``. This
module adds the CLIP image collection writer with the same lifecycle contract:
staging epochs are mutable, sealed epochs are immutable, physical point ids are
versioned by epoch, and each epoch is pinned to one embedding version.

New image points carry ``doc_version_epoch``. The online image filter does not
yet constrain by epoch, so pre-epoch legacy image points remain retrievable.
"""

from __future__ import annotations

import math
import uuid
from typing import Protocol

from offline.chunking import POINT_NAMESPACE
from offline.file_lock import FileLockProvider
from offline.validation import validate_epoch, validate_permissions

_FILE_LOCK_PROVIDER = FileLockProvider()


class ImageRecord(Protocol):
    doc_id: str
    image_id: str
    image_index: int
    page_number: int | None
    source_path: str
    image_uri: str
    ocr_full_text: str
    ocr_main_text: str
    embedding_type: str
    embedding_version: str
    role_mask: int
    dept_mask: int
    status: str
    doc_version_epoch: str
    metadata: dict


def ensure_cosine_collection(client, collection_name: str, dimension: int) -> None:
    from qdrant_client.http.exceptions import UnexpectedResponse
    from qdrant_client.http.models import Distance, VectorParams

    if not client.collection_exists(collection_name):
        try:
            client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
            )
        except UnexpectedResponse as exc:
            message = exc.content.decode("utf-8", errors="replace").lower()
            if exc.status_code != 409 or "already exists" not in message:
                raise
    info = client.get_collection(collection_name)
    vectors = info.config.params.vectors
    if isinstance(vectors, dict):
        raise ValueError("named Qdrant vectors are not supported")
    if vectors.size != dimension:
        raise ValueError(f"collection dimension does not match configured dimension {dimension}")
    if vectors.distance != Distance.COSINE:
        raise ValueError("collection distance must be cosine")


def _versioned_image_point_id(image_id: str, epoch: str) -> str:
    return str(uuid.uuid5(POINT_NAMESPACE, f"image:{image_id}:{epoch}"))


class QdrantImageWriter:
    """Write online-compatible CLIP image points with epoch lifecycle support."""

    def __init__(
        self,
        client,
        collection_name: str = "rag_image_512",
        dimension: int = 512,
        replacement_lock=None,
        embedding_version: str = "unspecified-image-v1",
    ):
        if not collection_name or type(dimension) is not int or dimension <= 0:
            raise ValueError("a collection name and positive vector dimension are required")
        self.client = client
        self.collection_name = collection_name
        self.dimension = dimension
        self.replacement_lock = replacement_lock
        self.embedding_version = embedding_version

    def ensure_collection(self) -> None:
        ensure_cosine_collection(self.client, self.collection_name, self.dimension)

    def _epoch_seal_id(self, epoch: str) -> str:
        return str(uuid.uuid5(POINT_NAMESPACE, f"image-epoch-seal:{epoch}"))

    def _epoch_embedding_version_id(self, epoch: str) -> str:
        return str(uuid.uuid5(POINT_NAMESPACE, f"image-epoch-embedding-version:{epoch}"))

    @staticmethod
    def _epoch_conditions(epoch: str):
        from qdrant_client.http.models import FieldCondition, IsEmptyCondition, MatchValue, PayloadField

        conditions = [FieldCondition(key="doc_version_epoch", match=MatchValue(value=epoch))]
        if epoch == "default":
            conditions.append(IsEmptyCondition(is_empty=PayloadField(key="doc_version_epoch")))
        return conditions

    def _build_points(self, images: list[ImageRecord], vectors: list[list[float]]):
        from qdrant_client.http.models import PointStruct

        if len(images) != len(vectors):
            raise ValueError("image and vector counts differ")
        _validate_image_vectors(vectors, len(images), self.dimension)
        points = []
        for image, vector in zip(images, vectors, strict=True):
            validate_permissions(image.role_mask, image.dept_mask)
            validate_epoch(image.doc_version_epoch)
            payload = {
                "doc_id": image.doc_id,
                "image_id": image.image_id,
                "image_index": image.image_index,
                "page_number": image.page_number,
                "source_path": image.source_path,
                "image_uri": image.image_uri,
                "content": image.ocr_full_text,
                "ocr_full_text": image.ocr_full_text,
                "ocr_main_text": image.ocr_main_text,
                "doc_type": "image",
                "embedding_type": image.embedding_type,
                "embedding_version": self.embedding_version,
                "role_mask": image.role_mask,
                "dept_mask": image.dept_mask,
                "status": image.status,
                "doc_version_epoch": image.doc_version_epoch,
                "metadata": image.metadata,
            }
            points.append(
                PointStruct(
                    id=_versioned_image_point_id(image.image_id, image.doc_version_epoch),
                    vector=vector,
                    payload=payload,
                )
            )
        return points

    def replace_document(
        self, doc_id: str, doc_version_epoch: str, images: list[ImageRecord], vectors: list[list[float]]
    ) -> None:
        """Replace one document's images in staging; sealed epochs stay immutable."""
        validate_epoch(doc_version_epoch)
        points = self._build_points(images, vectors)
        if any(image.doc_id != doc_id or image.doc_version_epoch != doc_version_epoch for image in images):
            raise ValueError("replacement images must match the requested document and epoch")

        from qdrant_client.http.models import FieldCondition, Filter, MatchValue

        epoch_lock = _FILE_LOCK_PROVIDER.epoch_lock(doc_version_epoch, shared=True)
        lock_context = (self.replacement_lock or _FILE_LOCK_PROVIDER.replacement_lock)(doc_id, doc_version_epoch)
        with epoch_lock, lock_context:
            self.ensure_collection()
            source_filter = Filter(
                must=[
                    FieldCondition(key="doc_id", match=MatchValue(value=doc_id)),
                    Filter(should=self._epoch_conditions(doc_version_epoch)),
                ]
            )
            existing = []
            offset = None
            while True:
                records, offset = self.client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=source_filter,
                    limit=256,
                    with_payload=True,
                    with_vectors=True,
                    offset=offset,
                )
                existing.extend(records)
                if offset is None:
                    break

            sealed = self._is_epoch_sealed(doc_version_epoch)
            existing_signature = self._document_signature(record.payload or {} for record in existing)
            desired_signature = self._document_signature(point.payload or {} for point in points)
            content_matches = existing_signature == desired_signature and len(existing) == len(points)
            vectors_match = self._vectors_match(existing, points)
            old_versions = {(record.payload or {}).get("embedding_version") for record in existing}
            legacy_unversioned = (
                bool(existing)
                and doc_version_epoch == "default"
                and all(
                    (record.payload or {}).get("doc_version_epoch") is None
                    and (record.payload or {}).get("embedding_version") is None
                    for record in existing
                )
            )
            version_matches = old_versions == {self.embedding_version} or legacy_unversioned
            if existing and not version_matches:
                raise ValueError("image embedding version changed or is unknown; ingest into a new epoch")
            if content_matches and not vectors_match:
                raise ValueError("stored image vectors differ; ingest into a new epoch")
            if sealed:
                if content_matches and vectors_match and version_matches:
                    return
                if not existing and not points:
                    return
                raise ValueError(f"knowledge epoch {doc_version_epoch!r} is sealed; write to a new epoch")
            if points:
                self._ensure_epoch_embedding_version(doc_version_epoch, doc_id)

            existing_ids = [record.id for record in existing]
            if existing_ids:
                self.client.set_payload(
                    collection_name=self.collection_name,
                    payload={"status": "archived"},
                    points=existing_ids,
                    wait=True,
                )
            if points:
                self.client.upsert(collection_name=self.collection_name, points=points, wait=True)
            replacement_ids = {point.id for point in points}
            stale_ids = [point_id for point_id in existing_ids if point_id not in replacement_ids]
            if stale_ids:
                self.client.delete(collection_name=self.collection_name, points_selector=stale_ids, wait=True)

    @staticmethod
    def _document_signature(payloads):
        def signature(payload):
            return (
                payload.get("image_id"),
                payload.get("image_index"),
                payload.get("page_number"),
                payload.get("ocr_full_text"),
                payload.get("ocr_main_text"),
                payload.get("role_mask"),
                payload.get("dept_mask"),
                payload.get("status"),
            )

        return sorted((signature(payload) for payload in payloads), key=repr)

    @staticmethod
    def _vectors_match(records, points) -> bool:
        record_vectors = {(r.payload or {}).get("image_id"): r.vector for r in records if r.payload}
        point_vectors = {(p.payload or {}).get("image_id"): p.vector for p in points if p.payload}
        if record_vectors.keys() != point_vectors.keys():
            return False
        for image_id, stored in record_vectors.items():
            current = point_vectors[image_id]
            if isinstance(stored, dict) or isinstance(current, dict) or len(stored) != len(current):
                return False
            stored_norm = math.sqrt(sum(float(value) ** 2 for value in stored))
            current_norm = math.sqrt(sum(float(value) ** 2 for value in current))
            if stored_norm == 0 or current_norm == 0:
                if stored_norm != current_norm:
                    return False
                continue
            if not all(
                math.isclose(float(left) / stored_norm, float(right) / current_norm, rel_tol=1e-6, abs_tol=1e-7)
                for left, right in zip(stored, current, strict=True)
            ):
                return False
        return True

    def _ensure_epoch_embedding_version(self, epoch: str, replacing_doc_id: str) -> None:
        from qdrant_client.http.models import FieldCondition, Filter, MatchValue, PointStruct

        with _FILE_LOCK_PROVIDER.epoch_embedding_lock(epoch):
            marker_id = self._epoch_embedding_version_id(epoch)
            marker = self.client.retrieve(
                collection_name=self.collection_name,
                ids=[marker_id],
                with_payload=True,
                with_vectors=False,
            )
            marker_payload = marker[0].payload or {} if marker else {}
            pinned_version = marker_payload.get("embedding_version")
            if pinned_version is not None and pinned_version != self.embedding_version:
                raise ValueError("image embedding version changed within epoch; ingest into a new epoch")
            if pinned_version == self.embedding_version:
                return

            epoch_filter = Filter(
                must=[
                    FieldCondition(key="status", match=MatchValue(value="active")),
                    Filter(should=self._epoch_conditions(epoch)),
                ]
            )
            existing_versions = set()
            records = []
            offset = None
            while True:
                page, offset = self.client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=epoch_filter,
                    limit=256,
                    with_payload=["embedding_version", "doc_id"],
                    with_vectors=False,
                    offset=offset,
                )
                records.extend(page)
                existing_versions.update((record.payload or {}).get("embedding_version") for record in page)
                if offset is None:
                    break

            unknown_legacy = [record for record in records if (record.payload or {}).get("embedding_version") is None]
            if any(
                epoch != "default" or (record.payload or {}).get("doc_id") != replacing_doc_id
                for record in unknown_legacy
            ):
                raise ValueError("image embedding version is missing or mixed within epoch; ingest into a new epoch")
            known_versions = existing_versions - {None}
            if known_versions and known_versions != {self.embedding_version}:
                raise ValueError("image embedding version is missing or mixed within epoch; ingest into a new epoch")
            if unknown_legacy:
                self.client.set_payload(
                    collection_name=self.collection_name,
                    payload={"status": "archived"},
                    points=[record.id for record in unknown_legacy],
                    wait=True,
                )
            if pinned_version is None:
                self.client.upsert(
                    collection_name=self.collection_name,
                    points=[
                        PointStruct(
                            id=marker_id,
                            vector=[1.0] + [0.0] * (self.dimension - 1),
                            payload={
                                "doc_type": "epoch_manifest",
                                "manifest_type": "embedding_version",
                                "embedding_version": self.embedding_version,
                                "doc_version_epoch": epoch,
                                "status": "archived",
                            },
                        )
                    ],
                    wait=True,
                )

    def ensure_epoch_embedding_version(self, epoch: str) -> None:
        """Pin an epoch to this writer's image embedding version without writing an image."""
        validate_epoch(epoch)
        self.ensure_collection()
        self._ensure_epoch_embedding_version(epoch, "__snapshot__")

    def _is_epoch_sealed(self, epoch: str) -> bool:
        if not self.client.collection_exists(self.collection_name):
            return False
        records = self.client.retrieve(
            collection_name=self.collection_name,
            ids=[self._epoch_seal_id(epoch)],
            with_payload=True,
            with_vectors=False,
        )
        return bool(records and (records[0].payload or {}).get("epoch_state") == "sealed")

    def seal_epoch(self, epoch: str) -> None:
        validate_epoch(epoch)
        from qdrant_client.http.models import PointStruct

        with _FILE_LOCK_PROVIDER.epoch_lock(epoch, shared=False):
            self.ensure_collection()
            self.client.upsert(
                collection_name=self.collection_name,
                points=[
                    PointStruct(
                        id=self._epoch_seal_id(epoch),
                        vector=[1.0] + [0.0] * (self.dimension - 1),
                        payload={
                            "doc_type": "epoch_manifest",
                            "epoch_state": "sealed",
                            "doc_version_epoch": epoch,
                            "status": "archived",
                        },
                    )
                ],
                wait=True,
            )


def _validate_image_vectors(vectors: list[list[float]], expected_count: int, dimension: int) -> None:
    if len(vectors) != expected_count:
        raise ValueError(f"embedder returned {len(vectors)} vectors for {expected_count} images")
    for index, vector in enumerate(vectors):
        if len(vector) != dimension or any(not math.isfinite(float(value)) for value in vector):
            raise ValueError(f"image embedding {index} must contain {dimension} finite values")
