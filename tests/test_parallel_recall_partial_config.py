"""Regression coverage for partial parallel-recall path configuration.

A caller may override only one path.  The execute() guards already treat missing
entries as optional; this test pins the matching runtime contract so later direct
indexing cannot turn an omitted path into a KeyError.
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


def test_partial_path_config_is_merged_with_canonical_defaults():
    manager = _manager()
    observed: dict[str, int] = {}

    def dense(_embedding, _filter, top_k):
        observed["dense_bge"] = top_k
        return []

    def bm25(_query, _role, _dept, top_k):
        observed["bm25_es"] = top_k
        return []

    def rewrite(_query, _embedding, _filter, top_k):
        observed["rewrite_variants"] = top_k
        return []

    manager._recall_dense = dense
    manager._recall_bm25 = bm25
    manager._recall_rewrite_variants = rewrite
    manager._recall_es_fallback = lambda *args, **kwargs: []

    results, agreement = manager.execute(
        query="法规限量查询",
        query_embedding=np.array([0.1]),
        user_role_mask=0,
        user_dept_mask=0,
        use_clip=False,
        top_k_per_path={"dense_bge": {"enabled": True, "top_k": 7}},
    )

    assert results == []
    assert isinstance(agreement, float)
    assert observed["dense_bge"] == 7
    # Missing paths inherit the canonical config instead of raising KeyError.
    assert observed["bm25_es"] == 50
    assert observed["rewrite_variants"] == 30


def test_partial_override_can_disable_one_path_without_removing_others():
    manager = _manager()
    called: list[str] = []

    manager._recall_dense = lambda *args, **kwargs: called.append("dense_bge") or []
    manager._recall_bm25 = lambda *args, **kwargs: called.append("bm25_es") or []
    manager._recall_rewrite_variants = lambda *args, **kwargs: called.append("rewrite_variants") or []
    manager._recall_es_fallback = lambda *args, **kwargs: []

    manager.execute(
        query="产品成分查询",
        query_embedding=np.array([0.1]),
        user_role_mask=0,
        user_dept_mask=0,
        use_clip=False,
        top_k_per_path={"bm25_es": {"enabled": False}},
    )

    assert "bm25_es" not in called
    assert "dense_bge" in called
    assert "rewrite_variants" in called
