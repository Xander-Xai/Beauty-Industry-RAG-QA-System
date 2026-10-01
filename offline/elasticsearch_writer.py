"""Elasticsearch writer for the offline knowledge base.

Writes the BM25 index (``cosmetics_docs``) with an explicit mapping so RBAC
masks and epoch fields never degrade into analyzed text. Document ids are
derived from the logical chunk/image id plus the epoch, so the same logical
content coexists across epochs without collisions.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

from offline.validation import validate_epoch, validate_permissions

DEFAULT_INDEX_NAME = "cosmetics_docs"

INDEX_MAPPING = {
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    "mappings": {
        "properties": {
            "doc_id": {"type": "keyword"},
            "chunk_id": {"type": "keyword"},
            "chunk_index": {"type": "integer"},
            "image_id": {"type": "keyword"},
            "content": {"type": "text"},
            "source_path": {"type": "keyword"},
            "doc_type": {"type": "keyword"},
            "embedding_type": {"type": "keyword"},
            "embedding_version": {"type": "keyword"},
            "role_mask": {"type": "long"},
            "dept_mask": {"type": "long"},
            "status": {"type": "keyword"},
            "doc_version_epoch": {"type": "keyword"},
            "metadata": {"type": "object", "dynamic": True},
        }
    },
}

REQUIRED_FIELD_TYPES = {
    "doc_id": "keyword",
    "chunk_id": "keyword",
    "chunk_index": "integer",
    "content": "text",
    "source_path": "keyword",
    "doc_type": "keyword",
    "embedding_type": "keyword",
    "embedding_version": "keyword",
    "role_mask": "long",
    "dept_mask": "long",
    "status": "keyword",
    "doc_version_epoch": "keyword",
    "metadata": "object",
}


class ElasticsearchDocument(Protocol):
    doc_id: str
    chunk_id: str
    chunk_index: int
    content: str
    source_path: str
    doc_type: str
    embedding_type: str
    embedding_version: str
    role_mask: int
    dept_mask: int
    status: str
    doc_version_epoch: str
    metadata: dict


def document_id(logical_id: str, epoch: str) -> str:
    """Stable ES document id for a logical chunk/image in one epoch."""
    return hashlib.sha256(f"{logical_id}:{epoch}".encode()).hexdigest()


class ElasticsearchWriter:
    """Upsert/replace documents in the BM25 index with explicit mapping checks."""

    def __init__(self, client, index_name: str = DEFAULT_INDEX_NAME, page_size: int = 1000):
        if not index_name:
            raise ValueError("an index name is required")
        if type(page_size) is not int or page_size <= 0:
            raise ValueError("page_size must be a positive integer")
        self.client = client
        self.index_name = index_name
        self.page_size = page_size

    def ensure_index(self, *, recreate: bool = False, confirm: bool = False) -> None:
        exists = bool(self.client.indices.exists(index=self.index_name))
        if exists and recreate:
            if not confirm:
                raise ValueError("recreating the index requires explicit confirmation")
            self.client.indices.delete(index=self.index_name)
            exists = False
        if not exists:
            self.client.indices.create(
                index=self.index_name,
                mappings=INDEX_MAPPING["mappings"],
                settings=INDEX_MAPPING["settings"],
            )
        self.validate_mapping()

    def validate_mapping(self) -> None:
        mapping = self.client.indices.get_mapping(index=self.index_name)
        properties = _extract_properties(mapping, self.index_name)
        for field, expected in REQUIRED_FIELD_TYPES.items():
            actual = properties.get(field, {}).get("type")
            if actual != expected:
                raise ValueError(f"index {self.index_name!r} field {field!r} must be {expected!r}, found {actual!r}")

    def build_document(self, chunk) -> dict:
        validate_permissions(chunk.role_mask, chunk.dept_mask)
        validate_epoch(chunk.doc_version_epoch)
        return {
            "doc_id": chunk.doc_id,
            "chunk_id": chunk.chunk_id,
            "chunk_index": chunk.chunk_index,
            "content": chunk.text if hasattr(chunk, "text") else chunk.content,
            "source_path": chunk.source_path,
            "doc_type": "text",
            "embedding_type": getattr(chunk, "embedding_type", "bge"),
            "embedding_version": getattr(chunk, "embedding_version", "unspecified-v1"),
            "role_mask": chunk.role_mask,
            "dept_mask": chunk.dept_mask,
            "status": chunk.status,
            "doc_version_epoch": chunk.doc_version_epoch,
            "metadata": getattr(chunk, "metadata", {}),
        }

    def upsert_documents(self, documents: list[dict]) -> None:
        for document in documents:
            self.client.index(
                index=self.index_name,
                id=document_id(document["chunk_id"], document["doc_version_epoch"]),
                document=document,
            )

    def replace_document(self, doc_id: str, doc_version_epoch: str, documents: list[dict]) -> None:
        validate_epoch(doc_version_epoch)
        if any(
            document["doc_id"] != doc_id or document["doc_version_epoch"] != doc_version_epoch for document in documents
        ):
            raise ValueError("replacement documents must match the requested document and epoch")
        desired_ids = {document_id(document["chunk_id"], doc_version_epoch) for document in documents}
        for existing_id in self._existing_ids(doc_id, doc_version_epoch):
            if existing_id not in desired_ids:
                self.client.delete(index=self.index_name, id=existing_id)
        self.upsert_documents(documents)

    def delete_document(self, doc_id: str, doc_version_epoch: str) -> None:
        for existing_id in self._existing_ids(doc_id, doc_version_epoch):
            self.client.delete(index=self.index_name, id=existing_id)

    def archive_document(self, doc_id: str, doc_version_epoch: str) -> None:
        for existing_id in self._existing_ids(doc_id, doc_version_epoch):
            self.client.update(index=self.index_name, id=existing_id, doc={"status": "archived"})

    def documents_for_epoch(self, doc_version_epoch: str) -> list[dict]:
        hits = self._paginated_search(_epoch_query(doc_version_epoch), source=True)
        return [hit["_source"] for hit in hits]

    def _paginated_search(self, query: dict, *, source: bool) -> list[dict]:
        """Search every matching document using ``search_after`` pagination."""
        hits: list[dict] = []
        search_after = None
        while True:
            kwargs = {
                "index": self.index_name,
                "query": query,
                "size": self.page_size,
                "_source": source,
                # Sort on the keyword chunk_id: ES 8 disallows sorting/fielddata
                # on _id, and chunk_id is unique within an epoch.
                "sort": [{"chunk_id": "asc"}],
            }
            if search_after is not None:
                kwargs["search_after"] = search_after
            response = self.client.search(**kwargs)
            page = response["hits"]["hits"]
            hits.extend(page)
            if len(page) < self.page_size:
                break
            last = page[-1]
            search_after = last.get("sort") or [last["_source"].get("chunk_id")]
        return hits

    def count_documents(self, doc_version_epoch: str | None = None) -> int:
        query = _epoch_query(doc_version_epoch) if doc_version_epoch is not None else {"match_all": {}}
        response = self.client.search(index=self.index_name, query=query, size=0, track_total_hits=True)
        return int(response["hits"]["total"]["value"])

    def _existing_ids(self, doc_id: str, doc_version_epoch: str) -> list[str]:
        query = {
            "bool": {
                "must": [{"term": {"doc_id": doc_id}}],
                "should": _epoch_conditions(doc_version_epoch),
                "minimum_should_match": 1,
            }
        }
        return [hit["_id"] for hit in self._paginated_search(query, source=False)]


def _epoch_conditions(epoch: str) -> list[dict]:
    conditions = [{"term": {"doc_version_epoch": epoch}}]
    if epoch == "default":
        conditions.append({"bool": {"must_not": [{"exists": {"field": "doc_version_epoch"}}]}})
    return conditions


def _epoch_query(epoch: str) -> dict:
    return {"bool": {"should": _epoch_conditions(epoch), "minimum_should_match": 1}}


def _extract_properties(mapping: dict, index_name: str) -> dict:
    if index_name in mapping:
        return mapping[index_name].get("mappings", {}).get("properties", {})
    return mapping.get("mappings", {}).get("properties", {})
