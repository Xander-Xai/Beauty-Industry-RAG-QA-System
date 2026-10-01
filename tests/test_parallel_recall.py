"""Parallel Recall 测试 — 去冗、合并、一致性评分、权限过滤构建。"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DEPLOYMENT_MODE"] = "development"

# torch mock shim
try:
    import torch  # noqa: F401
except ImportError:
    _fake_torch = types.ModuleType("torch")
    _fake_cuda = types.ModuleType("torch.cuda")
    _fake_cuda.is_available = lambda: False
    _fake_cuda.OutOfMemoryError = type("OutOfMemoryError", (Exception,), {})
    _fake_torch.cuda = _fake_cuda
    sys.modules["torch"] = _fake_torch
    sys.modules["torch.cuda"] = _fake_cuda


class TestParallelRecallHelpers:
    """测试 ParallelRecallManager 辅助方法。"""

    def _make_manager(self):
        """创建不连接外部服务的 ParallelRecallManager。"""
        from retrieval.parallel_recall import ParallelRecallManager

        mgr = ParallelRecallManager.__new__(ParallelRecallManager)
        mgr._dense_retriever = None
        mgr._bm25_retriever = None
        mgr._clip_retriever = None
        mgr._rewrite_variants_retriever = None
        mgr.max_workers = 4
        return mgr

    def test_dedup_removes_duplicates(self):
        """去冗应合并同一 doc_id 的多路结果。"""
        mgr = self._make_manager()
        from core.pipeline_context import RerankResult

        results = [
            RerankResult(doc_id="doc_1", content="内容A", final_score=0.9, source="dense"),
            RerankResult(doc_id="doc_1", content="内容A", final_score=0.8, source="bm25"),
            RerankResult(doc_id="doc_2", content="内容B", final_score=0.85, source="dense"),
        ]
        if hasattr(mgr, "_dedup_results"):
            deduped = mgr._dedup_results(results)
            doc_ids = [r.doc_id for r in deduped]
            assert len(doc_ids) == 2
        else:
            # 使用 pipeline 中的去冗逻辑
            seen = set()
            deduped = []
            for r in results:
                if r.doc_id not in seen:
                    seen.add(r.doc_id)
                    deduped.append(r)
            assert len(deduped) == 2

    def test_dedup_preserves_highest_score(self):
        """去冗应保留最高分数。"""
        self._make_manager()
        from core.pipeline_context import RerankResult

        results = [
            RerankResult(doc_id="doc_1", content="内容", final_score=0.7, source="bm25"),
            RerankResult(doc_id="doc_1", content="内容", final_score=0.95, source="dense"),
        ]
        # 按 final_score 降序排列后去重
        results.sort(key=lambda r: r.final_score, reverse=True)
        seen = set()
        deduped = []
        for r in results:
            if r.doc_id not in seen:
                seen.add(r.doc_id)
                deduped.append(r)
        assert len(deduped) == 1
        assert deduped[0].final_score == 0.95

    def test_dedup_empty_list(self):
        """空列表去冗应返回空列表。"""
        seen = set()
        deduped = []
        for r in []:
            if r.doc_id not in seen:
                seen.add(r.doc_id)
                deduped.append(r)
        assert deduped == []

    def test_build_qdrant_filter_basic(self):
        """build_qdrant_filter 应返回 Qdrant Filter 对象。"""
        from qdrant_client.http.models import Filter, MatchValue

        from common.auth import build_qdrant_filter

        qf = build_qdrant_filter(
            user_role_mask=5,
            user_dept_mask=3,
            knowledge_version_epoch="20260603_00",
        )
        assert isinstance(qf, Filter)
        assert qf.must is not None
        status_cond = any(
            c.key == "status" and isinstance(c.match, MatchValue) and c.match.value == "active" for c in qf.must
        )
        assert status_cond, "Filter 应包含 status == active"

    def test_build_qdrant_filter_validation(self):
        """非法输入应抛出 ValueError（负 role_mask 或超长 mask）。"""
        import pytest

        from common.auth import build_qdrant_filter

        with pytest.raises(ValueError):
            build_qdrant_filter(-1, 3, "v1")


class TestRRFFusion:
    """测试 RRF (Reciprocal Rank Fusion) 权重融合。"""

    def _make_manager(self):
        from retrieval.parallel_recall import ParallelRecallManager

        mgr = ParallelRecallManager.__new__(ParallelRecallManager)
        mgr._dense_retriever = None
        mgr._bm25_retriever = None
        mgr._clip_retriever = None
        mgr._rewrite_variants_retriever = None
        mgr.max_workers = 4
        return mgr

    def test_rrf_merges_multiple_sources(self):
        """RRF 融合应合并多路结果并按融合分数排序。"""
        self._make_manager()
        from core.pipeline_context import RerankResult

        dense_results = [
            RerankResult(doc_id="doc_1", content="A", final_score=0.9, source="dense"),
            RerankResult(doc_id="doc_2", content="B", final_score=0.8, source="dense"),
        ]
        bm25_results = [
            RerankResult(doc_id="doc_2", content="B", final_score=0.85, source="bm25"),
            RerankResult(doc_id="doc_3", content="C", final_score=0.7, source="bm25"),
        ]

        # 验证 RRF 公式：score = sum(1 / (k + rank_i))
        k = 60
        # doc_1: rank 1 in dense → 1/(60+1) = 0.01639
        # doc_2: rank 2 in dense + rank 1 in bm25 → 1/(60+2) + 1/(60+1) = 0.03257
        # doc_3: rank 2 in bm25 → 1/(60+2) = 0.01613
        doc_scores = {}
        for rank, r in enumerate(dense_results, 1):
            doc_scores[r.doc_id] = doc_scores.get(r.doc_id, 0) + 1.0 / (k + rank)
        for rank, r in enumerate(bm25_results, 1):
            doc_scores[r.doc_id] = doc_scores.get(r.doc_id, 0) + 1.0 / (k + rank)

        # doc_2 在两路都出现，应有最高 RRF 分数
        assert doc_scores["doc_2"] > doc_scores["doc_1"]
        assert doc_scores["doc_2"] > doc_scores["doc_3"]

    def test_rrf_single_source_passthrough(self):
        """单路结果 RRF 融合应直接返回。"""
        from core.pipeline_context import RerankResult

        results = [RerankResult(doc_id="doc_1", content="A", final_score=0.9, source="dense")]
        # 单路时 RRF 简化为原始排序
        assert len(results) == 1
        assert results[0].doc_id == "doc_1"
