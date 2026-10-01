"""
test_retrieval_pipeline.py — 新增测试: ParallelRecallManager.execute(), agreement score,
AnswerGate contradiction detection, CrossEncoderEnsemble reranking, CLIP timeout.

侧重端到端流程与 mock 外部依赖（Qdrant/ES/Redis/CLIP）。
"""

import os
import sys
import types
from unittest.mock import MagicMock, patch

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

import numpy as np

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_recall_result(doc_id, content, score, source="dense_bge"):
    from core.pipeline_context import RecallResult

    return RecallResult(doc_id=doc_id, content=content, score=score, source=source)


def _make_rerank_result(doc_id, content, source="dense_bge", ce_a=0.0, ce_b=0.0, ensemble=0.0):
    from core.pipeline_context import RerankResult

    r = RerankResult(doc_id=doc_id, content=content, source=source)
    r.ce_score_a = ce_a
    r.ce_score_b = ce_b
    r.ce_score_ensemble = ensemble
    return r


# ===========================================================================
# 1. ParallelRecallManager — execute() with mocked retrievers
# ===========================================================================


class TestParallelRecallExecute:
    """ParallelRecallManager.execute() 并行多路召回集成测试。"""

    def _make_manager(self):
        from retrieval.parallel_recall import ParallelRecallManager

        mgr = ParallelRecallManager.__new__(ParallelRecallManager)
        mgr._dense_retriever = MagicMock()
        mgr._bm25_retriever = MagicMock()
        mgr._clip_retriever = MagicMock()
        mgr._rewrite_variants_retriever = None
        mgr.max_workers = 4
        return mgr

    def _default_top_k(self):
        return {
            "dense_bge": {"enabled": True, "top_k": 50},
            "bm25_es": {"enabled": True, "top_k": 50},
            "clip_visual": {"enabled": True, "top_k": 20},
            "rewrite_variants": {"enabled": False, "top_k": 30},
        }

    @patch("auth.bitmask_rbac.build_qdrant_filter", return_value=None)
    @patch("retrieval.parallel_recall.log_audit_event")
    @patch("retrieval.parallel_recall.get_config_dict")
    def test_execute_returns_merged_results(self, mock_config, mock_audit, mock_filter):
        """execute() 应合并多路召回结果并返回 (results, agreement_score)。"""
        mock_config.return_value = {
            "retrieval": {"parallel_paths": self._default_top_k()},
            "knowledge_version_epoch": "20260607_00",
        }

        mgr = self._make_manager()
        # mock dense retriever
        mgr._dense_retriever.search.return_value = [
            {"doc_id": "d1", "content": "c1", "score": 0.9},
            {"doc_id": "d2", "content": "c2", "score": 0.8},
        ]
        # mock bm25 retriever
        mgr._bm25_retriever.search.return_value = [
            {"doc_id": "d2", "content": "c2", "score": 0.85},
            {"doc_id": "d3", "content": "c3", "score": 0.7},
        ]
        # mock clip retriever (disable to simplify — use_clip=False)

        query_emb = np.random.rand(768).astype(np.float32)
        results, score = mgr.execute(
            query="烟酰胺浓度",
            query_embedding=query_emb,
            user_role_mask=0xFF,
            user_dept_mask=0x0F,
            use_clip=False,
            top_k_per_path=self._default_top_k(),
        )

        assert len(results) >= 3  # d1, d2, d3
        doc_ids = {r.doc_id for r in results}
        assert "d1" in doc_ids
        assert "d2" in doc_ids
        assert "d3" in doc_ids
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    @patch("auth.bitmask_rbac.build_qdrant_filter", return_value=None)
    @patch("retrieval.parallel_recall.log_audit_event")
    @patch("retrieval.parallel_recall.get_config_dict")
    def test_execute_graceful_on_retriever_failure(self, mock_config, mock_audit, mock_filter):
        """单路召回失败不应阻断其它路径。"""
        mock_config.return_value = {
            "retrieval": {"parallel_paths": self._default_top_k()},
            "knowledge_version_epoch": "20260607_00",
        }

        mgr = self._make_manager()
        mgr._dense_retriever.search.side_effect = ConnectionError("Qdrant 宕机")
        mgr._bm25_retriever.search.return_value = [
            {"doc_id": "d1", "content": "c1", "score": 0.9},
        ]

        query_emb = np.random.rand(768).astype(np.float32)
        results, score = mgr.execute(
            query="test",
            query_embedding=query_emb,
            user_role_mask=0,
            user_dept_mask=0,
            use_clip=False,
            top_k_per_path=self._default_top_k(),
        )
        # bm25 路应返回结果
        assert len(results) >= 1

    @patch("auth.bitmask_rbac.build_qdrant_filter", return_value=None)
    @patch("retrieval.parallel_recall.log_audit_event")
    @patch("retrieval.parallel_recall.get_config_dict")
    def test_execute_clip_timeout_returns_empty(self, mock_config, mock_audit, mock_filter):
        """CLIP 超时时应丢弃 CLIP 分支，不影响主召回。"""
        top_k = self._default_top_k()
        top_k["clip_visual"]["enabled"] = True
        mock_config.return_value = {
            "retrieval": {"parallel_paths": top_k},
            "knowledge_version_epoch": "20260607_00",
            "clip_sync": {"timeout_ms": 50},
        }

        mgr = self._make_manager()
        mgr._dense_retriever.search.return_value = [
            {"doc_id": "d1", "content": "c1", "score": 0.9},
        ]
        # CLIP retriever 不可用
        mgr._clip_retriever.search.side_effect = Exception("GPU timeout")

        query_emb = np.random.rand(768).astype(np.float32)
        results, score = mgr.execute(
            query="test",
            query_embedding=query_emb,
            user_role_mask=0,
            user_dept_mask=0,
            use_clip=True,
            clip_top_k=20,
            top_k_per_path=top_k,
        )
        # 即使 CLIP 失败，dense 路仍应返回
        assert any(r.doc_id == "d1" for r in results)


