"""
GAP-15: 核心管线变量排序修复测试

验证：
1) is_complex 在使用前已赋值（无 NameError）
2) 复杂度评估失败时降级为简单模型
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

from core.pipeline import OnlineRAGPipeline
from core.pipeline_context import (
    AnswerGateResult,
    EvidenceGateResult,
    GenerationResult,
    QueryRewriteResult,
    RequestContext,
)

# ── helpers ──────────────────────────────────────────────────


def _make_ctx(user_input: str = "烟酰胺的安全性") -> RequestContext:
    """构造最小 RequestContext（request_id / user_id 自动填充）"""
    return RequestContext(user_input=user_input, user_id="test_user")


def _make_rewrite(**overrides) -> QueryRewriteResult:
    defaults = dict(
        rewritten_query="烟酰胺 安全性",
        business_type="general",
        intent="general",
        requires_context=False,
        confidence=0.9,
        fallback=False,
    )
    defaults.update(overrides)
    return QueryRewriteResult(**defaults)


def _setup_pipeline_mocks(pipeline: MagicMock, rewrite: QueryRewriteResult | None = None):
    """为 pipeline 的懒加载子模块统一注入 mock 行为。

    仅设置通过 process() 主路径所需的行为；
    各测试可在此基础上按需调整。
    """
    if rewrite is None:
        rewrite = _make_rewrite()

    # A/B 分流 —— 无运行实验
    pipeline._ab_platform = MagicMock()
    pipeline._ab_platform.get_running_experiments.return_value = []

    # Query Rewrite
    pipeline._query_rewriter = MagicMock()
    pipeline._query_rewriter.rewrite.return_value = rewrite

    # 缓存 —— 默认 MISS
    pipeline._cache = MagicMock()
    pipeline._cache.get.return_value = None
    pipeline._cache.set.return_value = None

    # KV 准入 —— 默认通过
    pipeline._admission = MagicMock()
    pipeline._admission.admit.return_value = (True, "ok", "P1")
    pipeline._admission.get_pressure.return_value = 0.5
    pipeline._admission.get_effective_max_tokens.return_value = 512
    pipeline._admission.get_truncation_tokens.return_value = None
    pipeline._admission.should_force_downgrade.return_value = False

    # Embedding / Recall / Rerank / Evidence / LLM / AnswerGate
    pipeline._embedding_service = MagicMock()
    pipeline._embedding_service.encode_text.return_value = [0.1] * 768

    pipeline._parallel_recall = MagicMock()
    pipeline._parallel_recall.execute.return_value = ([], 0.0)

    pipeline._bi_encoder = MagicMock()
    pipeline._bi_encoder.rerank.return_value = []

    pipeline._cross_encoder_ensemble = MagicMock()
    pipeline._cross_encoder_ensemble.rerank.return_value = []

    pipeline._evidence_gate = MagicMock()
    pipeline._evidence_gate.evaluate.return_value = EvidenceGateResult(
        evidence_score=0.8,
        ce_top1_score=0.9,
        ce_top3_mean_score=0.85,
        retrieval_agreement_score=0.7,
        doc_consistency_score=0.8,
        decision="pass",
        top_docs=[],
    )

    pipeline._llm_client = MagicMock()
    pipeline._llm_client.generate.return_value = GenerationResult(
        answer="烟酰胺在推荐浓度下是安全的。",
        model_used="qwen3-4b",
        max_tokens=512,
        has_more=False,
    )

    pipeline._answer_gate = MagicMock()
    pipeline._answer_gate.verify.return_value = AnswerGateResult(
        nli_contradiction_score=0.0,
        nli_entailment_score=0.95,
        passed=True,
    )


# ── tests ────────────────────────────────────────────────────


class TestPipelineVariableOrdering:
    """GAP-15: 确保 is_complex 在使用前已赋值且异常路径安全。"""

    @patch("core.pipeline.log_audit_event")
    def test_normal_path_no_name_error(self, _mock_audit):
        """复杂度评估正常完成，target_model 按 is_complex 选择，无 NameError。"""
        pipeline = OnlineRAGPipeline()
        _setup_pipeline_mocks(pipeline)
        pipeline._complexity_evaluator = MagicMock()
        pipeline._complexity_evaluator.evaluate.return_value = True  # 复杂查询

        ctx = _make_ctx()
        pipeline.process(ctx)

        # 复杂查询 → complex tier
        gen = pipeline._llm_client.generate
        assert gen.call_count == 1
        assert gen.call_args.kwargs.get("target_model") == "complex"
        assert ctx.degraded is False
        assert "complexity_eval_fallback" not in (ctx.fallback_reason or "")

    @patch("core.pipeline.log_audit_event")
    def test_complexity_failure_degrades_to_simple_model(self, _mock_audit):
        """复杂度评估器抛异常 → 降级为 4B，ctx.degraded=True。"""
        pipeline = OnlineRAGPipeline()
        _setup_pipeline_mocks(pipeline)
        # 模拟复杂度评估失败
        pipeline._complexity_evaluator = MagicMock()
        pipeline._complexity_evaluator.evaluate.side_effect = RuntimeError("BERT model load failed")

        ctx = _make_ctx()
        pipeline.process(ctx)

        gen = pipeline._llm_client.generate
        assert gen.call_count == 1
        assert gen.call_args.kwargs.get("target_model") == "simple"
        assert ctx.degraded is True
        assert "complexity_eval_fallback" in ctx.fallback_reason
        assert "BERT model load failed" in ctx.fallback_reason
