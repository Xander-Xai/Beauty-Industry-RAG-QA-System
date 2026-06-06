"""
test_llm_client_full.py — 新增测试: LLMClient 的 httpx 调用、超时处理、
fallback 逻辑、不同 business_type 的 max_output_tokens 差异。
"""
import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client():
    from models.llm_client import LLMClient
    client = LLMClient.__new__(LLMClient)
    client._router = MagicMock()
    client.max_conversation_rounds = 6
    client.prompt_version = "v2.1"
    return client


def _make_ctx(
    query="烟酰胺浓度",
    business_type="development",
    intent="ingredient",
    evidence_decision="pass",
    session_id="test_session",
):
    from core.pipeline_context import (
        RequestContext, QueryRewriteResult, RerankResult,
        EvidenceGateResult,
    )
    ctx = RequestContext(
        user_input=query,
        session_id=session_id,
        user_id="test_user",
    )
    ctx.rewrite_result = QueryRewriteResult(
        rewritten_query=query,
        business_type=business_type,
        intent=intent,
        requires_context=True,
    )
    ctx.rerank_results = [
        RerankResult(doc_id="doc_1", content="烟酰胺推荐浓度2-5%", final_score=0.95),
        RerankResult(doc_id="doc_2", content="透明质酸保湿配方", final_score=0.80),
        RerankResult(doc_id="doc_3", content="维生素E抗氧化", final_score=0.70),
    ]
    ctx.evidence_result = EvidenceGateResult(
        evidence_score=0.85, ce_top1_score=0.9, ce_top3_mean_score=0.85,
        retrieval_agreement_score=0.8, doc_consistency_score=0.9,
        decision=evidence_decision, top_docs=ctx.rerank_results,
    )
    ctx.user_role_mask = 0
    ctx.user_dept_mask = 0
    ctx.max_output_tokens = 512
    return ctx


# ===========================================================================
# 1. Successful Generation
# ===========================================================================

class TestLLMClientSuccessfulGeneration:
    """LLMClient 成功生成测试。"""

    def test_generate_returns_generation_result(self):
        """generate 应返回 GenerationResult 并正确传递 answer。"""
        client = _make_client()
        client.router.route_chat.return_value = "烟酰胺推荐浓度为2-5%。"

        ctx = _make_ctx()
        result = client.generate(ctx, target_model="qwen3-4b", max_tokens=512)

        assert result.answer == "烟酰胺推荐浓度为2-5%。"
        assert result.model_used == "qwen3-4b"
        assert result.max_tokens == 512

    def test_generate_passes_correct_parameters(self):
        """generate 应向 router 传递正确的 messages 和参数。"""
        client = _make_client()
        client.router.route_chat.return_value = "回答"

        ctx = _make_ctx()
        client.generate(ctx, target_model="qwen3-4b", max_tokens=256, temperature=0.5)

        call_kwargs = client.router.route_chat.call_args
        assert call_kwargs[1]["max_tokens"] == 256
        assert call_kwargs[1]["temperature"] == 0.5
        assert call_kwargs[0][0] == "gen_4b"  # endpoint key

    def test_generate_includes_system_and_user_messages(self):
        """generate 构建的 messages 应包含 system 和 user 角色。"""
        client = _make_client()
        captured = []
        def capture(endpoint, messages, **kw):
            captured.extend(messages)
            return "回答"
        client.router.route_chat.side_effect = capture

        ctx = _make_ctx()
        client.generate(ctx)

        roles = [m["role"] for m in captured]
        assert "system" in roles
        assert "user" in roles
        # system 应在 user 之前
        assert roles.index("system") < roles.index("user")

    def test_generate_formats_evidence_in_user_message(self):
        """generate 应将 rerank 结果格式化为证据文本。"""
        client = _make_client()
        captured = []
        def capture(endpoint, messages, **kw):
            captured.extend(messages)
            return "回答"
        client.router.route_chat.side_effect = capture

        ctx = _make_ctx()
        client.generate(ctx)

        user_msg = [m for m in captured if m["role"] == "user"][0]
        assert "证据" in user_msg["content"]
        # 应包含 doc_1 的内容（截取前 300 字符）
        assert "烟酰胺" in user_msg["content"]

    def test_generate_evidence_gate_enhanced_note(self):
        """Evidence Gate decision=enhanced_generate 时应注入增强提示。"""
        client = _make_client()
        captured = []
        def capture(endpoint, messages, **kw):
            captured.extend(messages)
            return "回答"
        client.router.route_chat.side_effect = capture

        ctx = _make_ctx(evidence_decision="enhanced_generate")
        client.generate(ctx)

        user_msg = [m for m in captured if m["role"] == "user"][0]
        assert "置信度中等" in user_msg["content"] or "注意" in user_msg["content"]


