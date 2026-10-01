"""In-memory fakes for offline pipeline tests (no external services)."""

from __future__ import annotations

import copy


class _FakeIndices:
    def __init__(self, store: dict):
        self._store = store

    def exists(self, index: str) -> bool:
        return index in self._store

    def create(self, index: str, mappings=None, settings=None):
        self._store[index] = {
            "mappings": copy.deepcopy(mappings) if mappings else {},
            "settings": copy.deepcopy(settings) if settings else {},
            "docs": {},
        }

    def delete(self, index: str):
        self._store.pop(index, None)

    def get_mapping(self, index: str):
        return {index: {"mappings": self._store[index]["mappings"]}}


class FakeElasticsearchClient:
    """Tiny subset of the elasticsearch client used by ElasticsearchWriter."""

    def __init__(self):
        self.store: dict = {}
        self.indices = _FakeIndices(self.store)

    def index(self, index: str, id: str, document: dict):
        self.store[index]["docs"][id] = dict(document)

    def delete(self, index: str, id: str):
        self.store[index]["docs"].pop(id, None)

    def update(self, index: str, id: str, doc: dict):
        self.store[index]["docs"].setdefault(id, {}).update(doc)

    def search(self, index: str, query: dict, size: int = 10, _source: bool = True, track_total_hits: bool = False):
        docs = self.store[index]["docs"]
        hits = [{"_id": doc_id, "_source": document} for doc_id, document in docs.items() if _matches(document, query)]
        return {"hits": {"total": {"value": len(hits)}, "hits": hits[:size]}}

    def set_mapping_type(self, index: str, field: str, value: str):
        self.store[index]["mappings"].setdefault("properties", {}).setdefault(field, {})["type"] = value


def _matches(document: dict, query: dict) -> bool:
    if "match_all" in query:
        return True
    if "term" in query:
        ((field, expected),) = query["term"].items()
        return document.get(field) == expected
    if "exists" in query:
        return query["exists"]["field"] in document
    if "bool" in query:
        clauses = query["bool"]
        must = clauses.get("must", [])
        should = clauses.get("should", [])
        must_not = clauses.get("must_not", [])
        if any(not _matches(document, clause) for clause in must):
            return False
        if any(_matches(document, clause) for clause in must_not):
            return False
        if should:
            matched = sum(1 for clause in should if _matches(document, clause))
            if matched < clauses.get("minimum_should_match", 1):
                return False
        return True
    raise NotImplementedError(f"unsupported fake ES query: {query}")
