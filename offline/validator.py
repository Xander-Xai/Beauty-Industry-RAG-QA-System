"""Epoch snapshot validator.

Runs before sealing. Any error makes ``seal-epoch`` fail, so a partially built
staging epoch can never be sealed by accident.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from offline.validation import UINT32_MAX


@dataclass
class ValidationReport:
    epoch: str
    text_count: int = 0
    image_count: int = 0
    es_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class SnapshotValidationError(RuntimeError):
    def __init__(self, report: ValidationReport):
        self.report = report
        super().__init__(f"epoch {report.epoch!r} validation failed: " + "; ".join(report.errors))


class SnapshotValidator:
    def __init__(
        self,
        *,
        text_client,
        text_collection: str,
        image_client,
        image_collection: str,
        es_writer,
        allowed_text_versions: set[str] | None = None,
        allowed_image_versions: set[str] | None = None,
        expected_doc_ids: set[str] | None = None,
    ):
        self.text_client = text_client
        self.text_collection = text_collection
        self.image_client = image_client
        self.image_collection = image_collection
        self.es_writer = es_writer
        self.allowed_text_versions = allowed_text_versions
        self.allowed_image_versions = allowed_image_versions
        self.expected_doc_ids = expected_doc_ids

    def validate(self, epoch: str) -> ValidationReport:
        report = ValidationReport(epoch=epoch)
        text_points = _scroll_active(self.text_client, self.text_collection, epoch, doc_type="text")
        image_points = _scroll_active(self.image_client, self.image_collection, epoch, doc_type="image")
        report.text_count = len(text_points)
        report.image_count = len(image_points)
        if self.es_writer is not None:
            report.es_count = self.es_writer.count_documents(epoch)

        seen_chunks: set[str] = set()
        for point in text_points:
            payload = point.payload or {}
            _validate_permission_fields(payload, report, "text point")
            if payload.get("doc_version_epoch") != epoch:
                report.errors.append(f"text point {point.id} has wrong epoch {payload.get('doc_version_epoch')!r}")
            version = payload.get("embedding_version")
            if not version:
                report.errors.append(f"text point {point.id} has no embedding_version")
            elif self.allowed_text_versions and version not in self.allowed_text_versions:
                report.errors.append(f"text point {point.id} has unknown embedding_version {version!r}")
            chunk_id = payload.get("chunk_id")
            if not chunk_id:
                report.errors.append(f"text point {point.id} has no chunk_id")
            elif chunk_id in seen_chunks:
                report.errors.append(f"duplicate logical chunk_id {chunk_id!r} in epoch {epoch!r}")
            else:
                seen_chunks.add(chunk_id)

        seen_images: set[str] = set()
        for point in image_points:
            payload = point.payload or {}
            _validate_permission_fields(payload, report, "image point")
            if payload.get("doc_version_epoch") != epoch:
                report.errors.append(f"image point {point.id} has wrong epoch {payload.get('doc_version_epoch')!r}")
            version = payload.get("embedding_version")
            if not version:
                report.errors.append(f"image point {point.id} has no embedding_version")
            elif self.allowed_image_versions and version not in self.allowed_image_versions:
                report.errors.append(f"image point {point.id} has unknown embedding_version {version!r}")
            image_id = payload.get("image_id")
            if not image_id:
                report.errors.append(f"image point {point.id} has no image_id")
            elif image_id in seen_images:
                report.errors.append(f"duplicate logical image_id {image_id!r} in epoch {epoch!r}")
            else:
                seen_images.add(image_id)
            if not payload.get("doc_id") or not payload.get("source_path"):
                report.errors.append(f"orphan image point {point.id} is missing doc_id or source_path")

        self._validate_es(report, epoch)
        self._validate_expected_doc_ids(report, text_points, image_points)
        return report

    def validate_or_raise(self, epoch: str) -> ValidationReport:
        report = self.validate(epoch)
        if not report.ok:
            raise SnapshotValidationError(report)
        return report

    def _validate_es(self, report: ValidationReport, epoch: str) -> None:
        if self.es_writer is None:
            report.warnings.append("Elasticsearch writer not configured; skipping ES validation")
            return
        documents = self.es_writer.documents_for_epoch(epoch)
        if len(documents) != report.es_count:
            report.errors.append(
                f"ES epoch {epoch!r} returned {len(documents)} documents but count is {report.es_count}"
            )
        for document in documents:
            _validate_permission_fields(document, report, "ES document")
            if document.get("status") != "active":
                report.errors.append(f"ES document {document.get('chunk_id')!r} is not active")
            if document.get("doc_version_epoch") != epoch:
                report.errors.append(
                    f"ES document {document.get('chunk_id')!r} has wrong epoch {document.get('doc_version_epoch')!r}"
                )

    def _validate_expected_doc_ids(self, report, text_points, image_points) -> None:
        if self.expected_doc_ids is None:
            return
        present = {point.payload.get("doc_id") for point in text_points + image_points}
        present.discard(None)
        missing = self.expected_doc_ids - present
        if missing:
            report.errors.append(f"epoch {report.epoch!r} is missing expected documents: {sorted(missing)[:10]}")
        unexpected = present - self.expected_doc_ids
        if unexpected:
            report.errors.append(
                f"epoch {report.epoch!r} contains documents outside the expected set: {sorted(unexpected)[:10]}"
            )


def _validate_permission_fields(payload: dict, report: ValidationReport, label: str) -> None:
    for name in ("role_mask", "dept_mask"):
        value = payload.get(name)
        if type(value) is not int or not 0 <= value <= UINT32_MAX:
            report.errors.append(f"{label} has invalid {name}={value!r}")
    if payload.get("status") != "active":
        report.errors.append(f"{label} has non-active status {payload.get('status')!r}")


def _scroll_active(client, collection: str, epoch: str, *, doc_type: str):
    from qdrant_client.http.models import FieldCondition, Filter, IsEmptyCondition, MatchValue, PayloadField

    if not client.collection_exists(collection):
        return []
    epoch_conditions = [FieldCondition(key="doc_version_epoch", match=MatchValue(value=epoch))]
    if epoch == "default":
        epoch_conditions.append(IsEmptyCondition(is_empty=PayloadField(key="doc_version_epoch")))
    scroll_filter = Filter(
        must=[
            FieldCondition(key="status", match=MatchValue(value="active")),
            FieldCondition(key="doc_type", match=MatchValue(value=doc_type)),
            Filter(should=epoch_conditions),
        ]
    )
    records = []
    offset = None
    while True:
        page, offset = client.scroll(
            collection_name=collection,
            scroll_filter=scroll_filter,
            limit=256,
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        records.extend(page)
        if offset is None:
            break
    return records
