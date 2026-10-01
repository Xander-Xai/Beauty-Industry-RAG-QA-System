"""Epoch snapshot builder for full and incremental offline ingestion.

The builder owns the cross-store sequencing: target epoch stays invisible until
it is validated and sealed. If any writer fails, the staging epoch is left
unsealed so an operator can inspect or retry it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from offline.carry_forward import carry_forward_es, carry_forward_images, carry_forward_text
from offline.document_processor import (
    SUPPORTED_DOCUMENT_EXTENSIONS,
    DocumentBlock,
    DocumentProcessor,
    ProcessedDocument,
)
from offline.image_processor import SUPPORTED_IMAGE_EXTENSIONS
from offline.state_store import ChangeSet, SourceState
from offline.validator import SnapshotValidator, ValidationReport
from offline.vectorizer import Vectorizer


def classify_source(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in SUPPORTED_DOCUMENT_EXTENSIONS:
        return "text"
    if suffix in SUPPORTED_IMAGE_EXTENSIONS:
        return "image"
    raise ValueError(f"unsupported source type {suffix or '<none>'}")


@dataclass(frozen=True)
class IngestionSource:
    source_id: str
    path: str
    document_type: str
    role_mask: int
    dept_mask: int
    relative_path: str = ""


@dataclass
class BuildResult:
    epoch: str
    documents_processed: int = 0
    chunks_written: int = 0
    images_written: int = 0
    carried_forward: int = 0
    changes: ChangeSet | None = None
    validation: ValidationReport | None = None
    sealed: bool = False
    failed_sources: list[str] = field(default_factory=list)


class SnapshotBuilder:
    def __init__(
        self,
        *,
        processor: DocumentProcessor,
        text_embedder,
        image_processor,
        text_writer,
        image_writer,
        state_store,
        es_writer=None,
    ):
        self.processor = processor
        self.text_embedder = text_embedder
        self.image_processor = image_processor
        self.text_writer = text_writer
        self.image_writer = image_writer
        self.state_store = state_store
        self.es_writer = es_writer
        self.vectorizer = Vectorizer(text_embedder, getattr(image_processor, "image_embedder", None))
        self.text_writer.embedding_version = self.vectorizer.text_embedding_version
        self.image_writer.embedding_version = self.vectorizer.image_embedding_version

    # -- helpers ----------------------------------------------------------

    def _doc_id(self, source: IngestionSource) -> str:
        if source.document_type == "image":
            return hashlib.sha256(source.source_id.encode()).hexdigest()
        _, doc_id = self.processor.document_identity(source.path, source.source_id)
        return doc_id

    @staticmethod
    def _file_hash(path: str | Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        return digest.hexdigest()

    def _current_metadata(self, sources: list[IngestionSource]) -> dict[str, dict]:
        metadata = {}
        for source in sources:
            stat = Path(source.path).stat()
            metadata[source.source_id] = {
                "content_hash": self._file_hash(source.path),
                "file_size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "document_type": source.document_type,
                "role_mask": source.role_mask,
                "dept_mask": source.dept_mask,
            }
        return metadata

    def _text_embedding_versions(self) -> set[str]:
        version = self.vectorizer.text_embedding_version
        return {version} if version and version != "unspecified-v1" else set()

    def _image_embedding_versions(self) -> set[str]:
        version = self.vectorizer.image_embedding_version
        return {version} if version and version != "unspecified-image-v1" else set()

    def _validator(self, expected_doc_ids: set[str] | None) -> SnapshotValidator:
        return SnapshotValidator(
            text_client=self.text_writer.client,
            text_collection=self.text_writer.collection_name,
            image_client=self.image_writer.client,
            image_collection=self.image_writer.collection_name,
            es_writer=self.es_writer,
            allowed_text_versions=self._text_embedding_versions(),
            allowed_image_versions=self._image_embedding_versions(),
            expected_doc_ids=expected_doc_ids,
        )

    # -- ingestion --------------------------------------------------------

    def _ocr_text_chunks(self, doc_id: str, source: IngestionSource, record, epoch: str):
        if not record.ocr_main_text.strip():
            return []
        processed = ProcessedDocument(
            doc_id=doc_id,
            source_id=source.source_id,
            source_path=source.path,
            document_type="ocr",
            content_hash=hashlib.sha256(record.ocr_main_text.encode()).hexdigest(),
            blocks=[
                DocumentBlock(
                    block_type="ocr",
                    text=record.ocr_main_text,
                    order=0,
                    block_identity=f"ocr:{record.image_index}",
                    metadata={"format": "ocr", "page_number": record.page_number},
                )
            ],
            images=[],
            requires_ocr=False,
            metadata={"source_path": source.path, "format": "ocr"},
        )
        return self.processor.build_chunks(
            processed,
            role_mask=source.role_mask,
            dept_mask=source.dept_mask,
            doc_version_epoch=epoch,
        )

    def ingest_source(
        self,
        source: IngestionSource,
        epoch: str,
        *,
        state_sink: list[SourceState] | None = None,
    ) -> tuple[int, int]:
        """Ingest one source and return (chunks_written, images_written).

        State is upserted immediately unless ``state_sink`` is provided, in
        which case the caller stages it and commits only after the whole
        snapshot succeeds.
        """
        doc_id = self._doc_id(source)
        if source.document_type == "image":
            record = self.image_processor.process_standalone_image(
                source.path,
                role_mask=source.role_mask,
                dept_mask=source.dept_mask,
                doc_version_epoch=epoch,
                doc_id=doc_id,
            )
            chunks = self._ocr_text_chunks(doc_id, source, record, epoch)
            images = [record]
        else:
            processed = self.processor.process(source.path, source_id=source.source_id)
            chunks = self.processor.build_chunks(
                processed,
                role_mask=source.role_mask,
                dept_mask=source.dept_mask,
                doc_version_epoch=epoch,
            )
            images = [
                self.image_processor.process_image(
                    image=image,
                    doc_id=doc_id,
                    role_mask=source.role_mask,
                    dept_mask=source.dept_mask,
                    doc_version_epoch=epoch,
                    source_path=source.path,
                )
                for image in processed.images
            ]
            for record in images:
                chunks.extend(self._ocr_text_chunks(doc_id, source, record, epoch))

        self._write_document(source, doc_id, epoch, chunks, images)
        stat = Path(source.path).stat()
        state = SourceState(
            source_id=source.source_id,
            relative_path=source.relative_path or source.source_id,
            file_size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            content_hash=self._file_hash(source.path),
            document_type=source.document_type,
            last_successful_epoch=epoch,
            last_processed_at=datetime.now(timezone.utc).isoformat(),
            status="active",
            role_mask=source.role_mask,
            dept_mask=source.dept_mask,
        )
        if state_sink is None:
            self.state_store.upsert(state)
        else:
            state_sink.append(state)
        return len(chunks), len(images)

    def _write_document(self, source, doc_id, epoch, chunks, images) -> None:
        vectors = self.vectorizer.embed_texts([chunk.text for chunk in chunks]) if chunks else []
        self.text_writer.replace_document(doc_id, epoch, chunks, vectors)
        # Always replace, even with zero images, so a document that changed from
        # image-bearing to text-only has its stale image points removed.
        self.image_writer.replace_document(doc_id, epoch, images, [image.embedding for image in images])
        if self.es_writer is not None:
            documents = []
            for chunk in chunks:
                document = self.es_writer.build_document(chunk)
                document["embedding_version"] = self.text_writer.embedding_version
                documents.append(document)
            self.es_writer.replace_document(doc_id, epoch, documents)

    # -- public workflows -------------------------------------------------

    def build_full(
        self, sources: list[IngestionSource], epoch: str, *, seal: bool = False, validate: bool = True
    ) -> BuildResult:
        result = BuildResult(epoch=epoch)
        expected = set()
        pending_states: list[SourceState] = []
        for source in sources:
            try:
                chunks, images = self.ingest_source(source, epoch, state_sink=pending_states)
            except Exception:
                result.failed_sources.append(source.source_id)
                raise
            expected.add(self._doc_id(source))
            result.documents_processed += 1
            result.chunks_written += chunks
            result.images_written += images
        if validate:
            result.validation = self._validator(expected).validate_or_raise(epoch)
        if seal:
            self.seal(epoch)
            result.sealed = True
        # Commit source state only after the snapshot (and optional seal)
        # succeeded, so a failed build cannot mark sources as processed.
        for state in pending_states:
            self.state_store.upsert(state)
        return result

    def build_incremental(
        self,
        sources: list[IngestionSource],
        from_epoch: str,
        to_epoch: str,
        *,
        seal: bool = False,
        validate: bool = True,
    ) -> BuildResult:
        result = BuildResult(epoch=to_epoch)
        metadata = self._current_metadata(sources)
        changes = self.state_store.diff(metadata)
        result.changes = changes

        source_by_id = {source.source_id: source for source in sources}
        unchanged_doc_ids = {self._doc_id(source_by_id[source_id]) for source_id in changes.unchanged}
        if unchanged_doc_ids:
            carried = carry_forward_text(
                self.text_writer,
                from_epoch,
                to_epoch,
                allowed_versions=self._text_embedding_versions(),
                doc_ids=unchanged_doc_ids,
            )
            carried += carry_forward_images(
                self.image_writer,
                from_epoch,
                to_epoch,
                allowed_versions=self._image_embedding_versions(),
                doc_ids=unchanged_doc_ids,
            )
            if self.es_writer is not None:
                carried += carry_forward_es(self.es_writer, from_epoch, to_epoch, doc_ids=unchanged_doc_ids)
            result.carried_forward = carried

        pending_states: list[SourceState] = []
        for source_id in changes.new + changes.modified:
            source = source_by_id[source_id]
            chunks, images = self.ingest_source(source, to_epoch, state_sink=pending_states)
            result.documents_processed += 1
            result.chunks_written += chunks
            result.images_written += images

        for source_id in changes.deleted:
            source = source_by_id.get(source_id)
            if source is not None:
                doc_id = self._doc_id(source)
                self.text_writer.replace_document(doc_id, to_epoch, [], [])
                self.image_writer.replace_document(doc_id, to_epoch, [], [])
                if self.es_writer is not None:
                    self.es_writer.delete_document(doc_id, to_epoch)

        if validate:
            expected = {self._doc_id(source) for source in sources}
            result.validation = self._validator(expected).validate_or_raise(to_epoch)
        if seal:
            self.seal(to_epoch)
            result.sealed = True
        # Commit state only after the snapshot succeeded.
        for state in pending_states:
            self.state_store.upsert(state)
        for source_id in changes.deleted:
            self.state_store.mark_deleted(source_id)
        return result

    def seal(self, epoch: str) -> None:
        self.text_writer.seal_epoch(epoch)
        self.image_writer.seal_epoch(epoch)

    def validator(self, expected_doc_ids: set[str] | None = None) -> SnapshotValidator:
        return self._validator(expected_doc_ids)

    def seal_epoch(self, epoch: str, *, validate: bool = True, expected_doc_ids: set[str] | None = None) -> None:
        if validate:
            self._validator(expected_doc_ids).validate_or_raise(epoch)
        self.seal(epoch)


def configured_document_processor(config: dict) -> DocumentProcessor:
    kb = config.get("knowledge_base", {})
    chunk_size = int(kb.get("chunk_size", 500))
    chunk_overlap = int(chunk_size * float(kb.get("chunk_overlap_ratio", 0.1)))
    return DocumentProcessor(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        max_document_bytes=int(kb.get("max_document_bytes", 131_072)),
        max_chunks=int(kb.get("max_chunks", 256)),
        source_root=kb.get("data_dir", "./data"),
        max_binary_document_bytes=int(kb.get("max_binary_document_bytes", 67_108_864)),
        ocr_min_text_chars=int(kb.get("ocr_min_text_chars", 20)),
        pdf_render_dpi=int(kb.get("pdf_render_dpi", 150)),
        max_images_per_document=int(kb.get("max_images_per_document", 100)),
        xlsx_rows_per_block=int(kb.get("xlsx_rows_per_block", 50)),
    )


def configured_snapshot_builder(config: dict | None = None) -> SnapshotBuilder:
    """Build production adapters from canonical project configuration."""
    from qdrant_client import QdrantClient

    from common.config import get_config_dict
    from offline.embeddings import BGETextEmbedder, CLIPImageEmbedder
    from offline.image_processor import ImageProcessor, PaddleOCRProvider
    from offline.qdrant_writer import QdrantImageWriter
    from offline.state_store import StateStore
    from offline.text_ingestion import QdrantTextWriter

    config = config or get_config_dict()
    kb = config.get("knowledge_base", {})
    embedding = config["embedding"]
    text_config = embedding["text"]
    image_config = embedding["image_clip"]
    qdrant_config = config["qdrant"]

    client = QdrantClient(
        host=qdrant_config["host"],
        port=qdrant_config["port"],
        grpc_port=qdrant_config.get("grpc_port"),
        prefer_grpc=False,
    )

    text_embedder = BGETextEmbedder(
        text_config["model_path"],
        int(text_config["dimension"]),
        int(kb.get("embedding_batch_size", 32)),
        model_revision=text_config.get("model_revision"),
    )
    image_embedder = CLIPImageEmbedder(
        image_config["model_path"],
        int(image_config.get("dimension", 512)),
        int(kb.get("image_batch_size", 16)),
        model_revision=image_config.get("model_revision"),
    )
    ocr_config = kb.get("ocr", {})
    ocr_provider = PaddleOCRProvider(
        language=ocr_config.get("language", "ch"),
    )
    image_processor = ImageProcessor(
        ocr_provider,
        image_embedder,
        visual_weight_repeat=int(ocr_config.get("visual_weight_repeat", 3)),
    )

    es_writer = _configured_es_writer(config)
    state_db_path = kb.get("state_db_path") or str(
        Path(__file__).resolve().parents[1] / "data" / "offline_state.sqlite3"
    )
    return SnapshotBuilder(
        processor=configured_document_processor(config),
        text_embedder=text_embedder,
        image_processor=image_processor,
        text_writer=QdrantTextWriter(client, text_config["collection"], int(text_config["dimension"])),
        image_writer=QdrantImageWriter(client, image_config["collection"], int(image_config.get("dimension", 512))),
        state_store=StateStore(state_db_path),
        es_writer=es_writer,
    )


def _configured_es_writer(config: dict):
    es_config = config.get("elasticsearch", {})
    if not es_config.get("enabled", False):
        return None
    from elasticsearch import Elasticsearch

    from offline.elasticsearch_writer import ElasticsearchWriter

    kwargs = {"hosts": [es_config.get("host", "http://localhost:9200")]}
    if es_config.get("username"):
        kwargs["basic_auth"] = (es_config["username"], es_config.get("password", ""))
    return ElasticsearchWriter(Elasticsearch(**kwargs), es_config.get("index", "cosmetics_docs"))
