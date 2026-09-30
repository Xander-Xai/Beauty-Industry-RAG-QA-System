"""
QueryRewriter -- migrated to standalone rewrite-service.

Calls vLLM (Qwen3-4B) via httpx for query rewriting, variant generation,
and structured JSON extraction.  Falls back to keyword-based rule
classification when the LLM is unavailable or returns unparseable output.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys

import httpx

# Ensure project root is on path for config.json resolution
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.config import get_config_dict

config = get_config_dict()

logger = logging.getLogger("rewrite-service.rewriter")


# ── Rewrite Prompt (identical to the original rewrite/query_rewriter.py) ──

REWRITE_PROMPT_TEMPLATE = """你是一个化妆品行业知识问答系统的查询改写助手。请分析用户的查询并输出结构化 JSON。

任务：
1. 将用户的原始查询改写为更适合向量检索的形式
2. 识别业务类型和意图
3. 提取标准化实体（成分名、法规条款号、INCI 名称等）

业务类型定义：
- regulation: 法规相关（合规、标准、许可、备案）
- development: 研发相关（配方、成分、工艺）
- general: 通用问答
- short: 简短查询（品牌、价格等简单问题）

意图定义：
- compliance: 合规查询
- formulation: 配方查询
- ingredient: 成分查询
- general: 通用意图

最近对话历史：
{dialog_history}

当前查询：{query}

请输出严格 JSON 格式（不要输出其他内容）：
{{
    "rewritten_query": "改写后的查询",
    "business_type": "业务类型",
    "intent": "意图",
    "requires_context": true/false,
    "standardized_entities": ["实体1", "实体2"]
}}
"""


# ── Keyword-based fallback rules ───────────────────────────────────────

_REGULATION_KEYWORDS = re.compile(r"(备案|注册|法规|标准|GB|合规|许可|公告|安全技术规范|化妆品监督)", re.IGNORECASE)
_INGREDIENT_KEYWORDS = re.compile(r"(成分|原料|INCI|功效|活性|配方|浓度|添加量)", re.IGNORECASE)
_FORMULATION_KEYWORDS = re.compile(r"(配方|工艺|制备|生产|乳化|稳定性|质地|剂型)", re.IGNORECASE)


class QueryRewriter:
    """
    Query rewrite microservice core logic.

    Uses vLLM OpenAI-compatible /v1/chat/completions endpoint for LLM inference.
    """

    def __init__(self) -> None:
        rewrite_cfg = config.get("gpu1", {}).get("models", {}).get("vllm_rewrite", {})
        port = rewrite_cfg.get("port", 8101)
        self._vllm_url = f"http://localhost:{port}/v1/chat/completions"

        qr_cfg = config.get("query_rewrite", {})
        self._max_output_tokens = qr_cfg.get("max_output_tokens", 192)
        self._temperature = qr_cfg.get("temperature", 0.1)
        self._dialog_rounds = qr_cfg.get("dialog_rounds", 6)

        self._http = httpx.Client(timeout=30.0)
        logger.info("QueryRewriter initialised  vllm_url=%s", self._vllm_url)

    # ── public API ──────────────────────────────────────────────────

    def rewrite(self, query: str, recent_dialogs: list[str] | None = None) -> dict:
        """
        Rewrite *query* using conversation context.

        Returns a plain dict compatible with ``common.models.QueryRewriteResult``.
        Raises ``Exception`` if LLM JSON parsing fails twice.
        """
        dialog_text = ""
        if recent_dialogs:
            for idx, d in enumerate(recent_dialogs[-self._dialog_rounds :], start=1):
                dialog_text += f"第{idx}轮: {d}\n"

        prompt = REWRITE_PROMPT_TEMPLATE.format(
            dialog_history=dialog_text or "（无历史对话）",
            query=query,
        )

        # First attempt (default temperature)
        response_text = self._call_llm(prompt)
        result = self._parse_response(response_text, query)

        if result is None:
            # Retry once at temperature=0
            logger.warning("Rewrite JSON parse failed, retrying at temperature=0")
            response_text = self._call_llm(prompt, temperature=0)
            result = self._parse_response(response_text, query)

        if result is None:
            # Keyword fallback
            logger.warning("Rewrite LLM failed twice, falling back to keywords")
            result = self._keyword_fallback(query)

        # Logical consistency fix (regulation + ingredient/formulation -> compliance)
        if result.get("business_type") == "regulation" and result.get("intent") in (
            "ingredient",
            "formulation",
        ):
            result["intent"] = "compliance"

        return result

    def generate_variants(self, rewritten_query: str) -> list[str]:
        """
        Generate 2-3 synonymous variant queries for recall expansion.
        """
        variant_prompt = (
            "请为以下查询生成2-3个不同表述的同义变体查询，用于搜索引擎检索。\n"
            "每个变体一行，只输出变体，不要其他内容。\n\n"
            f"原始查询：{rewritten_query}\n\n"
            "变体："
        )
        try:
            response = self._call_llm(variant_prompt, temperature=0.7)
            variants = [line.strip() for line in response.strip().split("\n") if line.strip()]
            # Strip leading numbering like "1. ", "2) ", "3、"
            variants = [re.sub(r"^\d+[\.\)、]\s*", "", v) for v in variants]
            variants = [v for v in variants if v and v != rewritten_query]
            return variants[:3]
        except Exception as exc:
            logger.warning("Variant generation failed: %s", exc)
            return []

    # ── LLM call ───────────────────────────────────────────────────

    def _call_llm(self, prompt: str, temperature: float | None = None) -> str:
        """Call vLLM chat completions endpoint via httpx."""
        payload = {
            "model": "default",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self._max_output_tokens,
            "temperature": temperature if temperature is not None else self._temperature,
        }
        resp = self._http.post(self._vllm_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    # ── Response parsing ────────────────────────────────────────────

    def _parse_response(self, text: str, original_query: str) -> dict | None:
        """Extract structured JSON dict from LLM output."""
        try:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return None
            data = json.loads(match.group())

            return {
                "rewritten_query": data.get("rewritten_query", original_query),
                "business_type": data.get("business_type", "general"),
                "intent": data.get("intent", "general"),
                "requires_context": data.get("requires_context", True),
                "standardized_entities": data.get("standardized_entities", []),
                "confidence": data.get("confidence", 0.5),
                "fallback": data.get("fallback", False),
            }
        except (json.JSONDecodeError, KeyError) as exc:
            logger.error("Rewrite JSON parse error: %s", exc)
            return None

    # ── Keyword-based fallback ──────────────────────────────────────

    @staticmethod
    def _keyword_fallback(query: str) -> dict:
        """
        Classify query using simple keyword rules when the LLM is
        completely unavailable.
        """
        if _REGULATION_KEYWORDS.search(query):
            biz, intent = "regulation", "compliance"
        elif _FORMULATION_KEYWORDS.search(query):
            biz, intent = "development", "formulation"
        elif _INGREDIENT_KEYWORDS.search(query):
            biz, intent = "development", "ingredient"
        else:
            biz, intent = "general", "general"

        return {
            "rewritten_query": query,
            "business_type": biz,
            "intent": intent,
            "requires_context": True,
            "standardized_entities": [],
            "confidence": 0.3,
            "fallback": True,
        }
