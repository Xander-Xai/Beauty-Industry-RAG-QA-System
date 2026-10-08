"""Executable architecture contracts for the verified monolith RAG path."""

from __future__ import annotations

import logging
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
    assert weights["rewrite_variants"] == 1.0


def test_architecture_baseline_locks_the_recall_count_and_mainline():
    """The canonical architecture description must stay aligned with executable routing."""
    baseline = (Path(__file__).resolve().parents[1] / "docs" / "architecture-baseline.md").read_text(encoding="utf-8")

    assert "唯一事实基线" in baseline
    assert "FastAPI 单体主链路" in baseline
    assert "动态 2 至 4 路召回" in baseline
    assert "ES Fallback 不算第五路" in baseline


# ---------------------------------------------------------------------------
# Single-fusion contract
#
# Weighted RRF used to run twice: once in `parallel_recall` with query-aware
# per-path weights, then again in `core/pipeline.py` over the already-fused list
# with a query-independent `w_text` / `w_clip` / `w_ocr` set. The second pass
# reordered results, which partially cancelled the first pass's boosts. These
# tests pin the converged shape: fusion happens in exactly one layer.
# ---------------------------------------------------------------------------


def test_union_dedup_does_not_rescore_or_reorder():
    """The post-fusion step is de-duplication only.

    A descending input must come back in the same order with the same objects
    and the same scores. Any re-scoring here would silently reintroduce a
    second fusion whose weights are not query-aware.
    """
    from core.pipeline import OnlineRAGPipeline

    results = [
        RecallResult(doc_id="low_score_but_first", content="a", score=0.01, source="dense_bge"),
        RecallResult(doc_id="high_score_second", content="b", score=0.99, source="bm25_es"),
        RecallResult(doc_id="middle", content="c", score=0.5, source="clip_visual"),
    ]

    deduped = OnlineRAGPipeline._union_dedup(results)

    assert [r.doc_id for r in deduped] == [r.doc_id for r in results]
    assert [r.score for r in deduped] == [r.score for r in results]
    assert all(a is b for a, b in zip(deduped, results, strict=True))


def test_union_dedup_keeps_the_fused_copy_over_a_later_duplicate():
    """ES Fallback runs after fusion, so it can re-offer an already-fused doc.

    First occurrence wins: the fused entry carries the merged chunk payload and
    the fused ordering, so it must be the one that survives.
    """
    from core.pipeline import OnlineRAGPipeline

    fused = RecallResult(doc_id="doc1", content="merged", score=0.5, source="dense_bge")
    fallback = RecallResult(doc_id="doc1", content="fallback", score=0.02, source="bm25_fallback")
    other = RecallResult(doc_id="doc2", content="x", score=0.4, source="bm25_es")

    deduped = OnlineRAGPipeline._union_dedup([fused, fallback, other])

    assert [r.doc_id for r in deduped] == ["doc1", "doc2"]
    assert deduped[0] is fused


def test_query_aware_weights_survive_the_whole_online_path(monkeypatch):
    """Acceptance for the convergence: nothing downstream may overwrite the boosts.

    Drives the real `ParallelRecallManager.execute()` with the weights the
    pipeline builds, captures what `rrf_fusion` receives, and then runs the
    pipeline's post-fusion step over the fused output. The regulation and visual
    boosts must still be the weights fusion saw.
    """
    import numpy as np

    import retrieval.parallel_recall as recall_module
    from core.pipeline import OnlineRAGPipeline
    from retrieval.parallel_recall import ParallelRecallManager

    captured: dict = {}
    real_fusion = recall_module.rrf_fusion

    def counting_fusion(results_map, k=60, weights=None):
        captured["calls"] = captured.get("calls", 0) + 1
        captured["weights"] = weights
        return real_fusion(results_map, k=k, weights=weights)

    monkeypatch.setattr(recall_module, "rrf_fusion", counting_fusion)

    manager = ParallelRecallManager.__new__(ParallelRecallManager)
    manager._dense_retriever = None
    manager._bm25_retriever = None
    manager._clip_retriever = None
    manager._rewrite_variants_retriever = None
    manager.max_workers = 2

    monkeypatch.setattr(
        ParallelRecallManager,
        "_recall_dense",
        lambda *a, **kw: [RecallResult(doc_id="d1", content="c", score=1.0, source="dense_bge")],
    )
    monkeypatch.setattr(
        ParallelRecallManager,
        "_recall_bm25",
        lambda *a, **kw: [RecallResult(doc_id="d2", content="c", score=1.0, source="bm25_es")],
    )
    monkeypatch.setattr(ParallelRecallManager, "_recall_es_fallback", lambda *a, **kw: [])

    weights = OnlineRAGPipeline._build_rrf_weights(business_type="regulation", is_visual_relevant=True)
    fused, _agreement = manager.execute(
        query="防腐剂限量",
        query_embedding=np.array([0.1]),
        user_role_mask=0,
        user_dept_mask=0,
        use_clip=False,
        clip_top_k=0,
        top_k_per_path={
            "dense_bge": {"enabled": True, "top_k": 5},
            "bm25_es": {"enabled": True, "top_k": 5},
        },
        rrf_weights=weights,
    )

    assert captured["calls"] == 1, "weighted RRF must run exactly once per recall request"
    assert captured["weights"] == weights
    assert captured["weights"]["bm25_es"] == 1.5
    assert captured["weights"]["clip_visual"] == 2.0

    # The post-fusion step must leave that ranking alone.
    assert OnlineRAGPipeline._union_dedup(fused) == fused


