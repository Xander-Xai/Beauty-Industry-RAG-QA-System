"""Security contracts shared by the online retrieval paths."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from common.models import RecallResult


def test_document_permission_metadata_must_be_present_and_well_formed():
    from common.auth import is_document_authorized

    assert is_document_authorized({"role_mask": 0, "dept_mask": 0}, 0, 0)
    assert not is_document_authorized({}, 0, 0)
    assert not is_document_authorized({}, 0xFFFFFFFF, 0xFFFFFFFF)
    assert not is_document_authorized({"role_mask": "2", "dept_mask": 0}, 2, 0)
    assert not is_document_authorized({"role_mask": 2, "dept_mask": -1}, 2, 0)


def test_role_department_and_combined_restrictions_match_canonical_policy():
    from common.auth import is_document_authorized

    assert is_document_authorized({"role_mask": 2, "dept_mask": 0}, 2, 0)
    assert not is_document_authorized({"role_mask": 2, "dept_mask": 0}, 1, 0)
    assert is_document_authorized({"role_mask": 0, "dept_mask": 4}, 0, 4)
    assert not is_document_authorized({"role_mask": 0, "dept_mask": 4}, 0, 2)
    assert is_document_authorized({"role_mask": 2, "dept_mask": 4}, 2, 4)
    assert not is_document_authorized({"role_mask": 2, "dept_mask": 4}, 2, 2)


def test_configured_admin_policy_is_shared_with_retrieval_rbac():
    from auth.bitmask_rbac import is_allowed as retrieval_is_allowed
    from common.auth import is_allowed as service_is_allowed
    from common.config import get_config

    admin_mask = get_config().rbac.roles["admin"]
    args = (2, admin_mask, 2, 0)
    assert service_is_allowed(*args)
    assert retrieval_is_allowed(*args) == service_is_allowed(*args)


def test_parallel_recall_drops_missing_and_unauthorized_metadata():
    from retrieval.parallel_recall import ParallelRecallManager

    manager = ParallelRecallManager.__new__(ParallelRecallManager)
    candidates = [
        RecallResult(
            doc_id="public",
            content="public",
            score=1.0,
            source="dense_bge",
            metadata={"role_mask": 0, "dept_mask": 0},
        ),
        RecallResult(
            doc_id="secret",
            content="secret",
            score=0.9,
            source="dense_bge",
            metadata={"role_mask": 2, "dept_mask": 2},
        ),
        RecallResult(doc_id="legacy", content="legacy", score=0.8, source="dense_bge"),
    ]

    visible = manager._apply_rbac_filter(candidates, user_role_mask=1, user_dept_mask=1)

    assert [result.doc_id for result in visible] == ["public"]


def test_parallel_recall_fallback_requires_permission_scoped_search():
    from retrieval.parallel_recall import ParallelRecallManager

    class DenseRetriever:
        def search(self, query_embedding, qdrant_filter, top_k):
            return []

    class BM25Retriever:
        def __init__(self):
            self.fallback_context = None

        def search(self, query, role_mask, dept_mask, top_k):
            return []

        def fallback_search(self, query, user_role_mask, user_dept_mask, top_k=100):
            self.fallback_context = (user_role_mask, user_dept_mask)
            return [
                {
                    "doc_id": "fallback-secret",
                    "content": "secret",
                    "score": 1.0,
                    "metadata": {"role_mask": 2, "dept_mask": 2},
                }
            ]

    manager = ParallelRecallManager.__new__(ParallelRecallManager)
    manager._dense_retriever = DenseRetriever()
    manager._bm25_retriever = BM25Retriever()
    manager._clip_retriever = None
    manager._rewrite_variants_retriever = None
    manager.max_workers = 1

    results, _ = manager.execute(
        query="policy",
        query_embedding=[0.1],
        user_role_mask=1,
        user_dept_mask=1,
        use_clip=False,
        top_k_per_path={
            "dense_bge": {"enabled": True, "top_k": 5},
            "bm25_es": {"enabled": False, "top_k": 0},
            "clip_visual": {"enabled": False, "top_k": 0},
            "rewrite_variants": {"enabled": False, "top_k": 0},
        },
    )

    assert manager._bm25_retriever.fallback_context == (1, 1)
    assert all(result.doc_id != "fallback-secret" for result in results)


def test_parallel_recall_filters_every_channel_before_fusion():
    from retrieval.parallel_recall import ParallelRecallManager

    public = {"role_mask": 0, "dept_mask": 0}
    allowed = {"role_mask": 1, "dept_mask": 1}
    denied = {"role_mask": 2, "dept_mask": 2}

    class DenseRetriever:
        def search(self, query_embedding, qdrant_filter, top_k):
            return [
                {"doc_id": "allowed", "content": "a", "score": 0.9, "metadata": allowed},
                {"doc_id": "public", "content": "p", "score": 0.8, "metadata": public},
            ]

    class BM25Retriever:
        def search(self, query, user_role_mask, user_dept_mask, top_k):
            return [{"doc_id": "denied", "content": "x", "score": 1.0, "metadata": denied}]

        def fallback_search(self, query, user_role_mask, user_dept_mask, top_k=100):
            return []

    manager = ParallelRecallManager.__new__(ParallelRecallManager)
    manager._dense_retriever = DenseRetriever()
    manager._bm25_retriever = BM25Retriever()
    manager._clip_retriever = None
    manager._rewrite_variants_retriever = None
    manager.max_workers = 2

    results, _ = manager.execute(
        query="policy",
        query_embedding=np.array([0.1]),
        user_role_mask=1,
        user_dept_mask=1,
        use_clip=False,
        top_k_per_path={
            "dense_bge": {"enabled": True, "top_k": 10},
            "bm25_es": {"enabled": True, "top_k": 10},
            "clip_visual": {"enabled": False, "top_k": 0},
            "rewrite_variants": {"enabled": False, "top_k": 0},
        },
    )

    assert {result.doc_id for result in results} == {"allowed", "public"}


def test_bm25_filter_always_enforces_department_and_rejects_bad_identity():
    from retrieval.bm25_retriever import BM25Retriever

    retriever = BM25Retriever.__new__(BM25Retriever)
    retriever._es_version = (8, 0)
    query = retriever._build_es_query("policy", 1, 0, 10)
    filters = query["query"]["bool"]["filter"]
    scripts = [
        clause["script"]["script"]["source"]
        for filter_clause in filters
        for clause in filter_clause.get("bool", {}).get("should", [])
        if "script" in clause
    ]
    assert any("dept_mask" in script for script in scripts)
    assert any(filter_clause.get("exists", {}).get("field") == "role_mask" for filter_clause in filters)
    assert any(filter_clause.get("exists", {}).get("field") == "dept_mask" for filter_clause in filters)

    try:
        retriever._build_es_query("policy", "bad", 0, 10)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid authorization identity must not be coerced to anonymous access")


def test_bm25_combined_role_and_department_filters_keep_public_exceptions():
    from retrieval.bm25_retriever import BM25Retriever

    retriever = BM25Retriever.__new__(BM25Retriever)
    retriever._es_version = (7, 10)
    filters = retriever._build_es_query("policy", 2, 4, 10)["query"]["bool"]["filter"]
    permission_clauses = [clause for clause in filters if "bool" in clause]

    assert len(permission_clauses) == 2
    for clause, field, param in zip(
        permission_clauses,
        ("role_mask", "dept_mask"),
        ("user_role_mask", "user_dept_mask"),
        strict=True,
    ):
        alternatives = clause["bool"]["should"]
        assert {"term": {field: 0}} in alternatives
        bitwise = next(item["script"]["script"] for item in alternatives if "script" in item)
        assert bitwise["params"][param] in (2, 4)


def test_configured_admin_keeps_canonical_bypass_without_dropping_metadata_checks():
    from common.config import get_config
    from retrieval.bm25_retriever import BM25Retriever

    retriever = BM25Retriever.__new__(BM25Retriever)
    retriever._es_version = (8, 0)
    admin_mask = get_config().rbac.roles["admin"]
    filters = retriever._build_es_query("policy", admin_mask, 0, 10)["query"]["bool"]["filter"]

    assert any(clause.get("exists", {}).get("field") == "role_mask" for clause in filters)
    assert any(clause.get("exists", {}).get("field") == "dept_mask" for clause in filters)
    assert not any("script" in str(clause) for clause in filters)


def test_bm25_fallback_query_carries_the_same_authorization_filters():
    from retrieval.bm25_retriever import BM25Retriever

    retriever = BM25Retriever.__new__(BM25Retriever)
    retriever._es_version = (8, 0)
    query = retriever._build_es_query("policy", 1, 2, 10)
    fallback = retriever._build_es_fallback_query("policy", 1, 2, 10)
    assert fallback["query"]["bool"]["filter"] == query["query"]["bool"]["filter"]


def test_embedding_results_retain_minimal_permission_metadata():
    from models.embedding_service import EmbeddingService

    class Point:
        id = "doc-1"
        score = 0.9
        payload = {
            "doc_id": "doc-1",
            "content": "content",
            "role_mask": 2,
            "dept_mask": 4,
        }

    class Client:
        def __init__(self):
            self.request = None

        def search(self, **kwargs):
            self.request = kwargs
            return [Point()]

    service = EmbeddingService.__new__(EmbeddingService)
    client = Client()
    service._qdrant_client = client

    result = service.search_qdrant_text(np.array([0.1]), top_k=1)[0]

    assert {"role_mask", "dept_mask"}.issubset(set(client.request["with_payload"]))
    assert result["metadata"]["role_mask"] == 2
    assert result["metadata"]["dept_mask"] == 4


def test_clip_embedding_results_retain_minimal_permission_metadata():
    from models.embedding_service import EmbeddingService

    class Point:
        score = 0.9
        payload = {
            "doc_id": "image-1",
            "content": "image content",
            "image_uri": "image.png",
            "role_mask": 2,
            "dept_mask": 4,
        }

    class Client:
        def __init__(self):
            self.request = None

        def search(self, **kwargs):
            self.request = kwargs
            return [Point()]

    service = EmbeddingService.__new__(EmbeddingService)
    client = Client()
    service._qdrant_client = client

    result = service.search_qdrant_image(np.array([0.1]), top_k=1)[0]

    assert {"role_mask", "dept_mask"}.issubset(set(client.request["with_payload"]))
    assert result["metadata"] == {"role_mask": 2, "dept_mask": 4}


def test_cached_async_clip_results_are_rechecked_for_current_permissions(monkeypatch):
    from retrieval.parallel_recall import ParallelRecallManager

    session = SimpleNamespace(
        async_clip_results=[
            RecallResult(
                doc_id="secret",
                content="secret",
                score=1.0,
                source="clip_async",
                metadata={"role_mask": 2, "dept_mask": 2},
            ),
            RecallResult(
                doc_id="public",
                content="public",
                score=0.9,
                source="clip_async",
                metadata={"role_mask": 0, "dept_mask": 0},
            ),
        ]
    )
    manager = ParallelRecallManager.__new__(ParallelRecallManager)

    from retrieval import parallel_recall

    monkeypatch.setitem(parallel_recall.config, "clip_async", {"enabled": True, "preheat_on_multiturn": True})
    results = manager.recall_async_clip("query", None, session=session, user_role_mask=1, user_dept_mask=1)

    assert [result.doc_id for result in results] == ["public"]
    synchronous = manager._apply_rbac_filter(session.async_clip_results, 1, 1)
    assert [result.doc_id for result in synchronous] == [result.doc_id for result in results]
