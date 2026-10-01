"""A deterministic TXT to Qdrant ingestion path for the Phase 1 slice."""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_UINT32_MAX = 0xFFFFFFFF
_EPOCH_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_POINT_NAMESPACE = uuid.UUID("74f7d957-77e6-4cac-9af6-a5f09c081215")


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


class DocumentProcessor:
    """Read UTF-8 TXT documents and split them into deterministic character windows."""

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        if type(chunk_size) is not int or chunk_size <= 0:
            raise ValueError("chunk_size must be a positive integer")
        if type(chunk_overlap) is not int or not 0 <= chunk_overlap < chunk_size:
            raise ValueError("chunk_overlap must be an integer in [0, chunk_size)")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    @staticmethod
    def document_identity(source: str | Path) -> tuple[str, str]:
        source_path = str(Path(source).resolve())
        doc_id = hashlib.sha256(source_path.encode("utf-8")).hexdigest()
        return source_path, doc_id

    def process(
        self,
        source: str | Path,
        *,
        role_mask: int,
        dept_mask: int,
        doc_version_epoch: str,
    ) -> list[TextChunk]:
        _validate_permissions(role_mask, dept_mask)
        _validate_epoch(doc_version_epoch)
        path = Path(source)
        if path.suffix.lower() != ".txt":
            raise ValueError(f"unsupported document type {path.suffix or '<none>'}; only .txt is supported")
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise ValueError(f"cannot read UTF-8 text document {path}: {exc}") from exc

        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            return []
        source_path, doc_id = self.document_identity(path)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        chunks: list[TextChunk] = []
        step = self.chunk_size - self.chunk_overlap
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
        from qdrant_client.http.models import Distance, VectorParams

        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=self.dimension, distance=Distance.COSINE),
            )
            return
        info = self.client.get_collection(self.collection_name)
        vectors = info.config.params.vectors
        if not isinstance(vectors, dict) and vectors.size != self.dimension:
            raise ValueError(f"collection dimension does not match configured dimension {self.dimension}")
        if isinstance(vectors, dict):
            raise ValueError("named Qdrant vectors are not supported by the text ingestion slice")

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
        """Replace one source/epoch safely; stale points become inactive before removal."""
        _validate_epoch(doc_version_epoch)
        points = self._build_points(chunks, vectors)
        if any(chunk.doc_id != doc_id or chunk.doc_version_epoch != doc_version_epoch for chunk in chunks):
            raise ValueError("replacement chunks must match the requested document and epoch")

        lock_context = self.replacement_lock(doc_id, doc_version_epoch) if self.replacement_lock else nullcontext()
        with lock_context:
            self.ensure_collection()
            from qdrant_client.http.models import FieldCondition, Filter, MatchValue

            existing_ids = []
            offset = None
            source_filter = Filter(
                must=[
                    FieldCondition(key="doc_id", match=MatchValue(value=doc_id)),
                    FieldCondition(key="doc_version_epoch", match=MatchValue(value=doc_version_epoch)),
                ]
            )
            while True:
                records, offset = self.client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=source_filter,
                    limit=256,
                    with_payload=False,
                    with_vectors=False,
                    offset=offset,
                )
                existing_ids.extend(record.id for record in records)
                if offset is None:
                    break

            if existing_ids:
                # Active-only online queries fail closed if subsequent upsert/delete fails.
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
            points.append(PointStruct(id=chunk.chunk_id, vector=vector, payload=payload))
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
    ) -> list[TextChunk]:
        _, doc_id = self.processor.document_identity(source)
        chunks = self.processor.process(
            source,
            role_mask=role_mask,
            dept_mask=dept_mask,
            doc_version_epoch=doc_version_epoch,
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
    redis_config = config["redis"]["cache"]
    from redis import Redis

    redis_client = Redis(
        host=redis_config["host"],
        port=redis_config["port"],
        db=redis_config.get("db", 0),
        decode_responses=True,
    )

    def replacement_lock(doc_id: str, epoch: str):
        return redis_client.lock(
            f"offline-text-ingestion:{doc_id}:{epoch}",
            timeout=3600,
            blocking_timeout=300,
        )

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
    return TextIngestionService(
        DocumentProcessor(chunk_size, int(chunk_size * overlap_ratio)),
        BGETextEmbedder(embedding["model_path"], dimension, embedding_batch_size),
        QdrantTextWriter(client, collection, dimension, replacement_lock=replacement_lock),
    )
