"""降级路径测试 — 验证外部服务不可用时的降级行为。"""
import os
import sys
import threading
import types
from collections import OrderedDict
from unittest.mock import MagicMock

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


class TestDegradationPaths:
    """验证外部服务不可用时的降级行为。"""

    def test_cache_degrades_without_redis(self):
        """Redis 不可用时缓存应降级到 L1。"""
        from cache.redis_cache import RedisCache
        cache = RedisCache.__new__(RedisCache)
        cache._l1 = OrderedDict()
        cache._l1_max = 100
        cache._l1_ttl = 300
        cache._l1_lock = threading.Lock()
        cache.enabled = False
        cache._degraded = False
        cache._degraded_since = 0
        cache.redis_client = None
        cache.l1_ttl = 300
        cache.l2_ttl = 3600

        stats = cache.get_stats()
        assert stats["l2_enabled"] is False

        cache.set("key", "val", 0, 0)
        assert cache.get("key", 0, 0) == "val"

    def test_answer_gate_with_no_nli_model(self):
        """NLI 模型不可用时应使用文本相似度兜底。"""
        from retrieval.answer_gate import AnswerGate
        gate = AnswerGate()
        if not gate._use_nli:
            doc = MagicMock()
            doc.content = "烟酰胺安全浓度≤5%在化妆品中使用"
            contradiction, entailment = gate._nli_inference(
                doc.content, "烟酰胺在化妆品中的浓度不超过5%"
            )
            assert isinstance(contradiction, float)
            assert isinstance(entailment, float)

    def test_bi_encoder_fallback_on_failure(self):
        """BiEncoder 重排失败时应降级返回原始候选。"""
        from core.pipeline_context import RecallResult
        from retrieval.bi_encoder import BiEncoderReranker
        reranker = BiEncoderReranker()

        mock_embedding = MagicMock()
        mock_embedding.encode_text.side_effect = RuntimeError("GPU OOM")
        reranker._embedding_service = mock_embedding

        candidates = [
            RecallResult(doc_id="d1", content="content1", score=0.9, source="dense_bge"),
            RecallResult(doc_id="d2", content="content2", score=0.8, source="bm25_es"),
        ]
        results = reranker.rerank(query="test", candidates=candidates, top_k=10)
        assert len(results) == 2
        for r in results:
            assert r.bi_score == 0.0

    def test_cross_encoder_fallback_on_failure(self):
        """CrossEncoder 重排失败时应降级返回 BiEncoder 排序。"""
        from core.pipeline_context import RerankResult
        from retrieval.cross_encoder_ensemble import CrossEncoderEnsemble
        ensemble = CrossEncoderEnsemble()

        candidates = [
            RerankResult(doc_id="d1", content="c1", bi_score=0.9),
            RerankResult(doc_id="d2", content="c2", bi_score=0.8),
        ]
        mock_aggregator = MagicMock()
        mock_aggregator.batch_predict.side_effect = RuntimeError("Model load failed")
        ensemble._batch_aggregator = mock_aggregator

        results = ensemble.rerank(query="test", candidates=candidates, top_k=10)
        assert len(results) == 2
        assert results[0].bi_score == 0.9

    def test_evidence_gate_single_doc(self):
        """单文档时 doc_consistency 应为 1.0。"""
        from core.pipeline_context import RerankResult
        from retrieval.evidence_gate import EvidenceEnsembleGate
        gate = EvidenceEnsembleGate()
        docs = [RerankResult(doc_id="d1", content="only one doc", ce_score_ensemble=0.7)]
        result = gate.evaluate(
            query="test", rerank_results=docs, retrieval_agreement_score=0.5,
        )
        assert result.doc_consistency_score == 1.0