# ===========================================================================
# 2. Agreement Score Computation
# ===========================================================================


class TestAgreementScore:
    """_compute_agreement_score() 一致性评分测试。"""

    def _make_manager(self):
        from retrieval.parallel_recall import ParallelRecallManager

        mgr = ParallelRecallManager.__new__(ParallelRecallManager)
        mgr._dense_retriever = MagicMock()
        mgr._bm25_retriever = MagicMock()
        mgr._clip_retriever = MagicMock()
        mgr._rewrite_variants_retriever = None
        mgr.max_workers = 4
        return mgr

    def test_high_agreement_identical_docs(self):
        """相同结果的多路召回应有高一致性。"""
        mgr = self._make_manager()
        from core.pipeline_context import RecallResult

        shared = [
            RecallResult(doc_id="d1", content="c1", score=0.9, source="dense"),
            RecallResult(doc_id="d2", content="c2", score=0.8, source="dense"),
            RecallResult(doc_id="d3", content="c3", score=0.7, source="dense"),
        ]
        path_results = {
            "dense_bge": list(shared),
            "bm25_es": list(shared),
        }
        score = mgr._compute_agreement_score(path_results)
        # 完全相同 → Jaccard = 1.0
        assert score > 0.5

    def test_low_agreement_disjoint_docs(self):
        """完全不同的多路召回应有低一致性。"""
        mgr = self._make_manager()
        from core.pipeline_context import RecallResult

        path_results = {
            "dense_bge": [
                RecallResult(doc_id="d1", content="c1", score=0.9, source="dense"),
                RecallResult(doc_id="d2", content="c2", score=0.8, source="dense"),
            ],
            "bm25_es": [
                RecallResult(doc_id="d9", content="c9", score=0.7, source="bm25"),
                RecallResult(doc_id="d10", content="c10", score=0.6, source="bm25"),
            ],
        }
        score = mgr._compute_agreement_score(path_results)
        # 完全不同 → Jaccard = 0.0
        assert score < 0.5

    def test_single_path_agreement(self):
        """单路召回 Jaccard 为 0，但聚类可能给默认分。"""
        mgr = self._make_manager()
        from core.pipeline_context import RecallResult

        path_results = {
            "dense_bge": [
                RecallResult(doc_id="d1", content="c1", score=0.9, source="dense"),
            ],
        }
        score = mgr._compute_agreement_score(path_results)
        assert 0.0 <= score <= 1.0

    def test_partial_agreement(self):
        """部分重叠的召回应有中等一致性。"""
        mgr = self._make_manager()
        from core.pipeline_context import RecallResult

        path_results = {
            "dense_bge": [
                RecallResult(doc_id="d1", content="c1", score=0.9, source="dense"),
                RecallResult(doc_id="d2", content="c2", score=0.8, source="dense"),
                RecallResult(doc_id="d3", content="c3", score=0.7, source="dense"),
            ],
            "bm25_es": [
                RecallResult(doc_id="d2", content="c2", score=0.85, source="bm25"),
                RecallResult(doc_id="d3", content="c3", score=0.75, source="bm25"),
                RecallResult(doc_id="d4", content="c4", score=0.6, source="bm25"),
            ],
        }
        score = mgr._compute_agreement_score(path_results)
        # 下限 = 1-of-3 聚类分布（clustering_score → 0） + Jaccard 0.5 的 30% 贡献 = 0.15
        assert 0.14 < score < 0.9


