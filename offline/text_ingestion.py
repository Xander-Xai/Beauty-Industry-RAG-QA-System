"""A deterministic TXT to Qdrant ingestion path for the Phase 1 slice."""

from __future__ import annotations

import hashlib
import math
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_UINT32_MAX = 0xFFFFFFFF
_EPOCH_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_POINT_NAMESPACE = uuid.UUID("74f7d957-77e6-4cac-9af6-a5f09c081215")


@contextmanager
def _local_replacement_lock(doc_id: str, epoch: str):
    """Serialize same-host CLI writers with a non-evictable POSIX file lock."""
    import fcntl

    owner_id = os.getuid() if hasattr(os, "getuid") else os.getpid()
    configured_lock_dir = os.environ.get("OFFLINE_INGESTION_LOCK_DIR")
    lock_dir = (
        Path(configured_lock_dir)
        if configured_lock_dir
        else Path(tempfile.gettempdir()) / f"beauty-rag-text-ingestion-{owner_id}"
    )
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory_stat = lock_dir.stat()
    if hasattr(os, "getuid") and directory_stat.st_uid != owner_id:
        raise PermissionError(f"ingestion lock directory is owned by another user: {lock_dir}")
    if directory_stat.st_mode & 0o077:
        raise PermissionError(f"ingestion lock directory must not be accessible by other users: {lock_dir}")

    lock_key = hashlib.sha256(f"{doc_id}:{epoch}".encode()).hexdigest()
    lock_path = lock_dir / f"{lock_key}.lock"
    file_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(file_descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(file_descriptor, fcntl.LOCK_UN)
        os.close(file_descriptor)


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
                )
            )
            if offset + self.chunk_size >= len(text):
                break
        return chunks


class BGETextEmbedder:
    """Production adapter using the same BGE tokenizer and mean pooling as online queries."""

    def __init__(self, model_name_or_path: str, dimension: int = 768, batch_size: int = 32):
        if not model_name_or_path:
            raise ValueError("a configured BGE model name or path is required")
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")
        self.model_name_or_path = model_name_or_path
        self.dimension = dimension
        self.batch_size = batch_size
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
    ):
        if not collection_name or type(dimension) is not int or dimension <= 0:
            raise ValueError("a collection name and positive vector dimension are required")
        self.client = client
        self.collection_name = collection_name
        self.dimension = dimension
        self.replacement_lock = replacement_lock

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
        """Write an immutable document snapshot; changes require a new epoch."""
        _validate_epoch(doc_version_epoch)
        points = self._build_points(chunks, vectors)
        if any(chunk.doc_id != doc_id or chunk.doc_version_epoch != doc_version_epoch for chunk in chunks):
            raise ValueError("replacement chunks must match the requested document and epoch")

        lock_context = (self.replacement_lock or _local_replacement_lock)(doc_id, doc_version_epoch)
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
                must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))],
                should=epoch_conditions,
            )
            while True:
                records, offset = self.client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=source_filter,
                    limit=256,
                    with_payload=["chunk_index", "content", "role_mask", "dept_mask", "status"],
                    with_vectors=False,
                    offset=offset,
                )
                existing_records.extend(records)
                if offset is None:
                    break

            if existing_records:

                def signature(payload):
                    return (
                        payload.get("chunk_index"),
                        payload.get("content"),
                        payload.get("role_mask"),
                        payload.get("dept_mask"),
                        payload.get("status"),
                    )

                existing_signature = sorted((signature(record.payload or {}) for record in existing_records), key=repr)
                desired_signature = sorted((signature(point.payload or {}) for point in points), key=repr)
                if existing_signature == desired_signature and len(existing_records) == len(points):
                    return
                raise ValueError(
                    "knowledge epochs are immutable: changed content, permissions, or deletion requires a new epoch"
                )

            if points:
                self.client.upsert(collection_name=self.collection_name, points=points, wait=True)

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
            payload = {
                "doc_id": chunk.doc_id,
                "chunk_id": chunk.chunk_id,
                "chunk_index": chunk.chunk_index,
                "content": chunk.text,
                "source_path": chunk.source_path,
                "content_hash": chunk.content_hash,
                "doc_type": "text",
                "embedding_type": "bge",
                "role_mask": chunk.role_mask,
                "dept_mask": chunk.dept_mask,
                "status": chunk.status,
                "doc_version_epoch": chunk.doc_version_epoch,
                "metadata": chunk.metadata,
            }
            points.append(PointStruct(id=_versioned_point_id(chunk), vector=vector, payload=payload))
        return points


class TextIngestionService:
    """Orchestrate processor, injected embedder, and writer without import-time model loading."""

    def __init__(self, processor: DocumentProcessor, embedder: TextEmbedder, writer: QdrantTextWriter):
        self.processor = processor
        self.embedder = embedder
        self.writer = writer

    def ingest(
        self,
        source: str | Path,
        *,
        role_mask: int,
        dept_mask: int,
        doc_version_epoch: str,
        source_id: str | None = None,
    ) -> list[TextChunk]:
        _, doc_id = self.processor.document_identity(source, source_id)
        chunks = self.processor.process(
            source,
            role_mask=role_mask,
            dept_mask=dept_mask,
            doc_version_epoch=doc_version_epoch,
            source_id=source_id,
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
    return TextIngestionService(
        DocumentProcessor(
            chunk_size,
            int(chunk_size * overlap_ratio),
            max_document_bytes,
            max_chunks,
            knowledge_base.get("data_dir", "./data"),
        ),
        BGETextEmbedder(embedding["model_path"], dimension, embedding_batch_size),
        QdrantTextWriter(client, collection, dimension),
    )