# ===========================================================================
# 2. Timeout / Error Handling
# ===========================================================================

class TestLLMClientErrorHandling:
    """LLMClient 错误处理测试。"""

    def test_generate_router_timeout_raises(self):
        """router 超时应向上层抛出异常。"""
        client = _make_client()
        client.router.route_chat.side_effect = TimeoutError("vLLM 超时")

        ctx = _make_ctx()
        with pytest.raises(TimeoutError):
            client.generate(ctx, target_model="qwen3-4b")

    def test_generate_router_connection_error_raises(self):
        """router 连接错误应向上层抛出异常。"""
        client = _make_client()
        client.router.route_chat.side_effect = ConnectionError("vLLM 不可达")

        ctx = _make_ctx()
        with pytest.raises(ConnectionError):
            client.generate(ctx)

    def test_generate_continuation_error_raises(self):
        """续写失败应向上层抛出异常。"""
        client = _make_client()
        client.router.route_chat.side_effect = RuntimeError("模型推理失败")

        ctx = _make_ctx()
        with pytest.raises(RuntimeError):
            client.generate_continuation(
                ctx, session_state=None, already_generated="前半部分", target_model="qwen3-14b"
            )


# ===========================================================================
# 3. Fallback on Error
# ===========================================================================

class TestLLMClientFallback:
    """LLMClient fallback / 降级逻辑测试。"""

    def test_resolve_endpoint_dev_mode_downgrades_14b(self):
        """非生产模式下 14B 应降级到 gen_4b。"""
        client = _make_client()
        with patch("common.config.is_production_mode", return_value=False):
            endpoint = client._resolve_endpoint("qwen3-14b")
        assert endpoint == "gen_4b"

    def test_resolve_endpoint_prod_mode_keeps_14b(self):
        """生产模式下 14B 应使用 gen_14b。"""
        client = _make_client()
        with patch("common.config.is_production_mode", return_value=True):
            endpoint = client._resolve_endpoint("qwen3-14b")
        assert endpoint == "gen_14b"

    def test_resolve_endpoint_4b_always_gen_4b(self):
        """4B 模型应始终使用 gen_4b 端点。"""
        client = _make_client()
        for prod in [True, False]:
            with patch("common.config.is_production_mode", return_value=prod):
                assert client._resolve_endpoint("qwen3-4b") == "gen_4b"

    def test_get_system_prompt_regulation_type(self):
        """法规类查询的系统提示应包含法规相关指引。"""
        client = _make_client()
        from core.pipeline_context import QueryRewriteResult
        rewrite = QueryRewriteResult(
            rewritten_query="铅含量标准",
            business_type="regulation",
            intent="compliance",
            requires_context=True,
        )
        prompt = client._get_system_prompt(rewrite)
        assert "法规" in prompt or "合规" in prompt

    def test_get_system_prompt_development_type(self):
        """研发类查询的系统提示应包含配方/工艺相关指引。"""
        client = _make_client()
        from core.pipeline_context import QueryRewriteResult
        rewrite = QueryRewriteResult(
            rewritten_query="烟酰胺配方",
            business_type="development",
            intent="formulation",
            requires_context=True,
        )
        prompt = client._get_system_prompt(rewrite)
        assert "配方" in prompt or "工艺" in prompt

    def test_get_system_prompt_general_type(self):
        """通用查询的系统提示不应包含特定业务指引。"""
        client = _make_client()
        from core.pipeline_context import QueryRewriteResult
        rewrite = QueryRewriteResult(
            rewritten_query="什么是护肤",
            business_type="general",
            intent="general",
            requires_context=False,
        )
        prompt = client._get_system_prompt(rewrite)
        assert "化妆品行业知识助手" in prompt

    def test_format_evidence_empty_rerank_results(self):
        """空 rerank 结果应返回无证据提示。"""
        client = _make_client()
        ctx = _make_ctx()
        ctx.rerank_results = []
        result = client._format_evidence(ctx)
        assert "无相关证据" in result

    def test_format_evidence_limits_to_top3(self):
        """evidence 格式化应限制为 top-3 文档。"""
        client = _make_client()
        ctx = _make_ctx()
        ctx.rerank_results = [
            MagicMock(ce_score_ensemble=0.9, bi_score=0.0, content=f"doc{i}")
            for i in range(10)
        ]
        result = client._format_evidence(ctx)
        # 应只包含 [证据1], [证据2], [证据3]
        assert "证据3" in result
        assert "证据4" not in result


