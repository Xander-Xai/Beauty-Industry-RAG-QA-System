"""端到端管线集成测试 — Mock LLM + 真实组件逻辑。"""

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


class TestEndToEndPipeline:
    """用 Mock LLM 测试完整管线流程。"""

    def test_full_pipeline_mock(self):
        """模拟完整管线: Context -> Rewrite -> Recall -> Rerank -> EvidenceGate -> Generate -> AnswerGate。"""
        from core.pipeline_context import (
            GenerationResult,
            QueryRewriteResult,
            RecallResult,
            RequestContext,
            RerankResult,
        )

        ctx = RequestContext(
            user_input="烟酰胺的安全浓度是多少？",
            user_id="test_user",
            user_role_mask=0x02,
            user_dept_mask=0x01,
        )

        rewrite_result = QueryRewriteResult(
            rewritten_query="烟酰胺 安全浓度 限量",
            business_type="ingredient",
            intent="compliance",
            requires_context=True,
            standardized_entities=["烟酰胺"],
            confidence=0.85,
            fallback=False,
        )
        ctx.rewrite_result = rewrite_result
        assert ctx.rewrite_result.business_type == "ingredient"

        recall_results = [
            RecallResult(doc_id="ing_0001", content="烟酰胺(Nicotinamide)安全浓度≤5%", score=0.92, source="dense_bge"),
            RecallResult(doc_id="reg_0001", content="GB/T 中规定烟酰胺限量", score=0.88, source="dense_bge"),
            RecallResult(doc_id="ing_0002", content="烟酰胺浓度限制在化妆品中的应用", score=0.85, source="bm25_es"),
        ]
        ctx.recall_results = recall_results

        rerank_results = [
            RerankResult(
                doc_id="ing_0001",
                content="烟酰胺(Nicotinamide)安全浓度≤5%，在化妆品中广泛使用",
                ce_score_ensemble=0.92,
                bi_score=0.88,
                final_score=0.95,
            ),
            RerankResult(
                doc_id="reg_0001",
                content="GB/T 化妆品中烟酰胺限量标准",
                ce_score_ensemble=0.75,
                bi_score=0.72,
                final_score=0.78,
            ),
        ]
        ctx.rerank_results = rerank_results

        from retrieval.evidence_gate import EvidenceEnsembleGate

        gate = EvidenceEnsembleGate()
        evidence_result = gate.evaluate(
            query=ctx.rewrite_result.rewritten_query,
            rerank_results=ctx.rerank_results,
            retrieval_agreement_score=0.80,
        )
        ctx.evidence_result = evidence_result
        assert evidence_result.decision == "pass"
        assert evidence_result.evidence_score > 0

        gen_result = GenerationResult(
            answer="烟酰胺（Nicotinamide）在化妆品中的安全浓度一般为 2%-5%。"
            "根据相关法规，烟酰胺在驻留型化妆品中的最大允许浓度为 5%。",
            model_used="qwen3-4b",
            max_tokens=512,
        )
        ctx.generation_result = gen_result
        assert "烟酰胺" in ctx.generation_result.answer

        from retrieval.answer_gate import AnswerGate

        answer_gate = AnswerGate()
        answer_gate_result = answer_gate.verify(
            answer=ctx.generation_result.answer,
            top_doc=ctx.rerank_results[0],
            is_regulation=(rewrite_result.business_type == "regulation"),
        )
        ctx.answer_gate_result = answer_gate_result
        assert isinstance(answer_gate_result.passed, bool)

        ctx.final_response = gen_result.answer if answer_gate_result.passed else ""
        assert len(ctx.final_response) > 0

        ctx.record_timing("rewrite", 45.0)
        ctx.record_timing("recall", 120.0)
        ctx.record_timing("rerank", 80.0)
        ctx.record_timing("evidence_gate", 15.0)
        ctx.record_timing("generation", 350.0)
        ctx.record_timing("answer_gate", 25.0)
        assert ctx.get_total_latency_ms() == 635.0

    def test_fallback_rewrite_logic(self):
        """pipeline 的 _fallback_rewrite 应正确进行规则分类。"""
        from core.pipeline import OnlineRAGPipeline

        pipeline = OnlineRAGPipeline()

        result = pipeline._fallback_rewrite("这个成分是否符合法规要求？")
        assert result.business_type == "regulation"
        assert result.intent == "compliance"
        assert result.fallback is True

        result = pipeline._fallback_rewrite("这个配方的制备工艺是什么？")
        assert result.business_type == "development"
        assert result.intent == "formulation"

        result = pipeline._fallback_rewrite("烟酰胺的功效和浓度？")
        assert result.business_type == "development"
        assert result.intent == "ingredient"

        result = pipeline._fallback_rewrite("你好")
        assert result.business_type == "general"
        assert result.intent == "general"

    def test_clip_routing_logic(self):
        """pipeline 的 _should_use_clip_sync 应返回三档 (use_clip, top_k)。"""
        from core.pipeline import OnlineRAGPipeline
        from core.pipeline_context import QueryRewriteResult

        pipeline = OnlineRAGPipeline()

        visual_rewrite = QueryRewriteResult(
            rewritten_query="请扫描标签上的成分表",
            business_type="ingredient",
            intent="ingredient",
            requires_context=True,
        )
        use_clip, top_k = pipeline._should_use_clip_sync(visual_rewrite)
        assert use_clip is True
        assert top_k >= 20

        ingredient_rewrite = QueryRewriteResult(
            rewritten_query="烟酰胺浓度",
            business_type="ingredient",
            intent="ingredient",
            requires_context=True,
        )
        use_clip, top_k = pipeline._should_use_clip_sync(ingredient_rewrite)
        assert use_clip is True

        general_rewrite = QueryRewriteResult(
            rewritten_query="你好世界",
            business_type="general",
            intent="general",
            requires_context=True,
        )
        use_clip, top_k = pipeline._should_use_clip_sync(general_rewrite)
        assert use_clip is False
        assert top_k == 0

    def test_merge_and_dedup(self):
        """pipeline 的 _merge_and_dedup 应正确去重。"""
        from core.pipeline import OnlineRAGPipeline
        from core.pipeline_context import RecallResult

        pipeline = OnlineRAGPipeline()

        results = [
            RecallResult(doc_id="doc1", content="content1", score=0.9, source="dense_bge"),
            RecallResult(doc_id="doc2", content="content2", score=0.8, source="bm25_es"),
            RecallResult(doc_id="doc1", content="content1_dup", score=0.7, source="rewrite_variant"),
            RecallResult(doc_id="doc3", content="content3", score=0.6, source="dense_bge"),
        ]
        merged = pipeline._merge_and_dedup(results)
        assert len(merged) == 3
        doc_ids = [r.doc_id for r in merged]
        assert doc_ids.count("doc1") == 1
