"""
Query Rewrite 模块（readme 4.4 节）

定位：路由增强器，非核心强依赖

输入：原始 query + 最近 6 轮对话
输出：强制 JSON Schema（rewritten_query, business_type, intent,
      requires_context, standardized_entities）

失败降级策略：
1. JSON 解析失败 → 重试 1 次（temperature=0）
2. 二次失败 → 结构兜底（规则/关键词 + BERT 意图分类）
3. 保守执行：法规兜底场景强制提升检索量、禁止缓存命中
"""

from __future__ import annotations

import json
import logging
import re

from common.config import get_config_dict
from common.models import QueryRewriteResult

config = get_config_dict()

logger = logging.getLogger(__name__)

REWRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "rewritten_query": {"type": "string"},
        "business_type": {
            "type": "string",
            "enum": ["regulation", "development", "ingredient", "product", "general", "short"],
        },
        "intent": {"type": "string", "enum": ["compliance", "formulation", "ingredient", "product", "general"]},
        "requires_context": {"type": "boolean"},
        "standardized_entities": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["rewritten_query", "business_type", "intent", "requires_context"],
}

# Rewrite Prompt 模板
REWRITE_PROMPT_TEMPLATE = """你是一个化妆品行业知识问答系统的查询改写助手。请分析用户的查询并输出结构化 JSON。

任务：
1. 将用户的原始查询改写为更适合向量检索的形式
2. 识别业务类型和意图
3. 提取标准化实体（成分名、INCI 名称、法规条款号、产品名等）

业务类型定义：
- regulation: 法规相关（合规、标准、许可、备案、禁用清单）
- development: 研发相关（配方、成分、工艺、制备）
- ingredient: 成分相关（功效、安全浓度、复配禁忌）
- product: 产品相关（品牌、价格、适用肤质）
- general: 通用问答
- short: 简短查询

意图定义：
- compliance: 合规查询
- formulation: 配方查询
- ingredient: 成分查询
- product: 产品查询
- general: 通用意图

最近对话历史：
{dialog_history}

当前查询：{query}

请输出严格 JSON 格式：
{{
    "rewritten_query": "改写后的查询",
    "business_type": "业务类型",
    "intent": "意图",
    "requires_context": true/false,
    "standardized_entities": ["实体1", "实体2"]
}}
"""


