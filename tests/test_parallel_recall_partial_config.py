"""Regression coverage for request-scoped parallel-recall path configuration.

The online pipeline deliberately passes a reduced mapping for simple queries. An
omitted path therefore means "disabled for this request", while None means
"use the canonical full topology".
"""

from __future__ import annotations

import numpy as np


def _manager():
    from retrieval.parallel_recall import ParallelRecallManager

    manager = ParallelRecallManager.__new__(ParallelRecallManager)
    manager._dense_retriever = None
    manager._bm25_retriever = None
    manager._clip_retriever = None
    manager._rewrite_variants_retriever = None
    manager.max_workers = 2
    return manager


def test_reduced_path_mapping_preserves_simple_query_topology():
    """A BGE+BM25 request must not silently restore rewrite or CLIP."""
    manager = _manager()
    called: list[str] = []
    observed: dict[str, int] = {}

    def dense(_embedding, _filter, top_k):
        called.append("dense_bge")
        observed["dense_bge"] = top_k
        return []

    def bm25(_query, _role, _dept, top_k):
        called.append("bm25_es")
        observed["bm25_es"] = top_k
        return []

    manager._recall_dense = dense
    manager._recall_bm25 = bm25
    manager._recall_clip = lambda *args, **kwargs: called.append("clip_visual") or []
    manager._recall_rewrite_variants = lambda *args, **kwargs: called.append("rewrite_variants") or []
    manager._recall_es_fallback = lambda *args, **kwargs: []

    results, agreement = manager.execute(
        query="法规限量查询",
        query_embedding=np.array([0.1]),
        user_role_mask=0,
        user_dept_mask=0,
        use_clip=True,
        clip_top_k=20,
        top_k_per_path={
            "dense_bge": {"enabled": True, "top_k": 7},
            "bm25_es": {"enabled": True, "top_k": 11},
        },
    )

    assert results == []
    assert isinstance(agreement, float)
    assert called.count("dense_bge") == 1
    assert called.count("bm25_es") == 1
    assert "clip_visual" not in called
    assert "rewrite_variants" not in called
    assert observed == {"dense_bge": 7, "bm25_es": 11}


def test_present_empty_path_config_uses_path_default_top_k():
    """Presence enables a path by default; only omission disables it."""
    manager = _manager()
    observed: dict[str, int] = {}

    def dense(_embedding, _filter, top_k):
        observed["dense_bge"] = top_k
        return []

    manager._recall_dense = dense
    manager._recall_es_fallback = lambda *args, **kwargs: []

    manager.execute(
        query="产品成分查询",
        query_embedding=np.array([0.1]),
        user_role_mask=0,
        user_dept_mask=0,
        use_clip=False,
        top_k_per_path={"dense_bge": {}},
    )

    assert observed["dense_bge"] == 50


def test_explicit_disabled_path_is_not_called():
    manager = _manager()
    called: list[str] = []

    manager._recall_dense = lambda *args, **kwargs: called.append("dense_bge") or []
    manager._recall_bm25 = lambda *args, **kwargs: called.append("bm25_es") or []
    manager._recall_es_fallback = lambda *args, **kwargs: []

    manager.execute(
        query="产品成分查询",
        query_embedding=np.array([0.1]),
        user_role_mask=0,
        user_dept_mask=0,
        use_clip=False,
        top_k_per_path={
            "dense_bge": {"enabled": True, "top_k": 5},
            "bm25_es": {"enabled": False, "top_k": 50},
        },
    )

    assert "dense_bge" in called
    assert "bm25_es" not in called