def test_pipeline_no_longer_reimplements_rrf():
    """No second re-scoring of an already fused list may live in the pipeline.

    `rrf_score` was the local variable of the removed second fusion pass. Its
    absence is a cheap structural guard against that pass coming back.
    """
    from core.pipeline import OnlineRAGPipeline

    pipeline_source = (Path(__file__).resolve().parents[1] / "core" / "pipeline.py").read_text(encoding="utf-8")

    assert "rrf_score" not in pipeline_source
    assert not hasattr(OnlineRAGPipeline, "_merge_and_dedup")
    assert hasattr(OnlineRAGPipeline, "_union_dedup")


# ---------------------------------------------------------------------------
# Recall-path naming contract
#
# The path name is one string used in three places: the `config.json`
# `retrieval.parallel_paths` topology key, the key registered into
# `path_results`, and the key `_build_rrf_weights` emits. A spelling mismatch is
# silent — `rrf_fusion` falls back to equal weights for an unknown path, and the
# Qdrant-failure diagnostic never fires for a path it cannot name.
# ---------------------------------------------------------------------------


#: path name in config / path_results / weights -> the `_recall_*` method that serves it.
_RECALL_METHOD_BY_PATH = {
    "dense_bge": "_recall_dense",
    "bm25_es": "_recall_bm25",
    "clip_visual": "_recall_clip",
    "rewrite_variants": "_recall_rewrite_variants",
}


def _offline_manager(monkeypatch):
    """A manager with no live backend: every path returns one synthetic document."""
    from retrieval.parallel_recall import ParallelRecallManager

    manager = ParallelRecallManager.__new__(ParallelRecallManager)
    manager._dense_retriever = None
    manager._bm25_retriever = None
    manager._clip_retriever = None
    manager._rewrite_variants_retriever = None
    manager.max_workers = 4

    def _one(path):
        def _recall(*args, **kwargs):
            return [RecallResult(doc_id=path, content="c", score=1.0, source=path)]

        return _recall

    for path, method in _RECALL_METHOD_BY_PATH.items():
        monkeypatch.setattr(ParallelRecallManager, method, _one(path))
    monkeypatch.setattr(ParallelRecallManager, "_recall_es_fallback", lambda *a, **kw: [])
    return manager


def _execute_capturing_path_results(monkeypatch, manager, top_k_per_path):
    """Run the real execute() and return the keys handed to rrf_fusion."""
    import numpy as np

    import retrieval.parallel_recall as recall_module

    captured: dict = {}

    def capture(results_map, k=60, weights=None):
        captured["paths"] = sorted(results_map)
        captured["weights"] = weights
        return []

    monkeypatch.setattr(recall_module, "rrf_fusion", capture)
    manager.execute(
        query="防腐剂限量",
        query_embedding=np.array([0.1]),
        user_role_mask=0,
        user_dept_mask=0,
        use_clip=True,
        clip_top_k=20,
        top_k_per_path=top_k_per_path,
    )
    return captured


def test_config_topology_keys_are_the_recall_path_names(monkeypatch):
    """config.json's parallel_paths keys must be exactly the registered path names."""
    from common.config import get_config_dict
    from core.pipeline import OnlineRAGPipeline

    topology = get_config_dict()["retrieval"]["parallel_paths"]
    manager = _offline_manager(monkeypatch)
    captured = _execute_capturing_path_results(monkeypatch, manager, dict(topology))

    assert captured["paths"] == sorted(topology), (
        "the names registered into path_results drifted from the config topology keys: "
        f"registered={captured['paths']}, config={sorted(topology)}"
    )

    # The same names must be the keys of the query-aware weight mapping, or the
    # boosts computed per query would be dropped at the fusion boundary.
    assert set(OnlineRAGPipeline._build_rrf_weights("general", False)) == set(topology)


def test_rewrite_recall_path_is_named_identically_everywhere():
    """The rewrite path is spelled the same way in config, weights and source.

    It used to be `rewrite_variants` in the config topology and the Qdrant
    diagnostic, but `rewrite_variant` everywhere else, which made the diagnostic
    branch unreachable and left the two spellings to be reconciled by reading.
    """
    from common.config import get_config_dict
    from core.pipeline import OnlineRAGPipeline
    from retrieval.parallel_recall import ParallelRecallManager

    topology = get_config_dict()["retrieval"]["parallel_paths"]
    assert "rewrite_variants" in topology

    source = (Path(__file__).resolve().parents[1] / "retrieval" / "parallel_recall.py").read_text(encoding="utf-8")
    assert '"rewrite_variants"' in source
    assert "rewrite_variants" in OnlineRAGPipeline._build_rrf_weights("general", False)
    assert hasattr(ParallelRecallManager, "_recall_rewrite_variants")


