"""BiEncoderReranker 单元测试 — 专用模型路径与回退逻辑"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DEPLOYMENT_MODE"] = "development"

import numpy as np
import pytest

from common.models import RecallResult, RerankResult


def _make_recall(doc_id: str, content: str = "content", score: float = 1.0) -> RecallResult:
    return RecallResult(doc_id=doc_id, content=content, score=score, source="test")


def _make_rerank(doc_id: str, bi_score: float = 0.0) -> RerankResult:
    return RerankResult(doc_id=doc_id, content="content", bi_score=bi_score)


# ---------------------------------------------------------------------------
# Fixtures: patch get_config_dict before BiEncoderReranker is imported
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config_both_paths_differ():
    """gpu1.bi_encoder.model_path != embedding.text.model_path → dedicated available"""
    from retrieval_service.rerank.bi_encoder import BiEncoderReranker

    # patch on the instance
    inst = BiEncoderReranker()
    inst._bi_encoder_model_path = "/models/dedicated-bi-encoder"
    inst._dedicated_model = None
    return inst


@pytest.fixture
def mock_config_no_bi_encoder_path():
    """No bi_encoder config → no dedicated model"""
    from retrieval_service.rerank.bi_encoder import BiEncoderReranker

    inst = BiEncoderReranker()
    inst._bi_encoder_model_path = None
    inst._dedicated_model = None
    return inst


@pytest.fixture
def mock_config_same_path():
    """bi_encoder path == embedding path → not dedicated (same model)"""
    from retrieval_service.rerank.bi_encoder import BiEncoderReranker

    inst = BiEncoderReranker()
    inst._bi_encoder_model_path = "/models/shared-bge"
    inst._dedicated_model = None
    return inst


@pytest.fixture
def mock_config_dedicated_loaded():
    """dedicated model already loaded in memory"""
    from retrieval_service.rerank.bi_encoder import BiEncoderReranker

    inst = BiEncoderReranker()
    inst._bi_encoder_model_path = "/models/dedicated-bi-encoder"
    inst._dedicated_model = MagicMock()
    return inst


class TestBiEncoderInit:
    """BiEncoderReranker 初始化"""

    def test_init_attributes(self):
        """初始化后属性应为 None / 从 config 读取"""
        from retrieval_service.rerank.bi_encoder import BiEncoderReranker

        inst = BiEncoderReranker()
        assert hasattr(inst, "_bi_encoder_model_path")
        assert hasattr(inst, "_dedicated_model")
        assert hasattr(inst, "_embedding_service")
        assert inst._dedicated_model is None


class TestDedicatedModelAvailable:
    """dedicated_model_available 属性"""

    def test_available_when_model_already_loaded(self, mock_config_dedicated_loaded):
        """_dedicated_model 已加载时返回 True"""
        assert mock_config_dedicated_loaded.dedicated_model_available is True

    def test_available_when_path_differs_from_embedding(self):
        """路径与 embedding 不同时返回 True"""
        from retrieval_service.rerank.bi_encoder import BiEncoderReranker

        inst = BiEncoderReranker()
        inst._bi_encoder_model_path = "/models/dedicated-bi-encoder"
        inst._dedicated_model = None
        with patch("retrieval_service.rerank.bi_encoder.config") as mock_cfg:
            # config.get("embedding", {}).get("text", {}).get("model_path", "")
            mock_cfg.get.side_effect = lambda key, default=None: (
                {"text": {"model_path": "/models/shared-bge"}} if key == "embedding" else default
            )
            assert inst.dedicated_model_available is True

    def test_not_available_when_no_path(self, mock_config_no_bi_encoder_path):
        """无 bi_encoder 配置时返回 False"""
        assert mock_config_no_bi_encoder_path.dedicated_model_available is False

    def test_not_available_when_path_empty_string(self):
        """路径为空字符串时返回 False"""
        from retrieval_service.rerank.bi_encoder import BiEncoderReranker

        inst = BiEncoderReranker()
        inst._bi_encoder_model_path = ""
        assert inst.dedicated_model_available is False


class TestRerankEmptyCandidates:
    """空候选项处理"""

    def test_empty_candidates_returns_empty_list(self, mock_config_both_paths_differ):
        """rerank 传入空列表应返回空列表"""
        result = mock_config_both_paths_differ.rerank("query", [], top_k=10)
        assert result == []


class TestRerankViaDedicatedModel:
    """通过专用 BiEncoder 模型重排序"""

    @pytest.fixture
    def reranker_with_mock_dedicated(self):
        from retrieval_service.rerank.bi_encoder import BiEncoderReranker

        inst = BiEncoderReranker()
        inst._bi_encoder_model_path = "/models/dedicated"
        inst._dedicated_model = MagicMock()
        # Mock encode to return controlled embeddings
        inst._dedicated_model.encode.side_effect = lambda texts, **kw: (
            np.array([[1.0, 0.0]]) if isinstance(texts, str) else np.array([[1.0, 0.0], [0.0, 1.0]])
        )
        return inst

    def test_dedicated_model_rerank_returns_top_k(self, reranker_with_mock_dedicated):
        """使用专用模型重排应返回 top_k 结果"""
        candidates = [_make_recall("a"), _make_recall("b")]
        results = reranker_with_mock_dedicated.rerank("query", candidates, top_k=1)
        assert len(results) == 1

    def test_dedicated_model_rerank_returns_rerank_result(self, reranker_with_mock_dedicated):
        """返回的结果应为 RerankResult 类型"""
        candidates = [_make_recall("a")]
        results = reranker_with_mock_dedicated.rerank("query", candidates, top_k=5)
        assert isinstance(results[0], RerankResult)

    def test_dedicated_model_rerank_sorted_descending(self, reranker_with_mock_dedicated):
        """结果应按 bi_score 降序排列"""
        candidates = [_make_recall("a"), _make_recall("b")]
        results = reranker_with_mock_dedicated.rerank("query", candidates, top_k=5)
        scores = [r.bi_score for r in results]
        assert all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1))


class TestRerankFallback:
    """回退到 EmbeddingService"""

    @pytest.fixture
    def reranker_no_dedicated(self):
        from retrieval_service.rerank.bi_encoder import BiEncoderReranker

        inst = BiEncoderReranker()
        inst._bi_encoder_model_path = None
        inst._dedicated_model = None
        # Mock embedding_service
        mock_emb = MagicMock()
        mock_emb.encode_text.return_value.flatten.return_value = np.array([1.0, 0.0])
        mock_emb.encode_texts_batch.return_value = np.array([[1.0, 0.0], [0.0, 1.0]])
        inst._embedding_service = mock_emb
        return inst

    def test_fallback_embedding_service(self, reranker_no_dedicated):
        """无专用模型时使用 EmbeddingService"""
        candidates = [_make_recall("a"), _make_recall("b")]
        results = reranker_no_dedicated.rerank("query", candidates, top_k=5)
        assert len(results) == 2

    def test_fallback_returns_top_k(self, reranker_no_dedicated):
        """EmbeddingService 回退应返回 top_k 结果"""
        candidates = [_make_recall("a"), _make_recall("b")]
        results = reranker_no_dedicated.rerank("query", candidates, top_k=1)
        assert len(results) == 1

    def test_dedicated_fails_then_fallback(self):
        """专用模型失败应回退到 EmbeddingService"""
        from retrieval_service.rerank.bi_encoder import BiEncoderReranker

        inst = BiEncoderReranker()
        inst._bi_encoder_model_path = "/models/failing-model"
        # Dedicated model exists but raises
        inst._dedicated_model = MagicMock()
        inst._dedicated_model.encode.side_effect = RuntimeError("OOM")

        # Mock EmbeddingService fallback
        mock_emb = MagicMock()
        mock_emb.encode_text.return_value.flatten.return_value = np.array([1.0, 0.0])
        mock_emb.encode_texts_batch.return_value = np.array([[1.0, 0.0]])
        inst._embedding_service = mock_emb

        candidates = [_make_recall("a")]
        results = inst.rerank("query", candidates, top_k=5)
        assert len(results) == 1
        # Should have gone through embedding service path and gotten a bi_score
        assert isinstance(results[0], RerankResult)


class TestRerankDegradation:
    """完全失败时降级返回原始候选项"""

    def test_both_paths_fail_returns_degraded(self):
        """专用模型和 EmbeddingService 都失败时返回原始候选项（最多 top_k 个）"""
        from retrieval_service.rerank.bi_encoder import BiEncoderReranker

        inst = BiEncoderReranker()
        inst._bi_encoder_model_path = "/models/failing"
        inst._dedicated_model = MagicMock()
        inst._dedicated_model.encode.side_effect = RuntimeError("dedicated fail")

        # EmbeddingService also fails
        mock_emb = MagicMock()
        inst._embedding_service = mock_emb

        candidates = [_make_recall("a"), _make_recall("b")]
        results = inst.rerank("query", candidates, top_k=5)
        assert len(results) == 2  # top_k > len(candidates),所以返回全部候选
        assert all(isinstance(r, RerankResult) for r in results)

    def test_degraded_returns_zero_scores(self):
        """降级结果的 bi_score 应为 0.0"""
        from retrieval_service.rerank.bi_encoder import BiEncoderReranker

        inst = BiEncoderReranker()
        inst._bi_encoder_model_path = None
        inst._dedicated_model = None
        # Make EmbeddingService's encode_text fail
        mock_emb = MagicMock()
        mock_emb.encode_text.side_effect = ValueError("no GPU")
        inst._embedding_service = mock_emb

        candidates = [_make_recall("a")]
        results = inst.rerank("query", candidates, top_k=5)
        assert results[0].bi_score == 0.0


class TestRerankWithDedicatedModel:
    """_rerank_with_dedicated_model 内部方法"""

    def test_loads_model_on_demand(self):
        """dedicated_model 为 None 时自动加载"""
        import types as _types

        from retrieval_service.rerank.bi_encoder import BiEncoderReranker

        inst = BiEncoderReranker()
        inst._bi_encoder_model_path = "/models/dedicated"
        inst._dedicated_model = None

        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda texts, **kw: (
            np.array([1.0, 0.0]) if isinstance(texts, str) else np.array([[1.0, 0.0]])
        )

        # Pre-install a mock sentence_transformers in sys.modules so the
        # import inside _rerank_with_dedicated_model doesn't trigger the
        # real cascading torch/transformers import chain.
        mock_st_mod = _types.ModuleType("sentence_transformers")
        mock_st_mod.SentenceTransformer = lambda *a, **kw: mock_model
        sys.modules["sentence_transformers"] = mock_st_mod

        try:
            candidates = [_make_recall("a")]
            results = inst._rerank_with_dedicated_model("query", candidates, top_k=5)
            assert len(results) == 1
            assert inst._dedicated_model is mock_model
        finally:
            sys.modules.pop("sentence_transformers", None)

    def test_raises_if_no_model_path(self):
        """model_path 为 None 时应抛出 RuntimeError"""
        from retrieval_service.rerank.bi_encoder import BiEncoderReranker

        inst = BiEncoderReranker()
        inst._bi_encoder_model_path = None
        inst._dedicated_model = None

        with pytest.raises(RuntimeError, match="Dedicated BiEncoder model not loaded"):
            inst._rerank_with_dedicated_model("query", [_make_recall("a")], top_k=5)

    def test_rerank_with_dedicated_model_returns_top_k(self):
        """_rerank_with_dedicated_model 返回 top_k 个结果"""
        from retrieval_service.rerank.bi_encoder import BiEncoderReranker

        inst = BiEncoderReranker()
        inst._bi_encoder_model_path = "/models/dedicated"
        mock_model = MagicMock()
        # encode query → 1D, encode docs → 2D so np.dot gives similarity vector
        mock_model.encode.side_effect = lambda texts, **kw: (
            np.array([1.0, 0.0, 0.0])
            if isinstance(texts, str)
            else np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
        )
        inst._dedicated_model = mock_model

        candidates = [_make_recall("a"), _make_recall("b"), _make_recall("c")]
        results = inst._rerank_with_dedicated_model("query", candidates, top_k=2)
        assert len(results) == 2


class TestEmbeddingServiceLazyLoad:
    """embedding_service 延迟加载"""

    def test_embedding_service_lazy(self):
        """访问 embedding_service 时才加载"""
        from retrieval_service.rerank.bi_encoder import BiEncoderReranker

        inst = BiEncoderReranker()
        assert inst._embedding_service is None
        # Access the property
        _ = inst.embedding_service
        assert inst._embedding_service is not None
