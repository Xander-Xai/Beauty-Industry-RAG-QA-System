"""A deterministic TXT to Qdrant ingestion path for the Phase 1 slice."""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from offline.file_lock import FileLockProvider
from offline.source_trust import enforce_writable_provenance, persisted_trust_class

_UINT32_MAX = 0xFFFFFFFF
_EPOCH_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_POINT_NAMESPACE = uuid.UUID("74f7d957-77e6-4cac-9af6-a5f09c081215")


_FILE_LOCK_PROVIDER = FileLockProvider()


@contextmanager
def _local_replacement_lock(doc_id: str, epoch: str):
    """Serialize same-host CLI writers with a non-evictable OS file lock."""
    with _FILE_LOCK_PROVIDER.replacement_lock(doc_id, epoch):
        yield


@contextmanager
def _local_epoch_lock(epoch: str, *, shared: bool):
    """Coordinate epoch writes with sealing across local CLI processes."""
    with _FILE_LOCK_PROVIDER.epoch_lock(epoch, shared=shared):
        yield


@contextmanager
def _local_epoch_embedding_lock(epoch: str):
    """Serialize initialization and validation of an epoch's embedding contract."""
    with _FILE_LOCK_PROVIDER.epoch_embedding_lock(epoch):
        yield


@dataclass(frozen=True)
class TextChunk:
    """Parsed text fragment plus the storage and authorization contract."""

    doc_id: str
    chunk_id: str
    source_path: str
    text: str
    chunk_index: int
    content_hash: str
    role_mask: int
    dept_mask: int
    status: str
    doc_version_epoch: str
    metadata: dict[str, str]
    #: Canonical ingestion provenance, persisted verbatim onto the point.
    provenance: dict[str, str] = field(default_factory=dict)


class TextEmbedder(Protocol):
    dimension: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


def _validate_permissions(role_mask: int, dept_mask: int) -> None:
    for name, value in (("role_mask", role_mask), ("dept_mask", dept_mask)):
        if type(value) is not int or not 0 <= value <= _UINT32_MAX:
            raise ValueError(f"{name} must be an integer uint32")


def _validate_epoch(epoch: str) -> None:
    if not isinstance(epoch, str) or not _EPOCH_RE.fullmatch(epoch):
        raise ValueError("doc_version_epoch must contain only letters, digits, _ or -")


def _versioned_point_id(chunk: TextChunk) -> str:
    """Return the physical Qdrant identity for a logical chunk in one epoch."""
    return str(uuid.uuid5(_POINT_NAMESPACE, f"{chunk.chunk_id}:{chunk.doc_version_epoch}"))


