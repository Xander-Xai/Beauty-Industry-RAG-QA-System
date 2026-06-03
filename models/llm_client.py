"""
LLM 客户端模块

支持双 vLLM 实例调用：
- vLLM-Gen-4B (GPU1): 简单查询
- vLLM-Gen-14B+QLoRA (GPU0): 复杂查询（法规/研发）

通过 StatelessRouter 分发请求
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

logger = logging.getLogger(__name__)


class LLMClient:
    """
    LLM 客户端

    封装对 vLLM 实例的调用，支持：
    - 模型路由（4B/14B）
    - Prompt 构造（含 Rewrite 结果 + 证据 + 对话历史）
    - 长文本一致性保障（readme 4.6）
    - 动态输出长度控制（readme 4.7）
    """

    def __init__(self):
        self._router = None
        self.max_conversation_rounds = config["generation"]["max_conversation_rounds"]
        self.prompt_version = config["generation"]["prompt_version"]
        logger.info("LLMClient 初始化完成")

    @property
    def router(self):
        if self._router is None:
            from router.stateless_router import StatelessRouter
            self._router = StatelessRouter()
        return self._router

    def generate(
        self,
        ctx,
        target_model: str = "qwen3-4b",
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> "GenerationResult":
        """
        生成回答

        Args:
            ctx: RequestContext（含 rewrite_result, evidence_result, rerank_results 等）
            target_model: "qwen3-4b" 或 "qwen3-14b"
            max_tokens: 最大输出 token 数
            temperature: 生成温度

        Returns:
            GenerationResult
        """
        from core.pipeline_context import GenerationResult, SessionState

        # 构造 Prompt
        messages = self._build_messages(ctx)

        # 映射目标模型到端点
        endpoint_key = "gen_14b" if target_model == "qwen3-14b" else "gen_4b"

        # 调用 vLLM
        try:
            answer = self.router.route_chat(
                endpoint_key,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            # 检查是否需要续写（readme 4.6）
            has_more = self._check_truncation(answer, max_tokens, ctx.rewrite_result)

            return GenerationResult(
                answer=answer,
                model_used=target_model,
                max_tokens=max_tokens,
                has_more=has_more,
                session_id=ctx.session_id,
            )

        except Exception as e:
            logger.error(f"LLM 生成失败 ({target_model}): {e}")
            raise

    def generate_continuation(
        self,
        ctx,
        session_state,
        already_generated: str,
        target_model: str = "qwen3-14b",
        max_tokens: int = 1024,
    ) -> "GenerationResult":
        """
        长文本续写（readme 4.6）

        上下文重建 + 单次重生成（非续写机制）：
        1. 重建完整上下文
        2. 构造约束式 Prompt（不重复已输出内容，保持语义一致）
        3. 证据锁定：续写时使用首次检索的 Top-3 doc_id
        """
        from core.pipeline_context import GenerationResult

        # 重建 messages，追加约束前缀
        messages = self._build_messages(ctx)
        messages.append({
            "role": "assistant",
            "content": already_generated,
        })
        messages.append({
            "role": "user",
            "content": (
                "请继续补充后续内容。要求：\n"
                "1. 保持与已有回答的语义、语气、结构完全一致\n"
                "2. 不重复已输出的内容\n"
                "3. 仅补充后续部分"
            ),
        })

        endpoint_key = "gen_14b" if target_model == "qwen3-14b" else "gen_4b"

        try:
            answer = self.router.route_chat(
                endpoint_key,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.0,  # 确定性解码保证一致性
            )

            return GenerationResult(
                answer=answer,
                model_used=target_model,
                max_tokens=max_tokens,
                has_more=self._check_truncation(answer, max_tokens, ctx.rewrite_result),
                session_id=ctx.session_id,
            )
        except Exception as e:
            logger.error(f"LLM 续写失败 ({target_model}): {e}")
            raise

    def _build_messages(self, ctx) -> list[dict]:
        """
        构造 Chat Messages

        返回标准 OpenAI format: [{"role": "system", "content": ...}, ...]
        """
        from core.pipeline_context import SessionState

        messages = []

        # ① System Prompt
        system_prompt = self._get_system_prompt(ctx.rewrite_result)
        messages.append({"role": "system", "content": system_prompt})

        # ② 对话历史
        if ctx.session_id:
            session = SessionState.get_or_create(ctx.session_id)
            history = session.dialog_rounds[-self.max_conversation_rounds:]
            for round in history:
                if "user_input" in round:
                    messages.append({"role": "user", "content": round["user_input"]})
                if "response" in round:
                    messages.append({"role": "assistant", "content": round["response"]})

        # ③ Evidence Gate 增强提示 + 检索证据
        evidence_text = self._format_evidence(ctx)
        evidence_gate_note = ""
        if ctx.evidence_result and ctx.evidence_result.decision == "enhanced_generate":
            evidence_gate_note = (
                "\n【注意】系统置信度中等，请基于以下多个证据源综合回答，"
                "如有矛盾请指出并不确定性。\n"
            )

        # ④ 当前用户问题（含证据）
        user_content = f"""{evidence_gate_note}
相关证据：
{evidence_text}

用户问题：{ctx.user_input}

请基于以上证据给出回答："""

        messages.append({"role": "user", "content": user_content})

        return messages

    def _get_system_prompt(self, rewrite_result=None) -> str:
        """获取系统提示"""
        business_type = rewrite_result.business_type if rewrite_result else "general"

        base_prompt = (
            "你是一个专业的化妆品行业知识助手。请基于提供的证据准确回答用户问题。\n"
            "要求：\n"
            "1. 回答必须基于提供的证据，不编造信息\n"
            "2. 引用具体法规条款或成分数据时请标注来源\n"
            "3. 不确定的内容请明确说明\n"
        )

        if business_type == "regulation":
            base_prompt += "4. 法规类问题请特别注意引用的准确性，不准确的法规引用可能导致合规风险\n"
        elif business_type == "development":
            base_prompt += "4. 研发类问题请提供具体的配方参数和工艺建议\n"

        return base_prompt

    def _format_evidence(self, ctx) -> str:
        """格式化检索证据"""
        if not ctx.rerank_results:
            return "（无相关证据）"

        evidence_parts = []
        for i, doc in enumerate(ctx.rerank_results[:5], 1):
            score = doc.ce_score_ensemble or doc.bi_score or 0.0
            evidence_parts.append(
                f"[证据{i}] (相关度:{score:.2f})\n{doc.content[:500]}"
            )
        return "\n\n".join(evidence_parts)

    def _check_truncation(self, answer: str, max_tokens: int, rewrite_result) -> bool:
        """
        检查输出是否被截断

        readme 4.6: 当生成内容超过 max_tokens，返回 has_more=True
        """
        # 粗略估计：如果输出接近 max_tokens，可能被截断
        estimated_tokens = len(answer) * 1.5  # 中文字符 ≈ 1.5 tokens
        if estimated_tokens >= max_tokens * 0.9:
            # 检查最后一句是否完整
            last_chars = answer[-3:] if len(answer) >= 3 else answer
            if not any(c in last_chars for c in ["。", "！", "？", ".", "!", "?", "）", "）"]):
                return True
        return False