def test_rewrite_path_qdrant_failure_diagnostic_is_reachable(monkeypatch, caplog):
    """A failing Qdrant-backed rewrite path must be named in the warning.

    The path is registered under `rewrite_variants`; the diagnostic matched the
    same spelling, so with the old singular spelling no `rewrite_variants` entry
    ever existed in `path_results` and this warning could not fire.

    The warning must also *not* claim the ES fallback ran. A Qdrant path can
    fail while the surviving paths still return enough documents, in which case
    the fallback condition is false; a message saying "触发 ES Fallback" would
    send an operator looking for a fallback log line that was never written.
    """
    import numpy as np

    from retrieval.parallel_recall import ParallelRecallManager

    manager = ParallelRecallManager.__new__(ParallelRecallManager)
    manager._dense_retriever = None
    manager._bm25_retriever = None
    manager._clip_retriever = None
    manager._rewrite_variants_retriever = None
    manager.max_workers = 2

    def _boom(*args, **kwargs):
        raise RuntimeError("qdrant unavailable")

    monkeypatch.setattr(ParallelRecallManager, "_recall_rewrite_variants", _boom)
    monkeypatch.setattr(ParallelRecallManager, "_recall_es_fallback", lambda *a, **kw: [])

    with caplog.at_level(logging.WARNING, logger="retrieval.parallel_recall"):
        manager.execute(
            query="防腐剂限量",
            query_embedding=np.array([0.1]),
            user_role_mask=0,
            user_dept_mask=0,
            use_clip=False,
            clip_top_k=0,
            top_k_per_path={"rewrite_variants": {"enabled": True, "top_k": 10}},
        )

    warnings = [record.message for record in caplog.records if "Qdrant 路径异常" in record.message]
    assert warnings, (
        "the Qdrant-failure warning is unreachable for the rewrite recall path; "
        f"records={[record.message for record in caplog.records]}"
    )
    assert any("rewrite_variants" in message for message in warnings), (
        f"the warning must name the degraded path, got {warnings}"
    )
    assert not any("触发 ES Fallback" in message for message in warnings), (
        f"the warning must not claim the fallback ran, got {warnings}"
    )


def test_qdrant_failure_does_not_trigger_the_es_fallback(monkeypatch, caplog):
    """The fallback has exactly one trigger: too few unique documents.

    A Qdrant path failing while the others return enough documents degrades
    coverage without starting supplementary recall. Making a failure trigger
    the fallback would be a retrieval-behaviour change, so the invariant is
    asserted rather than altered.
    """
    import numpy as np

    from retrieval.parallel_recall import FALLBACK_MIN_DOC_IDS, ParallelRecallManager

    manager = ParallelRecallManager.__new__(ParallelRecallManager)
    manager._dense_retriever = None
    manager._bm25_retriever = None
    manager._clip_retriever = None
    manager._rewrite_variants_retriever = None
    manager.max_workers = 2

    fallback_calls: list = []

    def _boom(*args, **kwargs):
        raise RuntimeError("qdrant unavailable")

    monkeypatch.setattr(ParallelRecallManager, "_recall_rewrite_variants", _boom)
    monkeypatch.setattr(
        ParallelRecallManager,
        "_recall_bm25",
        lambda *a, **kw: [
            RecallResult(
                doc_id=f"d{index}",
                content="c",
                score=1.0,
                source="bm25_es",
                # The RBAC filter fails closed on missing masks, so the synthetic
                # documents need real ones to survive into the fused set.
                metadata={"role_mask": 0x04, "dept_mask": 0x04},
            )
            for index in range(FALLBACK_MIN_DOC_IDS + 5)
        ],
    )
    monkeypatch.setattr(
        ParallelRecallManager,
        "_recall_es_fallback",
        lambda *a, **kw: fallback_calls.append(1) or [],
    )

    with caplog.at_level(logging.WARNING, logger="retrieval.parallel_recall"):
        results, _agreement = manager.execute(
            query="防腐剂限量",
            query_embedding=np.array([0.1]),
            user_role_mask=0x04,
            user_dept_mask=0x04,
            use_clip=False,
            clip_top_k=0,
            top_k_per_path={
                "bm25_es": {"enabled": True, "top_k": 100},
                "rewrite_variants": {"enabled": True, "top_k": 10},
            },
        )

    assert len({r.doc_id for r in results}) >= FALLBACK_MIN_DOC_IDS, (
        "the surviving path must clear the fallback threshold for this test to mean anything"
    )
    assert fallback_calls == [], "a failing Qdrant path must not independently start the ES fallback"
    assert any("rewrite_variants" in record.message for record in caplog.records)