class QueryRewriter:
    """
    Query Rewrite 模块

    通过共享 4B vLLM 端点 (Qwen3-4B, gen_4b) 执行查询改写，
    输出 business_type / intent / rewritten_query 等结构化字段，
    为后续检索路由和权限控制提供信号。

    支持 Prompt 反馈闭环（PRD §12.2）：
    启动时加载活跃的 Prompt 版本，反馈循环可热更新。
    """

    def __init__(self):
        self._router = None
        self.max_output_tokens = config["query_rewrite"]["max_output_tokens"]
        self.temperature = config["query_rewrite"]["temperature"]
        self.dialog_rounds = config["query_rewrite"]["dialog_rounds"]
        self._prompt_template = None  # 懒加载活跃 prompt 版本
        logger.info("QueryRewriter 初始化完成")

    @property
    def router(self):
        if self._router is None:
            from router.stateless_router import StatelessRouter

            self._router = StatelessRouter()
        return self._router

    def _get_prompt_template(self) -> str:
        """
        获取活跃的 Prompt 模板（PRD §12.2 反馈闭环）。

        优先加载 Feedback 系统管理的活跃版本，
        不存在则回退到默认硬编码模板。
        """
        if self._prompt_template is not None:
            return self._prompt_template

        try:
            from rewrite.feedback import RewriteFeedback

            feedback = RewriteFeedback()
            active_version = feedback.get_active_prompt_version()
            if active_version and active_version.get("prompt_text"):
                self._prompt_template = active_version["prompt_text"]
                logger.info(f"加载活跃 Prompt 版本: {active_version.get('version', 'unknown')}")
                return self._prompt_template
        except Exception as e:
            logger.debug(f"加载活跃 Prompt 版本失败（使用默认模板）: {e}")

        self._prompt_template = REWRITE_PROMPT_TEMPLATE
        return self._prompt_template

    def reload_prompt_template(self):
        """强制重新加载 Prompt 模板（用于反馈循环热更新后调用）"""
        self._prompt_template = None
        logger.info("Prompt 模板已重置，下次 rewrite 将重新加载")

    def rewrite(self, query: str, recent_dialogs: list[str] = None) -> QueryRewriteResult:
        """
        执行 Query Rewrite

        Args:
            query: 用户原始查询
            recent_dialogs: 最近 N 轮对话历史

        Returns:
            QueryRewriteResult 结构化输出

        Raises:
            Exception: JSON 解析失败两次后抛出（调用方降级）
        """

        # 构造对话历史
        dialog_text = ""
        if recent_dialogs:
            for i, d in enumerate(recent_dialogs[-self.dialog_rounds :], 1):
                dialog_text += f"第{i}轮: {d}\n"

        # 构造 prompt（使用活跃的 Prompt 版本）
        prompt = self._get_prompt_template().format(
            dialog_history=dialog_text or "（无历史对话）",
            query=query,
        )

        # 调用共享 4B vLLM 端点 (gen_4b)
        response_text = self._call_llm(prompt)

        # 解析 JSON
        result = self._parse_response(response_text, query)
        if result is None:
            # 重试一次（temperature=0）
            logger.warning("Rewrite JSON 解析失败，重试...")
            response_text = self._call_llm(prompt, temperature=0)
            result = self._parse_response(response_text, query)

        if result is None:
            raise Exception("Rewrite JSON 解析两次失败")

        # 逻辑一致性修正（readme 4.4）
        if result.business_type == "regulation" and result.intent in ("ingredient", "formulation"):
            result.intent = "compliance"

        return result

    def generate_variants(self, rewritten_query: str) -> list[str]:
        """
        生成 2~3 个变体 Query 用于改写泛化路召回（readme 7.1 第4路）

        通过共享 4B vLLM 端点 (gen_4b) 生成同义变体
        """
        variant_prompt = f"""请为以下查询生成2-3个不同表述的同义变体查询，用于搜索引擎检索。
每个变体一行，只输出变体，不要其他内容。

原始查询：{rewritten_query}

变体："""
        try:
            response = self._call_llm(variant_prompt, temperature=0.7)
            variants = [line.strip() for line in response.strip().split("\n") if line.strip()]
            # 去掉可能的编号前缀
            variants = [re.sub(r"^\d+[\.\)、]\s*", "", v) for v in variants]
            variants = [v for v in variants if v and v != rewritten_query]
            return variants[:3]
        except Exception as e:
            logger.warning(f"生成变体查询失败: {e}")
            return []

    def _call_llm(self, prompt: str, temperature: float = None) -> str:
        """调用共享 4B vLLM 端点 (gen_4b)"""
        from router.stateless_router import StatelessRouter

        payload = {
            "prompt": prompt,
            "max_tokens": self.max_output_tokens,
            "temperature": temperature if temperature is not None else self.temperature,
        }

        try:
            router = StatelessRouter()
            response = router.route_completion("gen_4b", prompt, max_tokens=192, temperature=payload["temperature"])
            return response
        except Exception as e:
            logger.error(f"共享 4B vLLM 端点调用失败: {e}")
            raise

    def _parse_response(self, text: str, original_query: str) -> QueryRewriteResult | None:
        """解析 vLLM 输出为 QueryRewriteResult"""
        from core.pipeline_context import QueryRewriteResult

        try:
            # 尝试从文本中提取 JSON
            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            if not json_match:
                return None
            data = json.loads(json_match.group())

            return QueryRewriteResult(
                rewritten_query=data.get("rewritten_query", original_query),
                business_type=data.get("business_type", "general"),
                intent=data.get("intent", "general"),
                requires_context=data.get("requires_context", True),
                standardized_entities=data.get("standardized_entities", []),
                confidence=data.get("confidence", 0.5),
                fallback=data.get("fallback", False),
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Rewrite JSON 解析异常: {e}")
            return None

    def _simulate_rewrite(self, query: str) -> str:
        """模拟 Rewrite（开发/测试用）"""
        # 从 config.json 读取领域关键词
        _domain_kw = config.get("domain_keywords", {})
        regulation_kw = _domain_kw.get("regulation", ["法规", "合规", "标准", "备案", "许可", "禁用"])
        development_kw = _domain_kw.get("development", ["配方", "研发", "工艺", "制备", "开发"])
        ingredient_kw = _domain_kw.get("ingredient", ["成分", "INCI", "功效", "浓度"])
        product_kw = _domain_kw.get("product", ["产品", "品牌", "适用", "肤质", "包装"])

        biz_type = "general"
        intent = "general"
        if any(kw in query for kw in regulation_kw):
            biz_type = "regulation"
            intent = "compliance"
        elif any(kw in query for kw in development_kw):
            biz_type = "development"
            intent = "formulation"
        elif any(kw in query for kw in ingredient_kw):
            biz_type = "ingredient"
            intent = "ingredient"
        elif any(kw in query for kw in product_kw):
            biz_type = "product"
            intent = "product"

        return json.dumps(
            {
                "rewritten_query": query,
                "business_type": biz_type,
                "intent": intent,
                "requires_context": True,
                "standardized_entities": [],
            },
            ensure_ascii=False,
        )