# ===========================================================================
# 3. AnswerGate Contradiction Detection
# ===========================================================================


class TestAnswerGate:
    """AnswerGate 矛盾检测测试。"""

    def _make_gate(self, nli_threshold=0.8):
        from retrieval.answer_gate import AnswerGate

        gate = AnswerGate.__new__(AnswerGate)
        gate.nli_threshold = nli_threshold
        gate._nli_model = None
        gate._nli_tokenizer = None
        gate._use_nli = False
        return gate

    def test_verify_no_doc_returns_pass(self):
        """无文档时应直接通过。"""
        gate = self._make_gate()
        result = gate.verify(answer="测试答案", top_doc=None, is_regulation=False)
        assert result.passed is True
        assert result.nli_contradiction_score == 0.0

    def test_verify_high_jaccard_fast_pass(self):
        """高 Jaccard 相似度应快速通过（跳过 NLI 模型）。"""
        gate = self._make_gate()
        from core.pipeline_context import RerankResult

        # 相同内容 → Jaccard 接近 1.0
        doc = RerankResult(doc_id="d1", content="烟酰胺浓度推荐2-5%")
        result = gate.verify(answer="烟酰胺浓度推荐2-5%", top_doc=doc)
        assert result.passed is True
        assert result.warning is False

    def test_verify_low_jaccard_regulation_forced_reject(self):
        """法规类低相似度应强制拒答。"""
        gate = self._make_gate()
        from core.pipeline_context import RerankResult

        doc = RerankResult(doc_id="d1", content="关于化妆品安全技术规范中铅限量标准")
        result = gate.verify(
            answer="完全不同的回答内容xyz",
            top_doc=doc,
            is_regulation=True,
        )
        # 低 Jaccard (< 0.15) + is_regulation → passed=False
        assert result.passed is False
        assert result.warning is True

    def test_verify_low_jaccard_non_regulation_warning(self):
        """非法规类低相似度应标记 warning 但通过。"""
        gate = self._make_gate()
        from core.pipeline_context import RerankResult

        doc = RerankResult(doc_id="d1", content="关于化妆品安全技术规范中铅限量标准")
        result = gate.verify(
            answer="完全不同的回答内容xyz",
            top_doc=doc,
            is_regulation=False,
        )
        assert result.warning is True
        assert result.passed is True  # 非法规类不强制拒答

    def test_jaccard_similarity_identical_texts(self):
        """相同文本 Jaccard 应为 1.0。"""
        gate = self._make_gate()
        score = gate._jaccard_similarity("hello world", "hello world")
        assert score == 1.0

    def test_jaccard_similarity_empty_texts(self):
        """空文本 Jaccard 应为 0.0。"""
        gate = self._make_gate()
        assert gate._jaccard_similarity("", "test") == 0.0
        assert gate._jaccard_similarity("test", "") == 0.0
        assert gate._jaccard_similarity("", "") == 0.0

    def test_batch_verify_returns_scores(self):
        """batch_verify 应返回每个文档的矛盾/蕴含分数。"""
        gate = self._make_gate()
        from core.pipeline_context import RerankResult

        docs = [
            RerankResult(doc_id="d1", content="烟酰胺是一种维生素"),
            RerankResult(doc_id="d2", content="透明质酸保湿"),
        ]
        results = gate.batch_verify(answer="烟酰胺的浓度", top_docs=docs)
        assert len(results) == 2
        for r in results:
            assert "doc_id" in r
            assert "contradiction" in r
            assert "entailment" in r

    def test_batch_verify_empty_docs(self):
        """空文档列表 batch_verify 应返回空。"""
        gate = self._make_gate()
        assert gate.batch_verify(answer="test", top_docs=[]) == []


# ===========================================================================
# 4. CrossEncoderEnsemble Reranking
# ===========================================================================


