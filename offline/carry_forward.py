"""Snapshot carry-forward for incremental epochs.

An epoch is a complete immutable snapshot, so an incremental build must copy
unchanged documents from the source epoch into the target epoch rather than
only writing the changed files. Embedding versions must be compatible; a model
or preprocessing change requires a full rebuild instead.
"""

from __future__ import annotations

import uuid

from offline.chunking import POINT_NAMESPACE


class IncompatibleEmbeddingVersion(RuntimeError):
    """Raised when a source epoch cannot be carried into the target contract."""


def _scroll_active(client, collection: str, epoch: str, doc_type: str):
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
            with_vectors=True,
            offset=offset,
        )
        records.extend(page)
        if offset is None:
            break
    return records


def carry_forward_text(
    text_writer,
    source_epoch: str,
    target_epoch: str,
    *,
    allowed_versions: set[str] | None = None,
    doc_ids: set[str] | None = None,
) -> int:
    from qdrant_client.http.models import PointStruct

    records = _scroll_active(text_writer.client, text_writer.collection_name, source_epoch, "text")
    if doc_ids is not None:
        records = [record for record in records if (record.payload or {}).get("doc_id") in doc_ids]
    if not records:
        return 0
    _assert_versions(records, allowed_versions, "text")
    text_writer.ensure_collection()
    points = []
    for record in records:
        payload = dict(record.payload or {})
        payload["doc_version_epoch"] = target_epoch
        points.append(
            PointStruct(
                id=str(uuid.uuid5(POINT_NAMESPACE, f"{payload['chunk_id']}:{target_epoch}")),
                vector=record.vector,
                payload=payload,
            )
        )
    text_writer.client.upsert(collection_name=text_writer.collection_name, points=points, wait=True)
    text_writer.ensure_epoch_embedding_version(target_epoch)
    return len(points)


def carry_forward_images(
    image_writer,
    source_epoch: str,
    target_epoch: str,
    *,
    allowed_versions: set[str] | None = None,
    doc_ids: set[str] | None = None,
) -> int:
    from qdrant_client.http.models import PointStruct

    records = _scroll_active(image_writer.client, image_writer.collection_name, source_epoch, "image")
    if doc_ids is not None:
        records = [record for record in records if (record.payload or {}).get("doc_id") in doc_ids]
    if not records:
        return 0
    _assert_versions(records, allowed_versions, "image")
    image_writer.ensure_collection()
    points = []
    for record in records:
        payload = dict(record.payload or {})
        payload["doc_version_epoch"] = target_epoch
        points.append(
            PointStruct(
                id=str(uuid.uuid5(POINT_NAMESPACE, f"image:{payload['image_id']}:{target_epoch}")),
                vector=record.vector,
                payload=payload,
            )
        )
    image_writer.client.upsert(collection_name=image_writer.collection_name, points=points, wait=True)
    image_writer.ensure_epoch_embedding_version(target_epoch)
    return len(points)


def carry_forward_es(es_writer, source_epoch: str, target_epoch: str, *, doc_ids: set[str] | None = None) -> int:
    documents = es_writer.documents_for_epoch(source_epoch)
    if doc_ids is not None:
        documents = [document for document in documents if document.get("doc_id") in doc_ids]
    if not documents:
        return 0
    carried = [{**document, "doc_version_epoch": target_epoch} for document in documents]
    es_writer.upsert_documents(carried)
    return len(carried)


def _assert_versions(records, allowed_versions: set[str] | None, kind: str) -> None:
    if not allowed_versions:
        return
    for record in records:
        version = (record.payload or {}).get("embedding_version")
        if version not in allowed_versions:
            raise IncompatibleEmbeddingVersion(
                f"cannot carry forward {kind} point {record.id}: embedding_version {version!r} "
                f"is incompatible with the target contract {sorted(allowed_versions)}; run a full rebuild"
            )