class DocumentProcessor:
    """Read UTF-8 TXT documents and split them into deterministic character windows."""

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        max_document_bytes: int = 131_072,
        max_chunks: int = 256,
        source_root: str | Path | None = None,
    ):
        if type(chunk_size) is not int or chunk_size <= 0:
            raise ValueError("chunk_size must be a positive integer")
        if type(chunk_overlap) is not int or not 0 <= chunk_overlap < chunk_size:
            raise ValueError("chunk_overlap must be an integer in [0, chunk_size)")
        if type(max_document_bytes) is not int or max_document_bytes <= 0:
            raise ValueError("max_document_bytes must be a positive integer")
        if type(max_chunks) is not int or max_chunks <= 0:
            raise ValueError("max_chunks must be a positive integer")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_document_bytes = max_document_bytes
        self.max_chunks = max_chunks
        self.source_root = Path(source_root).resolve() if source_root is not None else None

    def document_identity(self, source: str | Path, source_id: str | None = None) -> tuple[str, str]:
        resolved_path = Path(source).resolve()
        source_path = str(resolved_path)
        if source_id is not None:
            identity = source_id.strip()
            if not identity:
                raise ValueError("source_id must not be empty")
        elif self.source_root is not None:
            try:
                identity = resolved_path.relative_to(self.source_root).as_posix()
            except ValueError as exc:
                raise ValueError(
                    f"source is outside the configured data root {self.source_root}; provide an explicit source_id"
                ) from exc
        else:
            # Low-level/test users without a configured root retain path-based isolation.
            identity = source_path
        doc_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return source_path, doc_id

    def process(
        self,
        source: str | Path,
        *,
        role_mask: int,
        dept_mask: int,
        doc_version_epoch: str,
        source_id: str | None = None,
        provenance: dict | None = None,
    ) -> list[TextChunk]:
        _validate_permissions(role_mask, dept_mask)
        _validate_epoch(doc_version_epoch)
        path = Path(source)
        if path.suffix.lower() != ".txt":
            raise ValueError(f"unsupported document type {path.suffix or '<none>'}; only .txt is supported")
        try:
            with path.open("rb") as source_file:
                raw = source_file.read(self.max_document_bytes + 1)
            if len(raw) > self.max_document_bytes:
                raise ValueError(f"text document exceeds the {self.max_document_bytes}-byte ingestion limit: {path}")
            text = raw.decode("utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"cannot read UTF-8 text document {path}: {exc}") from exc

        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            return []
        step = self.chunk_size - self.chunk_overlap
        chunk_count = 1 if len(text) <= self.chunk_size else math.ceil((len(text) - self.chunk_size) / step) + 1
        if chunk_count > self.max_chunks:
            raise ValueError(f"text document exceeds the {self.max_chunks}-chunk ingestion limit: {path}")
        source_path, doc_id = self.document_identity(path, source_id)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        chunks: list[TextChunk] = []
        for chunk_index, offset in enumerate(range(0, len(text), step)):
            chunk_text = text[offset : offset + self.chunk_size]
            if not chunk_text:
                continue
            identity = f"{doc_id}:{content_hash}:{chunk_index}:{hashlib.sha256(chunk_text.encode()).hexdigest()}"
            chunk_id = str(uuid.uuid5(_POINT_NAMESPACE, identity))
            chunks.append(
                TextChunk(
                    doc_id=doc_id,
                    chunk_id=chunk_id,
                    source_path=source_path,
                    text=chunk_text,
                    chunk_index=chunk_index,
                    content_hash=content_hash,
                    role_mask=role_mask,
                    dept_mask=dept_mask,
                    status="active",
                    doc_version_epoch=doc_version_epoch,
                    metadata={"source_path": source_path, "format": "txt"},
                    provenance=dict(provenance or {}),
                )
            )
            if offset + self.chunk_size >= len(text):
                break
        return chunks


class BGETextEmbedder:
    """Production adapter using the same BGE tokenizer and mean pooling as online queries."""

    def __init__(
        self,
        model_name_or_path: str,
        dimension: int = 768,
        batch_size: int = 32,
        model_revision: str | None = None,
    ):
        if not model_name_or_path:
            raise ValueError("a configured BGE model name or path is required")
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")
        self.model_name_or_path = model_name_or_path
        self.dimension = dimension
        self.batch_size = batch_size
        self.embedding_version = f"{model_revision or model_name_or_path}:attention-mask-mean-pooling-v1"
        self._embedding_service = None

    def _load_embedding_service(self):
        if self._embedding_service is None:
            try:
                from models.embedding_service import EmbeddingService

                self._embedding_service = EmbeddingService(model_path=self.model_name_or_path)
            except Exception as exc:
                raise RuntimeError(f"failed to initialize BGE model {self.model_name_or_path!r}: {exc}") from exc
        return self._embedding_service

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            service = self._load_embedding_service()
        except Exception as exc:
            raise RuntimeError(f"BGE embedding failed for {self.model_name_or_path!r}: {exc}") from exc
        rows = []
        for offset in range(0, len(texts), self.batch_size):
            batch = texts[offset : offset + self.batch_size]
            try:
                vectors = service.encode_texts_batch(batch)
            except Exception as exc:
                raise RuntimeError(f"BGE embedding failed for {self.model_name_or_path!r}: {exc}") from exc
            batch_rows = vectors.tolist() if hasattr(vectors, "tolist") else vectors
            _validate_vectors(batch_rows, len(batch), self.dimension)
            rows.extend(batch_rows)
        _validate_vectors(rows, len(texts), self.dimension)
        return rows


class DeterministicTestEmbedder:
    """Small stable feature-hash embedder for integration tests; this is not BGE."""

    def __init__(self, dimension: int = 768):
        if type(dimension) is not int or dimension <= 0:
            raise ValueError("dimension must be a positive integer")
        self.dimension = dimension
        self.embedding_version = "test-feature-hash-v1"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        rows = []
        for text in texts:
            vector = [0.0] * self.dimension
            tokens = text.casefold().split()
            for token in tokens:
                index = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big") % self.dimension
                vector[index] += 1.0
            norm = math.sqrt(sum(value * value for value in vector))
            if norm:
                vector = [value / norm for value in vector]
            rows.append(vector)
        _validate_vectors(rows, len(texts), self.dimension)
        return rows


def _validate_vectors(vectors: list[list[float]], expected_count: int, dimension: int) -> None:
    if len(vectors) != expected_count:
        raise ValueError(f"embedder returned {len(vectors)} vectors for {expected_count} texts")
    for index, vector in enumerate(vectors):
        if len(vector) != dimension or any(not math.isfinite(float(value)) for value in vector):
            raise ValueError(f"embedding {index} must contain {dimension} finite values")


class QdrantTextWriter:
    """Write online-reader-compatible text points to the configured Qdrant collection."""

    def __init__(
        self,
        client,
        collection_name: str = "rag_text_768",
        dimension: int = 768,
        replacement_lock=None,
        embedding_version: str = "unspecified-v1",
    ):
        if not collection_name or type(dimension) is not int or dimension <= 0:
            raise ValueError("a collection name and positive vector dimension are required")
        self.client = client
        self.collection_name = collection_name
        self.dimension = dimension
        self.replacement_lock = replacement_lock
        self.embedding_version = embedding_version

    def ensure_collection(self) -> None:
        from qdrant_client.http.exceptions import UnexpectedResponse
        from qdrant_client.http.models import Distance, VectorParams

        if not self.client.collection_exists(self.collection_name):
            try:
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(size=self.dimension, distance=Distance.COSINE),
                )
            except UnexpectedResponse as exc:
                # Qdrant reports a duplicate concurrent create as HTTP 409. Only
                # treat the explicit already-exists response as a winning race.
                message = exc.content.decode("utf-8", errors="replace").lower()
                if exc.status_code != 409 or "already exists" not in message:
                    raise
        info = self.client.get_collection(self.collection_name)
        vectors = info.config.params.vectors
        if isinstance(vectors, dict):
            raise ValueError("named Qdrant vectors are not supported by the text ingestion slice")
        if vectors.size != self.dimension:
            raise ValueError(f"collection dimension does not match configured dimension {self.dimension}")
        if vectors.distance != Distance.COSINE:
            raise ValueError("collection distance must be cosine for the text ingestion slice")

    def upsert(self, chunks: list[TextChunk], vectors: list[list[float]]) -> None:
        points = self._build_points(chunks, vectors)
        if points:
            self.ensure_collection()
            self.client.upsert(collection_name=self.collection_name, points=points, wait=True)

    def replace_document(
        self,
        doc_id: str,
        doc_version_epoch: str,
        chunks: list[TextChunk],
        vectors: list[list[float]],
    ) -> None:
        """Replace one document in staging, while sealed epochs remain immutable."""
        _validate_epoch(doc_version_epoch)
        points = self._build_points(chunks, vectors)
        if any(chunk.doc_id != doc_id or chunk.doc_version_epoch != doc_version_epoch for chunk in chunks):
            raise ValueError("replacement chunks must match the requested document and epoch")

        epoch_lock = _local_epoch_lock(doc_version_epoch, shared=True)
        lock_context = (self.replacement_lock or _local_replacement_lock)(doc_id, doc_version_epoch)
        with epoch_lock:
            with lock_context:
                self.ensure_collection()
                from qdrant_client.http.models import FieldCondition, Filter, IsEmptyCondition, MatchValue, PayloadField

                epoch_conditions = [FieldCondition(key="doc_version_epoch", match=MatchValue(value=doc_version_epoch))]
                if doc_version_epoch == "default":
                    # Treat pre-slice points as members of the legacy default snapshot.
                    epoch_conditions.append(IsEmptyCondition(is_empty=PayloadField(key="doc_version_epoch")))
                existing_records = []
                offset = None
                source_filter = Filter(
                    must=[
                        FieldCondition(key="doc_id", match=MatchValue(value=doc_id)),
                        Filter(should=epoch_conditions),
                    ],
                )
                while True:
                    records, offset = self.client.scroll(
                        collection_name=self.collection_name,
                        scroll_filter=source_filter,
                        limit=256,
                        with_payload=[
                            "chunk_index",
                            "content",
                            "role_mask",
                            "dept_mask",
                            "status",
                            "embedding_version",
                            "doc_version_epoch",
                            # Provenance is part of the document signature below,
                            # so it has to be selected explicitly: an explicit
                            # ``with_payload`` list is a projection, not a filter.
                            "provenance",
                        ],
                        with_vectors=True,
                        offset=offset,
                    )
                    existing_records.extend(records)
                    if offset is None:
                        break

                sealed = self._is_epoch_sealed(doc_version_epoch)
                existing_signature = self._document_signature(record.payload or {} for record in existing_records)
                desired_signature = self._document_signature(point.payload or {} for point in points)
                content_matches = existing_signature == desired_signature and len(existing_records) == len(points)
                vectors_match = self._document_vectors_match(existing_records, points)
                old_versions = {(record.payload or {}).get("embedding_version") for record in existing_records}
                legacy_unversioned = (
                    bool(existing_records)
                    and doc_version_epoch == "default"
                    and all(
                        (record.payload or {}).get("doc_version_epoch") is None
                        and (record.payload or {}).get("embedding_version") is None
                        for record in existing_records
                    )
                )
                version_matches = old_versions == {self.embedding_version} or legacy_unversioned
                if existing_records and not version_matches:
                    raise ValueError("embedding version changed or is unknown; ingest into a new epoch")
                if content_matches and not vectors_match:
                    raise ValueError("stored embedding vectors differ; ingest into a new epoch")
                if sealed:
                    if content_matches and vectors_match and version_matches:
                        return
                    if not existing_records and not points:
                        return
                    raise ValueError(f"knowledge epoch {doc_version_epoch!r} is sealed; write to a new epoch")
                if points:
                    self._ensure_epoch_embedding_version(doc_version_epoch, doc_id)

                existing_ids = [record.id for record in existing_records]
                if existing_ids:
                    # Staging snapshots are not queryable as the active epoch; archive first to fail closed.
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
                payload.get("chunk_index"),
                payload.get("content"),
                payload.get("role_mask"),
                payload.get("dept_mask"),
                payload.get("status"),
                # Provenance is part of the document's contract, so a changed
                # trust decision is a changed document. Keeping it in the
                # signature makes an approval recorded into an already-sealed
                # epoch fail closed instead of being silently ignored.
                persisted_trust_class(payload.get("provenance")),
            )

        return sorted((signature(payload) for payload in payloads), key=repr)

    @staticmethod
    def _document_vectors_match(records, points) -> bool:
        record_vectors = {record.payload.get("chunk_index"): record.vector for record in records if record.payload}
        point_vectors = {point.payload.get("chunk_index"): point.vector for point in points if point.payload}
        if record_vectors.keys() != point_vectors.keys():
            return False
        for chunk_index, stored in record_vectors.items():
            current = point_vectors[chunk_index]
            if isinstance(stored, dict) or isinstance(current, dict) or len(stored) != len(current):
                return False
            # Qdrant normalizes vectors when storing points in a cosine collection.
            # Compare their cosine-equivalent unit vectors so unnormalized model
            # output remains idempotent after a round trip through Qdrant.
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

    def _epoch_seal_id(self, epoch: str) -> str:
        return str(uuid.uuid5(_POINT_NAMESPACE, f"epoch-seal:{epoch}"))

    def _epoch_embedding_version_id(self, epoch: str) -> str:
        return str(uuid.uuid5(_POINT_NAMESPACE, f"epoch-embedding-version:{epoch}"))

    def _ensure_epoch_embedding_version(self, epoch: str, replacing_doc_id: str) -> None:
        """Pin each epoch to one embedding revision before any document is written."""
        from qdrant_client.http.models import (
            FieldCondition,
            Filter,
            IsEmptyCondition,
            MatchValue,
            PayloadField,
            PointStruct,
        )

        with _local_epoch_embedding_lock(epoch):
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
                raise ValueError("embedding version changed within epoch; ingest into a new epoch")
            if pinned_version == self.embedding_version:
                return

            epoch_conditions = [FieldCondition(key="doc_version_epoch", match=MatchValue(value=epoch))]
            if epoch == "default":
                epoch_conditions.append(IsEmptyCondition(is_empty=PayloadField(key="doc_version_epoch")))
            existing_versions = set()
            epoch_records = []
            offset = None
            epoch_filter = Filter(
                must=[
                    FieldCondition(key="status", match=MatchValue(value="active")),
                    Filter(should=epoch_conditions),
                ],
            )
            while True:
                records, offset = self.client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=epoch_filter,
                    limit=256,
                    with_payload=["embedding_version", "doc_id"],
                    with_vectors=False,
                    offset=offset,
                )
                epoch_records.extend(records)
                existing_versions.update((record.payload or {}).get("embedding_version") for record in records)
                if offset is None:
                    break

            unknown_legacy_records = [
                record for record in epoch_records if (record.payload or {}).get("embedding_version") is None
            ]
            if any(
                epoch != "default" or (record.payload or {}).get("doc_id") != replacing_doc_id
                for record in unknown_legacy_records
            ):
                raise ValueError("embedding version is missing or mixed within epoch; ingest into a new epoch")
            known_versions = existing_versions - {None}
            if known_versions and known_versions != {self.embedding_version}:
                raise ValueError("embedding version is missing or mixed within epoch; ingest into a new epoch")
            if unknown_legacy_records:
                self.client.set_payload(
                    collection_name=self.collection_name,
                    payload={"status": "archived"},
                    points=[record.id for record in unknown_legacy_records],
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
        """Pin an epoch to this writer's embedding version without writing a document."""
        _validate_epoch(epoch)
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
        """Seal a staged epoch before activating it through application configuration."""
        _validate_epoch(epoch)
        with _local_epoch_lock(epoch, shared=False):
            self.ensure_collection()
            from qdrant_client.http.models import PointStruct

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

    def _build_points(self, chunks: list[TextChunk], vectors: list[list[float]]):
        from qdrant_client.http.models import PointStruct

        if len(chunks) != len(vectors):
            raise ValueError("chunk and vector counts differ")
        _validate_vectors(vectors, len(chunks), self.dimension)
        points = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            _validate_permissions(chunk.role_mask, chunk.dept_mask)
            _validate_epoch(chunk.doc_version_epoch)
            if chunk.status != "active":
                raise ValueError("Phase 1 only writes active text chunks")
            # Ingestion trust gate: a point is only written with usable, persisted
            # provenance. A rejected source is refused outright; a quarantined
            # (unapproved) source may be staged because a staging epoch is not
            # queryable, and offline/validator.py refuses to seal it.
            enforce_writable_provenance(
                chunk.provenance,
                label=f"text chunk {chunk.chunk_id!r}",
                source_id=chunk.doc_id,
                epoch=chunk.doc_version_epoch,
            )
            payload = {
                "doc_id": chunk.doc_id,
                "chunk_id": chunk.chunk_id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.text,
                "source_path": chunk.source_path,
                "content_hash": chunk.content_hash,
                "doc_type": "text",
                "embedding_type": "bge",
                "embedding_version": self.embedding_version,
                "role_mask": chunk.role_mask,
                "dept_mask": chunk.dept_mask,
                "status": chunk.status,
                "doc_version_epoch": chunk.doc_version_epoch,
                "metadata": chunk.metadata,
                "provenance": dict(chunk.provenance),
            }
            points.append(PointStruct(id=_versioned_point_id(chunk), vector=vector, payload=payload))
        return points


class TextIngestionService:
    """Orchestrate processor, injected embedder, and writer without import-time model loading."""

    def __init__(self, processor: DocumentProcessor, embedder: TextEmbedder, writer: QdrantTextWriter):
        self.processor = processor
        self.embedder = embedder
        self.writer = writer
        self.writer.embedding_version = getattr(
            embedder,
            "embedding_version",
            f"{type(embedder).__module__}.{type(embedder).__qualname__}",
        )

    def ingest(
        self,
        source: str | Path,
        *,
        role_mask: int,
        dept_mask: int,
        doc_version_epoch: str,
        source_id: str | None = None,
        provenance: dict | None = None,
    ) -> list[TextChunk]:
        _, doc_id = self.processor.document_identity(source, source_id)
        chunks = self.processor.process(
            source,
            role_mask=role_mask,
            dept_mask=dept_mask,
            doc_version_epoch=doc_version_epoch,
            source_id=source_id,
            provenance=provenance,
        )
        vectors = self.embedder.embed_texts([chunk.text for chunk in chunks])
        self.writer.replace_document(doc_id, doc_version_epoch, chunks, vectors)
        return chunks


def configured_text_ingestion_service():
    """Create production adapters from canonical project configuration."""
    from qdrant_client import QdrantClient

    from common.config import get_config_dict

    config = get_config_dict()
    embedding = config["embedding"]["text"]
    collection = embedding["collection"]
    qdrant = config["qdrant"]
    client = QdrantClient(
        host=qdrant["host"],
        port=qdrant["port"],
        grpc_port=qdrant.get("grpc_port"),
        prefer_grpc=False,
    )
    knowledge_base = config.get("knowledge_base", {})
    chunk_size = int(knowledge_base.get("chunk_size", 500))
    overlap_ratio = float(knowledge_base.get("chunk_overlap_ratio", 0.1))
    dimension = int(embedding["dimension"])
    embedding_batch_size = int(knowledge_base.get("embedding_batch_size", 32))
    max_document_bytes = int(knowledge_base.get("max_document_bytes", 131_072))
    max_chunks = int(knowledge_base.get("max_chunks", 256))
    writer = QdrantTextWriter(client, collection, dimension)
    writer.seal_epoch(config.get("knowledge_version_epoch", "default"))
    return TextIngestionService(
        DocumentProcessor(
            chunk_size,
            int(chunk_size * overlap_ratio),
            max_document_bytes,
            max_chunks,
            knowledge_base.get("data_dir", "./data"),
        ),
        BGETextEmbedder(
            embedding["model_path"],
            dimension,
            embedding_batch_size,
            model_revision=embedding.get("model_revision"),
        ),
        writer,
    )