class TestCrossEncoderEnsemble:
    """CrossEncoderEnsemble 重排测试。"""

    def _make_ensemble(self):
        from retrieval.cross_encoder_ensemble import CrossEncoderEnsemble

        ce = CrossEncoderEnsemble.__new__(CrossEncoderEnsemble)
        ce._ce_a = MagicMock()
        ce._ce_b = MagicMock()
        ce._batch_aggregator = MagicMock()
        from retrieval.cross_encoder_ensemble import PlattScaler

        ce._platt_scaler = PlattScaler.__new__(PlattScaler)
        ce._platt_scaler._calibrators = {
            "ce_a": {"a": -1.0, "b": 0.0},
            "ce_b": {"a": -1.0, "b": 0.0},
            "ensemble": {"a": -1.0, "b": 0.0},
        }
        return ce

    def test_rerank_returns_sorted_top_k(self):
        """rerank 应返回按 ce_score_ensemble 降序排列的 top_k 结果。"""
        ce = self._make_ensemble()
        ce._batch_aggregator.batch_predict.side_effect = [
            [0.9, 0.3, 0.7],  # ce_a scores
            [0.8, 0.5, 0.6],  # ce_b scores
        ]
        candidates = [
            _make_rerank_result("d1", "内容1", "dense", ce_a=0.0, ce_b=0.0, ensemble=0.0),
            _make_rerank_result("d2", "内容2", "bm25", ce_a=0.0, ce_b=0.0, ensemble=0.0),
            _make_rerank_result("d3", "内容3", "dense", ce_a=0.0, ce_b=0.0, ensemble=0.0),
        ]

        results = ce.rerank(query="测试查询", candidates=candidates, top_k=2)

        assert len(results) == 2
        # 验证排序：最高分在前
        assert results[0].ce_score_ensemble >= results[1].ce_score_ensemble

    def test_rerank_empty_candidates(self):
        """空候选列表应返回空列表。"""
        ce = self._make_ensemble()
        results = ce.rerank(query="test", candidates=[], top_k=10)
        assert results == []

    def test_rerank_fallback_on_error(self):
        """模型异常时应降级返回 BiEncoder 排序。"""
        ce = self._make_ensemble()
        ce._batch_aggregator.batch_predict.side_effect = RuntimeError("GPU OOM")
        candidates = [
            _make_rerank_result("d1", "c1", "dense", ensemble=0.0),
            _make_rerank_result("d2", "c2", "dense", ensemble=0.0),
        ]
        results = ce.rerank(query="test", candidates=candidates, top_k=2)
        assert len(results) == 2  # 降级保留原始列表


# ===========================================================================
# 5. PlattScaler
# ===========================================================================


class TestPlattScaler:
    """Platt Scaling 校准器测试。"""

    def _make_scaler(self):
        from retrieval.cross_encoder_ensemble import PlattScaler

        s = PlattScaler.__new__(PlattScaler)
        s._calibrators = {
            "ce_a": {"a": -1.0, "b": 0.0},
            "ce_b": {"a": -1.0, "b": 0.0},
            "ensemble": {"a": -1.0, "b": 0.0},
        }
        return s

    def test_calibrate_maps_to_probability_range(self):
        """校准后分数应在 [0, 1] 范围内。"""
        s = self._make_scaler()
        for raw_score in [-10.0, -5.0, 0.0, 5.0, 10.0]:
            p = s.calibrate(raw_score, "ensemble")
            assert 0.0 <= p <= 1.0, f"Score {p} out of range for raw={raw_score}"

    def test_calibrate_high_raw_score_approaches_one(self):
        """高原始分（负方向，因 a<0）应映射为接近 1 的概率。"""
        s = self._make_scaler()
        p = s.calibrate(-10.0, "ensemble")
        assert p > 0.9

    def test_calibrate_low_raw_score_approaches_zero(self):
        """低原始分应映射为接近 0 的概率。"""
        s = self._make_scaler()
        p = s.calibrate(10.0, "ensemble")
        assert p < 0.1

    def test_calibrate_batch_length_matches_input(self):
        """批量校准输出长度应与输入一致。"""
        s = self._make_scaler()
        scores = [0.1, 0.5, 0.9, 1.5]
        result = s.calibrate_batch(scores, "ensemble")
        assert len(result) == len(scores)

    def test_update_params_changes_behavior(self):
        """update_params 应改变后续校准行为。"""
        s = self._make_scaler()
        p_before = s.calibrate(0.0, "ensemble")
        s.update_params("ensemble", a=0.0, b=1.0)
        p_after = s.calibrate(0.0, "ensemble")
        assert p_after != p_before
