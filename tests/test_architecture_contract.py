"""Executable architecture contracts for the verified monolith RAG path."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from common.models import RecallResult
from retrieval_service.rerank.rrf_fusion import rrf_fusion


def test_qdrant_filter_scopes_results_to_active_epoch():
    """Vector recall must filter both active status and knowledge epoch."""
    from qdrant_client.http.models import FieldCondition, MatchValue

    from auth.bitmask_rbac import build_qdrant_filter

    query_filter = build_qdrant_filter(1, 1, "20260725_01")
    must_matches = {
        condition.key: condition.match.value
        for condition in query_filter.must
        if isinstance(condition.match, MatchValue)
    }
    should_matches = {
        condition.key: condition.match.value
        for condition in query_filter.should
        if isinstance(condition, FieldCondition) and isinstance(condition.match, MatchValue)
    }

    assert must_matches == {"status": "active"}
    assert should_matches == {"doc_version_epoch": "20260725_01"}


def test_default_epoch_does_not_hide_existing_qdrant_data():
    """The sentinel default epoch means version isolation is not active yet."""
    from auth.bitmask_rbac import build_qdrant_filter

    query_filter = build_qdrant_filter(1, 1, "default")

    assert [condition.key for condition in query_filter.must] == ["status"]


def test_retrieval_rbac_treats_configured_admin_like_service_layer_admin():
    """Retrieval and service-layer RBAC must agree on configured admin access."""
    from auth.bitmask_rbac import is_allowed
    from common.config import get_config

    admin_mask = get_config().rbac.roles["admin"]

    assert is_allowed(
        dr=2,
        ur=admin_mask,
        dd=2,
        ud=0,
    )


def test_parallel_recall_removes_unauthorized_qdrant_hits():
    """Restricted Qdrant documents must not survive the recall boundary."""
    from retrieval.parallel_recall import ParallelRecallManager

    class DenseRetriever:
        def search(self, query_embedding, qdrant_filter, top_k):
            public_hits = [
                {
                    "doc_id": f"public-{index}",
                    "content": "public",
                    "score": 1.0 - index / 1000,
                    "metadata": {"role_mask": 0, "dept_mask": 0},
                }
                for index in range(50)
            ]
            return public_hits + [
                {
                    "doc_id": "quality-secret",
                    "content": "restricted",
                    "score": 0.99,
                    "metadata": {"role_mask": 2, "dept_mask": 2},
                }
            ]

    manager = ParallelRecallManager.__new__(ParallelRecallManager)
    manager._dense_retriever = DenseRetriever()
    manager._bm25_retriever = None
    manager._clip_retriever = None
    manager._rewrite_variants_retriever = None
    manager.max_workers = 1

    results, _agreement = manager.execute(
        query="法规",
        query_embedding=[0.1, 0.2],
        user_role_mask=1,
        user_dept_mask=1,
        use_clip=False,
        top_k_per_path={
            "dense_bge": {"enabled": True, "top_k": 60},
            "bm25_es": {"enabled": False, "top_k": 0},
            "clip_visual": {"enabled": False, "top_k": 0},
            "rewrite_variants": {"enabled": False, "top_k": 0},
        },
    )

    assert len(results) == 50
    assert all(result.doc_id != "quality-secret" for result in results)


def test_clip_recall_preserves_permission_metadata_for_post_filter():
    """CLIP adapters must not discard role/dept metadata before RBAC."""
    from retrieval.parallel_recall import ParallelRecallManager

    class FakeEmbeddingService:
        def encode_text_clip(self, query):
            return [0.1, 0.2]

    class FakeClipRetriever:
        def search(self, query_embedding, qdrant_filter, top_k):
            return [
                {
                    "doc_id": "visual-secret",
                    "content": "restricted image",
                    "score": 0.9,
                    "image_uri": "secret.png",
                    "metadata": {"role_mask": 2, "dept_mask": 2},
                }
            ]

    manager = ParallelRecallManager.__new__(ParallelRecallManager)
    manager._clip_retriever = FakeClipRetriever()

    with patch("models.embedding_service.EmbeddingService", FakeEmbeddingService):
        results = manager._recall_clip("包装图", None, 10)

    assert manager._apply_rbac_filter(results, 1, 1) == []


def test_rewrite_recall_preserves_permission_metadata_for_post_filter():
    """Rewrite adapters must not discard role/dept metadata before RBAC."""
    from retrieval.parallel_recall import ParallelRecallManager

    class FakeQueryRewriter:
        def generate_variants(self, query):
            return ["防腐剂限量"]

    class FakeDenseRetriever:
        def encode(self, query):
            return [0.1, 0.2]

        def search(self, query_embedding, qdrant_filter, top_k):
            return [
                {
                    "doc_id": "formula-secret",
                    "content": "restricted formula",
                    "score": 0.9,
                    "metadata": {"role_mask": 2, "dept_mask": 2},
                }
            ]

    manager = ParallelRecallManager.__new__(ParallelRecallManager)
    manager._dense_retriever = FakeDenseRetriever()

    with patch("rewrite.query_rewriter.QueryRewriter", FakeQueryRewriter):
        results = manager._recall_rewrite_variants("防腐剂", None, None, 30)

    assert manager._apply_rbac_filter(results, 1, 1) == []


def test_async_clip_recall_applies_the_same_rbac_boundary():
    """Background CLIP preheat must not cache documents the user cannot read."""
    from retrieval.parallel_recall import ParallelRecallManager

    class FakeEmbeddingService:
        def encode_text_clip(self, query):
            return [0.1, 0.2]

    class FakeClipRetriever:
        def search(self, query_embedding, qdrant_filter, top_k):
            return [
                {
                    "doc_id": "async-secret",
                    "content": "restricted image",
                    "score": 0.9,
                    "metadata": {"role_mask": 2, "dept_mask": 2},
                }
            ]

    manager = ParallelRecallManager.__new__(ParallelRecallManager)
    manager._clip_retriever = FakeClipRetriever()

    with patch("models.embedding_service.EmbeddingService", FakeEmbeddingService):
        results = manager.recall_async_clip(
            "包装图",
            qdrant_filter=None,
            user_role_mask=1,
            user_dept_mask=1,
        )

    assert results == []


def test_es_fallback_uses_the_same_permission_aware_query_as_primary_bm25():
    """Fallback must never bypass document role, department, or epoch filters."""
    from retrieval.bm25_retriever import BM25Retriever

    class FakeElasticsearch:
        def __init__(self):
            self.last_body = None

        def search(self, *, index, body):
            self.last_body = body
            return {"hits": {"hits": []}}

    retriever = BM25Retriever.__new__(BM25Retriever)
    retriever._es_client = FakeElasticsearch()
    retriever._es_version = (8, 12)
    retriever.enabled = True

    retriever.fallback_search(
        "防腐剂法规",
        user_role_mask=4,
        user_dept_mask=4,
        top_k=20,
    )

    filters = retriever._es_client.last_body["query"]["bool"]["filter"]
    assert any(item.get("term", {}).get("status") == "active" for item in filters)
    assert any("script" in option for item in filters for option in item.get("bool", {}).get("should", []))


def test_anonymous_es_query_cannot_match_department_restricted_public_docs():
    """role_mask=0 must still enforce dept_mask restrictions for anonymous users."""
    from retrieval.bm25_retriever import BM25Retriever

    retriever = BM25Retriever.__new__(BM25Retriever)
    retriever._es_version = (8, 12)

    query_body = retriever._build_es_query(
        "配方",
        user_role_mask=0,
        user_dept_mask=0,
        top_k=10,
    )
    filters = query_body["query"]["bool"]["filter"]
    script_sources = [
        option["script"]["script"]["source"]
        for item in filters
        for option in item.get("bool", {}).get("should", [])
        if "script" in option
    ]

    assert any("doc['dept_mask']" in source and "params.user_dept_mask" in source for source in script_sources)


def test_es_rbac_treats_configured_admin_like_service_layer_admin():
    """Configured admins must bypass ES role/dept filters just like media access."""
    from common.config import get_config
    from retrieval.bm25_retriever import BM25Retriever

    retriever = BM25Retriever.__new__(BM25Retriever)
    retriever._es_version = (8, 12)
    admin_mask = get_config().rbac.roles["admin"]

    query_body = retriever._build_es_query(
        "法规",
        user_role_mask=admin_mask,
        user_dept_mask=0,
        top_k=10,
    )
    filters = query_body["query"]["bool"]["filter"]

    assert all("bool" not in item for item in filters)


def test_semantic_rrf_weight_names_map_to_real_recall_paths():
    """Configured w_text/w_clip aliases must affect actual path names."""
    dense = RecallResult(
        doc_id="dense",
        content="dense",
        score=1.0,
        source="dense_bge",
    )
    clip = RecallResult(
        doc_id="clip",
        content="clip",
        score=1.0,
        source="clip_visual",
    )

    results = rrf_fusion(
        {"dense_bge": [dense], "clip_visual": [clip]},
        weights={"w_text": 0.5, "w_clip": 2.0, "w_ocr": 0.8},
    )

    assert [result.doc_id for result in results] == ["clip", "dense"]
    assert results[0].score == 2.0 / 61
    assert results[1].score == 0.5 / 61


def test_pipeline_builds_query_aware_rrf_overrides():
    """Regulation and visual signals must boost the matching recall paths."""
    from core.pipeline import OnlineRAGPipeline

    pipeline = OnlineRAGPipeline.__new__(OnlineRAGPipeline)
    weights = pipeline._build_rrf_weights(
        business_type="regulation",
        is_visual_relevant=True,
    )

    assert weights["bm25_es"] == 1.5
    assert weights["clip_visual"] == 2.0
    assert weights["dense_bge"] == 1.0
    assert weights["rewrite_variant"] == 1.0


def test_interview_baseline_locks_the_recall_count_and_mainline():
    """The canonical interview answer must stay aligned with executable routing."""
    baseline = (Path(__file__).resolve().parents[1] / "docs" / "architecture-baseline.md").read_text(encoding="utf-8")

    assert "唯一事实基线" in baseline
    assert "FastAPI 单体主链路" in baseline
    assert "动态 2 至 4 路召回" in baseline
    assert "ES Fallback 不算第五路" in baseline