# ===========================================================================
# 4. Different Business Types Use Different max_output_tokens
# ===========================================================================

class TestLLMClientBusinessTypeTokens:
    """不同 business_type 应使用不同的 max_output_tokens 配置。"""

    def test_regulation_type_uses_higher_tokens(self):
        """法规类查询通常需要更长输出（通过 ctx.max_output_tokens 控制）。"""
        client = _make_client()
        client.router.route_chat.return_value = "回答"

        ctx = _make_ctx(business_type="regulation", intent="compliance")
        ctx.max_output_tokens = 2048  # 法规类使用更大 token 数

        client.generate(ctx, target_model="qwen3-14b", max_tokens=ctx.max_output_tokens)

        call_kwargs = client.router.route_chat.call_args
        assert call_kwargs[1]["max_tokens"] == 2048

    def test_general_type_uses_standard_tokens(self):
        """通用查询使用标准 token 数。"""
        client = _make_client()
        client.router.route_chat.return_value = "回答"

        ctx = _make_ctx(business_type="general", intent="general")
        ctx.max_output_tokens = 512

        client.generate(ctx, target_model="qwen3-4b", max_tokens=ctx.max_output_tokens)

        call_kwargs = client.router.route_chat.call_args
        assert call_kwargs[1]["max_tokens"] == 512

    def test_development_type_uses_medium_tokens(self):
        """研发类查询使用中等 token 数。"""
        client = _make_client()
        client.router.route_chat.return_value = "回答"

        ctx = _make_ctx(business_type="development", intent="formulation")
        ctx.max_output_tokens = 1024

        client.generate(ctx, target_model="qwen3-4b", max_tokens=ctx.max_output_tokens)

        call_kwargs = client.router.route_chat.call_args
        assert call_kwargs[1]["max_tokens"] == 1024

    def test_continuation_uses_deterministic_decoding(self):
        """续写应使用 temperature=0 确保一致性。"""
        client = _make_client()
        client.router.route_chat.return_value = "续写内容"

        ctx = _make_ctx()
        client.generate_continuation(
            ctx, session_state=None, already_generated="前半部分", target_model="qwen3-14b"
        )

        call_kwargs = client.router.route_chat.call_args
        assert call_kwargs[1]["temperature"] == 0.0

    def test_generate_conversation_history_limit(self):
        """多轮对话应限制历史轮数。"""
        from core.pipeline_context import SessionState
        client = _make_client()
        client.max_conversation_rounds = 3  # 限制为 3 轮
        client.router.route_chat.return_value = "回答"

        ctx = _make_ctx(session_id="test_hist_limit")
        session = SessionState.get_or_create("test_hist_limit")
        for i in range(10):
            session.dialog_rounds.append({
                "user_input": f"问题{i}",
                "response": f"回答{i}",
            })

        captured = []
        def capture(endpoint, messages, **kw):
            captured.extend(messages)
            return "回答"
        client.router.route_chat.side_effect = capture

        client.generate(ctx)

        # 历史消息应被限制
        user_msgs = [m for m in captured if m["role"] == "user"]
        # 最后一个 user 消息是当前问题，历史最多 max_conversation_rounds 轮
        # 总 user 消息数应 <= max_conversation_rounds + 1 (当前问题)
        assert len(user_msgs) <= client.max_conversation_rounds + 1

        # 清理
        SessionState._sessions.pop("test_hist_limit", None)
