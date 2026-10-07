"""Epoch snapshot builder for full and incremental offline ingestion.

The builder owns the cross-store sequencing: target epoch stays invisible until
it is validated and sealed. If any writer fails, the staging epoch is left
unsealed so an operator can inspect or retry it.
"""

from __future__ import annotations

import hashlib
import uuid
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
from offline.source_trust import (
    APPROVAL_NOT_REQUIRED,
    APPROVAL_REJECTED,
    CLI_TRUST_ACTOR_ID,
    PROVENANCE_SCHEMA_VERSION,
    TRUST_MANAGED_INTERNAL,
    RejectedSourceError,
    SourceTrustRecord,
    TrustRegistry,
    audit_quarantine,
    file_content_hash,
)
from offline.state_store import ChangeSet, SourceState
from offline.validator import SnapshotValidator, ValidationReport, scroll_active_points
from offline.vectorizer import Vectorizer

#: Point id namespace for the per-epoch ingestion trust manifest. Reuses the
#: shared chunk namespace so every deterministic point id in this repository
#: derives from one UUID5 namespace, exactly as the seal and embedding-version
#: markers already do.
_TRUST_MANIFEST_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "offline/snapshot_builder/trust-manifest")


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
    #: Provenance *claim*, resolved from managed configuration by
    #: ``offline.source_discovery.discover_sources``. The default is the explicit
    #: legacy compatibility policy (``managed_record``): every caller that
    #: existed before the trust contract ingested from the managed data root, so
    #: declaring nothing declares managed internal content. It does not bypass
    #: anything — the resolved record is still persisted and still gated at seal.
    source_trust: str = TRUST_MANAGED_INTERNAL
    #: Declared approval status. Only ever set by an explicit operator decision;
    #: the ingestion path always re-derives the effective status from the
    #: approval ledger, so this cannot be used to smuggle in an approval.
    approval_status: str = APPROVAL_NOT_REQUIRED


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
    #: Sources that were staged but may not be activated without a review
    #: decision. Reported so an operator can see the quarantine queue from the
    #: build result instead of only from the validator error text.
    quarantined_sources: list[str] = field(default_factory=list)


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
        trust_registry=None,
    ):
        self.processor = processor
        self.text_embedder = text_embedder
        self.image_processor = image_processor
        self.text_writer = text_writer
        self.image_writer = image_writer
        self.state_store = state_store
        self.es_writer = es_writer
        #: Optional approval ledger. ``None`` means there is no approval record
        #: to consult, which resolves every untrusted source to PENDING_REVIEW —
        #: it never resolves to an approval, so omitting the ledger quarantines
        #: rather than admits.
        self.trust_registry = trust_registry if trust_registry is not None else TrustRegistry(":memory:")
        self.vectorizer = Vectorizer(text_embedder, getattr(image_processor, "image_embedder", None))
        self.text_writer.embedding_version = self.vectorizer.text_embedding_version
        self.image_writer.embedding_version = self.vectorizer.image_embedding_version

    # -- ingestion trust --------------------------------------------------

    def resolve_source_trust(self, source: IngestionSource, content_hash: str) -> SourceTrustRecord:
        """Return the canonical provenance record for one source and its bytes.

        This is the only place a source acquires provenance, and it is a pure
        function of the configured trust claim, the approval ledger and the
        content hash: the same three inputs always yield the same record, and no
        ingestion branch can promote a source on its own. Resolving is not
        admitting — refusing a rejected source is :meth:`ingest_source`'s job, so
        change detection still sees the source and reports it as a failure.
        """
        return self.trust_registry.resolve(
            source.source_id,
            declared_trust=source.source_trust,
            content_hash=content_hash,
        )

    def _refuse_rejected(self, source: IngestionSource, record: SourceTrustRecord) -> None:
        """Fail closed on a source a reviewer rejected, and record the refusal."""
        reason = f"{source.source_id} was rejected in review and is not ingested at all"
        audit_quarantine(
            source_id=source.source_id,
            trust_class=record.trust_class,
            reason=reason,
            actor_id=CLI_TRUST_ACTOR_ID,
        )
        raise RejectedSourceError(reason)

    # -- helpers ----------------------------------------------------------

    def _doc_id(self, source: IngestionSource) -> str:
        if source.document_type == "image":
            return hashlib.sha256(source.source_id.encode()).hexdigest()
        _, doc_id = self.processor.document_identity(source.path, source.source_id)
        return doc_id

    def _current_metadata(self, sources: list[IngestionSource]) -> dict[str, dict]:
        metadata = {}
        for source in sources:
            stat = Path(source.path).stat()
            content_hash = file_content_hash(source.path)
            record = self.resolve_source_trust(source, content_hash)
            metadata[source.source_id] = {
                "content_hash": content_hash,
                "file_size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "document_type": source.document_type,
                "role_mask": source.role_mask,
                "dept_mask": source.dept_mask,
                # Persisted alongside the permission masks so a recorded approval
                # or rejection forces reprocessing. Without this, approving a
                # quarantined source would leave the stored points holding the
                # old PENDING_REVIEW provenance and the seal would keep failing
                # for a decision that was in fact made.
                "source_trust": record.source_trust,
                "approval_status": record.approval_status,
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

    def _ocr_text_chunks(self, doc_id: str, source: IngestionSource, record, epoch: str, provenance: dict):
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
            provenance=provenance,
        )

    def ingest_source(
        self,
        source: IngestionSource,
        epoch: str,
        *,
        state_sink: list[SourceState] | None = None,
        trust_sink: list[SourceTrustRecord] | None = None,
    ) -> tuple[int, int]:
        """Ingest one source and return (chunks_written, images_written).

        State is upserted immediately unless ``state_sink`` is provided, in
        which case the caller stages it and commits only after the whole
        snapshot succeeded. ``trust_sink`` follows the same pattern for the
        resolved provenance, so a build can report its quarantine queue without
        re-hashing the source.
        """
        doc_id = self._doc_id(source)
        content_hash = file_content_hash(source.path)
        trust_record = self.resolve_source_trust(source, content_hash)
        if trust_record.approval_status == APPROVAL_REJECTED:
            self._refuse_rejected(source, trust_record)
        provenance = trust_record.to_payload()

        if source.document_type == "image":
            record = self.image_processor.process_standalone_image(
                source.path,
                role_mask=source.role_mask,
                dept_mask=source.dept_mask,
                doc_version_epoch=epoch,
                doc_id=doc_id,
                provenance=provenance,
            )
            chunks = self._ocr_text_chunks(doc_id, source, record, epoch, provenance)
            images = [record]
        else:
            processed = self.processor.process(source.path, source_id=source.source_id)
            chunks = self.processor.build_chunks(
                processed,
                role_mask=source.role_mask,
                dept_mask=source.dept_mask,
                doc_version_epoch=epoch,
                provenance=provenance,
            )
            images = [
                self.image_processor.process_image(
                    image=image,
                    doc_id=doc_id,
                    role_mask=source.role_mask,
                    dept_mask=source.dept_mask,
                    doc_version_epoch=epoch,
                    source_path=source.path,
                    provenance=provenance,
                )
                for image in processed.images
            ]
            for record in images:
                chunks.extend(self._ocr_text_chunks(doc_id, source, record, epoch, provenance))

        self._write_document(source, doc_id, epoch, chunks, images)
        stat = Path(source.path).stat()
        state = SourceState(
            source_id=source.source_id,
            relative_path=source.relative_path or source.source_id,
            file_size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            content_hash=content_hash,
            document_type=source.document_type,
            last_successful_epoch=epoch,
            last_processed_at=datetime.now(timezone.utc).isoformat(),
            status="active",
            role_mask=source.role_mask,
            dept_mask=source.dept_mask,
            source_trust=trust_record.source_trust,
            approval_status=trust_record.approval_status,
        )
        if state_sink is None:
            self.state_store.upsert(state)
        else:
            state_sink.append(state)
        if trust_sink is not None:
            trust_sink.append(trust_record)
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
        trust_records: list[SourceTrustRecord] = []
        for source in sources:
            try:
                chunks, images = self.ingest_source(source, epoch, state_sink=pending_states, trust_sink=trust_records)
            except Exception:
                result.failed_sources.append(source.source_id)
                raise
            expected.add(self._doc_id(source))
            result.documents_processed += 1
            result.chunks_written += chunks
            result.images_written += images
        result.quarantined_sources = sorted(record.source_id for record in trust_records if record.is_quarantined)
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
        trust_records: list[SourceTrustRecord] = []
        for source_id in changes.new + changes.modified:
            source = source_by_id[source_id]
            chunks, images = self.ingest_source(source, to_epoch, state_sink=pending_states, trust_sink=trust_records)
            result.documents_processed += 1
            result.chunks_written += chunks
            result.images_written += images
        result.quarantined_sources = sorted(record.source_id for record in trust_records if record.is_quarantined)

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
        self.write_trust_manifest(epoch)

    # -- snapshot-level trust manifest -------------------------------------

    @staticmethod
    def _trust_manifest_id(epoch: str) -> str:
        return str(uuid.uuid5(_TRUST_MANIFEST_NAMESPACE, f"epoch-trust-manifest:{epoch}"))

    def trust_manifest(self, epoch: str) -> dict | None:
        """Return the persisted trust manifest for a sealed epoch, if any."""
        writer = self.text_writer
        if not writer.client.collection_exists(writer.collection_name):
            return None
        records = writer.client.retrieve(
            collection_name=writer.collection_name,
            ids=[self._trust_manifest_id(epoch)],
            with_payload=True,
            with_vectors=False,
        )
        return records[0].payload if records else None

    def write_trust_manifest(self, epoch: str) -> dict:
        """Persist the composition of this epoch's sources as snapshot metadata.

        The points already carry per-source provenance; this records the epoch's
        aggregate decision next to the seal marker, so "what was admitted into
        this snapshot and on whose authority" is answerable from the snapshot
        itself rather than by re-deriving it from the source tree later.

        The manifest is derived from the points the snapshot validator just
        accepted, so it cannot claim a composition the epoch does not have:
        anything quarantined fails validation before sealing is reached.
        """
        from qdrant_client.http.models import PointStruct

        writer = self.text_writer
        writer.ensure_collection()
        points = [
            *scroll_active_points(writer.client, writer.collection_name, epoch, doc_type="text"),
            *scroll_active_points(self.image_writer.client, self.image_writer.collection_name, epoch, doc_type="image"),
        ]
        summary: dict[str, int] = {}
        approvers: set[str] = set()
        for record in points:
            provenance = (record.payload or {}).get("provenance") or {}
            trust_class = provenance.get("trust_class") or "UNKNOWN"
            summary[trust_class] = summary.get(trust_class, 0) + 1
            actor = provenance.get("approval_actor")
            if actor and trust_class == "APPROVED_EXTERNAL":
                approvers.add(actor)
        manifest = {
            "manifest_type": "source_trust",
            "trust_class_counts": {key: summary[key] for key in sorted(summary)},
            "approval_actors": sorted(approvers),
            "provenance": {"provenance_schema_version": PROVENANCE_SCHEMA_VERSION},
        }
        writer.client.upsert(
            collection_name=writer.collection_name,
            points=[
                PointStruct(
                    id=self._trust_manifest_id(epoch),
                    vector=[1.0] + [0.0] * (writer.dimension - 1),
                    payload={
                        "doc_type": "epoch_manifest",
                        "epoch_state": "sealed",
                        "doc_version_epoch": epoch,
                        # Archived so the manifest is never mistaken for corpus
                        # content by the active-status scrolls that drive
                        # validation and recall.
                        "status": "archived",
                        "source_trust": manifest,
                    },
                )
            ],
            wait=True,
        )
        return manifest

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
    trust_config = config.get("source_trust", {}) or {}
    approval_store_path = trust_config.get("approval_store_path") or str(
        Path(state_db_path).with_name("offline_source_trust.sqlite3")
    )
    return SnapshotBuilder(
        processor=configured_document_processor(config),
        text_embedder=text_embedder,
        image_processor=image_processor,
        text_writer=QdrantTextWriter(client, text_config["collection"], int(text_config["dimension"])),
        image_writer=QdrantImageWriter(client, image_config["collection"], int(image_config.get("dimension", 512))),
        state_store=StateStore(state_db_path),
        es_writer=es_writer,
        trust_registry=TrustRegistry(approval_store_path),
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
