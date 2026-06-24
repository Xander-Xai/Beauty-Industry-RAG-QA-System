"""
Query Rewrite 测试 (rewrite/query_rewriter.py)

覆盖 §4.4 Query Rewrite 逻辑：
- _simulate_rewrite 关键词识别（模拟模式）
- _parse_response JSON Schema 输出解析
- 逻辑一致性修正 (regulation + ingredient/formulation -> compliance)
- 降级兜底逻辑（JSON 解析失败时返回 None）
- generate_variants 变体生成
- rewrite 端到端流程（mock LLM）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from unittest.mock import patch

import pytest

from core.pipeline_context import QueryRewriteResult
from rewrite.query_rewriter import REWRITE_SCHEMA, QueryRewriter

# ── 模拟 Rewrite 测试 ──

class TestSimulateRewrite:
    """测试 _simulate_rewrite 关键词识别（无需 LLM）"""

    def test_regulation_keywords(self):
        """法规类关键词识别为 regulation + compliance"""
        rewriter = QueryRewriter()
        result = json.loads(rewriter._simulate_rewrite("烟酰胺在化妆品中的使用法规是什么？"))
        assert result["business_type"] == "regulation"
        assert result["intent"] == "compliance"

    def test_regulation_keywords_compliance(self):
        """'合规' 关键词识别为 regulation"""
        rewriter = QueryRewriter()
        result = json.loads(rewriter._simulate_rewrite("这个产品是否符合合规要求？"))
        assert result["business_type"] == "regulation"
        assert result["intent"] == "compliance"

    def test_regulation_keywords_ban_list(self):
        """'禁用' 关键词识别为 regulation"""
        rewriter = QueryRewriter()
        result = json.loads(rewriter._simulate_rewrite("哪些成分在中国是禁用的？"))
        assert result["business_type"] == "regulation"

    def test_ingredient_keywords(self):
        """成分关键词识别为 ingredient + ingredient"""
        rewriter = QueryRewriter()
        result = json.loads(rewriter._simulate_rewrite("玻色因的功效和浓度范围？"))
        assert result["business_type"] == "ingredient"
        assert result["intent"] == "ingredient"

    def test_ingredient_keywords_niacinamide(self):
        """烟酰胺识别为 ingredient"""
        rewriter = QueryRewriter()
        result = json.loads(rewriter._simulate_rewrite("烟酰胺的浓度是多少？"))
        assert result["business_type"] == "ingredient"

    def test_development_keywords(self):
        """研发关键词识别为 development + formulation"""
        rewriter = QueryRewriter()
        result = json.loads(rewriter._simulate_rewrite("如何设计一个保湿配方的防腐体系？"))
        assert result["business_type"] == "development"
        assert result["intent"] == "formulation"

    def test_product_keywords(self):
        """产品关键词识别为 product + product"""
        rewriter = QueryRewriter()
        result = json.loads(rewriter._simulate_rewrite("这个品牌的适用肤质是？"))
        assert result["business_type"] == "product"
        assert result["intent"] == "product"

    def test_general_query(self):
        """无特定关键词识别为 general"""
        rewriter = QueryRewriter()
        result = json.loads(rewriter._simulate_rewrite("你好，你是谁？"))
        assert result["business_type"] == "general"
        assert result["intent"] == "general"

    def test_output_has_required_fields(self):
        """输出包含所有必需字段"""
        rewriter = QueryRewriter()
        result = json.loads(rewriter._simulate_rewrite("测试查询"))
        required_fields = ["rewritten_query", "business_type", "intent", "requires_context"]
        for field in required_fields:
            assert field in result, f"缺少必需字段: {field}"

    def test_requires_context_is_true(self):
        """默认 requires_context 为 True"""
        rewriter = QueryRewriter()
        result = json.loads(rewriter._simulate_rewrite("测试"))
        assert result["requires_context"] is True


# ── JSON 解析测试 ──

class TestParseResponse:
    """测试 _parse_response JSON Schema 解析"""

    def test_parse_valid_json(self):
        """解析有效 JSON 字符串"""
        rewriter = QueryRewriter()
        text = json.dumps({
            "rewritten_query": "改写后的查询",
            "business_type": "regulation",
            "intent": "compliance",
            "requires_context": True,
            "standardized_entities": ["烟酰胺"],
        }, ensure_ascii=False)
        result = rewriter._parse_response(text, "原始查询")
        assert result is not None
        assert result.rewritten_query == "改写后的查询"
        assert result.business_type == "regulation"
        assert result.intent == "compliance"
        assert result.standardized_entities == ["烟酰胺"]

    def test_parse_json_with_surrounding_text(self):
        """JSON 嵌入在文本中时能正确提取"""
        rewriter = QueryRewriter()
        text = '根据分析，结果如下：\n{"rewritten_query":"test","business_type":"general","intent":"general","requires_context":false}\n以上是分析结果。'
        result = rewriter._parse_response(text, "query")
        assert result is not None
        assert result.rewritten_query == "test"

    def test_parse_invalid_json_returns_none(self):
        """无效 JSON 返回 None"""
        rewriter = QueryRewriter()
        result = rewriter._parse_response("这不是 JSON", "query")
        assert result is None

    def test_parse_empty_string_returns_none(self):
        """空字符串返回 None"""
        rewriter = QueryRewriter()
        result = rewriter._parse_response("", "query")
        assert result is None

    def test_parse_missing_required_field_uses_default(self):
        """缺少可选字段时使用默认值"""
        rewriter = QueryRewriter()
        text = json.dumps({
            "rewritten_query": "test",
            "business_type": "general",
            "intent": "general",
            "requires_context": True,
        })
        result = rewriter._parse_response(text, "query")
        assert result is not None
        assert result.standardized_entities == []  # 默认空列表
        assert result.confidence == 0.5  # 默认值

    def test_parse_incomplete_json_missing_rewritten_query(self):
        """缺少 rewritten_query 字段时返回默认值"""
        rewriter = QueryRewriter()
        text = json.dumps({
            "business_type": "ingredient",
            "intent": "ingredient",
            "requires_context": False,
        })
        result = rewriter._parse_response(text, "fallback_query")
        assert result is not None
        assert result.rewritten_query == "fallback_query"  # 使用原始查询作为兜底


# ── 逻辑一致性修正 ──

class TestLogicConsistencyFix:
    """测试 rewrite 方法中的逻辑一致性修正"""

    def test_regulation_intent_fixed_to_compliance(self):
        """regulation + ingredient intent 修正为 compliance"""
        rewriter = QueryRewriter()
        # 直接调用 rewrite，mock _call_llm 返回一个 regulation+ingredient 的结果
        mock_response = json.dumps({
            "rewritten_query": "test",
            "business_type": "regulation",
            "intent": "ingredient",
            "requires_context": True,
        })
        with patch.object(rewriter, "_call_llm", return_value=mock_response):
            result = rewriter.rewrite("测试法规")
        assert result.intent == "compliance"

    def test_regulation_formulation_intent_fixed_to_compliance(self):
        """regulation + formulation intent 修正为 compliance"""
        rewriter = QueryRewriter()
        mock_response = json.dumps({
            "rewritten_query": "test",
            "business_type": "regulation",
            "intent": "formulation",
            "requires_context": True,
        })
        with patch.object(rewriter, "_call_llm", return_value=mock_response):
            result = rewriter.rewrite("测试法规配方")
        assert result.intent == "compliance"

    def test_non_regulation_intent_not_modified(self):
        """非 regulation 类型的 intent 不被修正"""
        rewriter = QueryRewriter()
        mock_response = json.dumps({
            "rewritten_query": "test",
            "business_type": "ingredient",
            "intent": "formulation",
            "requires_context": True,
        })
        with patch.object(rewriter, "_call_llm", return_value=mock_response):
            result = rewriter.rewrite("测试成分")
        assert result.intent == "formulation"  # 不被修正


# ── 降级兜底逻辑 ──

class TestFallbackLogic:
    """测试 JSON 解析失败时的降级策略"""

    def test_retry_on_first_parse_failure(self):
        """第一次解析失败时重试一次（temperature=0）"""
        rewriter = QueryRewriter()
        fail_response = "这不是有效 JSON"
        success_response = json.dumps({
            "rewritten_query": "retry_ok",
            "business_type": "general",
            "intent": "general",
            "requires_context": True,
        })
        mock_obj = patch.object(rewriter, "_call_llm", side_effect=[fail_response, success_response])
        mock = mock_obj.start()
        try:
            result = rewriter.rewrite("测试重试")
            assert result is not None
            assert result.rewritten_query == "retry_ok"
            # 第一次调用用默认 temperature，第二次用 temperature=0
            assert mock.call_count == 2
        finally:
            mock_obj.stop()

    def test_raise_on_double_parse_failure(self):
        """两次解析都失败时抛出异常"""
        rewriter = QueryRewriter()
        with patch.object(rewriter, "_call_llm", return_value="invalid json both times"):
            with pytest.raises(Exception, match="两次失败"):
                rewriter.rewrite("测试双失败")

    def test_retry_uses_temperature_zero(self):
        """重试时使用 temperature=0"""
        rewriter = QueryRewriter()
        mock_obj = patch.object(rewriter, "_call_llm", side_effect=["bad", '{"rewritten_query":"ok","business_type":"general","intent":"general","requires_context":true}'])
        mock = mock_obj.start()
        try:
            rewriter.rewrite("测试")
            # 第二次调用应传入 temperature=0
            second_call = mock.call_args_list[1]
            assert second_call[1].get("temperature") == 0
        finally:
            mock_obj.stop()


# ── Schema 验证 ──

class TestSchemaDefinition:
    """验证 REWRITE_SCHEMA 的完整性"""

    def test_schema_has_required_fields(self):
        """Schema 定义了所有必需字段"""
        assert "rewritten_query" in REWRITE_SCHEMA["required"]
        assert "business_type" in REWRITE_SCHEMA["required"]
        assert "intent" in REWRITE_SCHEMA["required"]
        assert "requires_context" in REWRITE_SCHEMA["required"]

    def test_business_type_enum_values(self):
        """business_type 枚举值包含所有已知类型"""
        valid_types = set(REWRITE_SCHEMA["properties"]["business_type"]["enum"])
        expected = {"regulation", "development", "ingredient", "product", "general", "short"}
        assert expected.issubset(valid_types)

    def test_intent_enum_values(self):
        """intent 枚举值包含所有已知意图"""
        valid_intents = set(REWRITE_SCHEMA["properties"]["intent"]["enum"])
        expected = {"compliance", "formulation", "ingredient", "product", "general"}
        assert expected.issubset(valid_intents)


# ── Rewrite 端到端 ──

class TestRewriteEndToEnd:
    """端到端 rewrite 测试（mock LLM 调用）"""

    def test_rewrite_returns_query_rewrite_result(self):
        """rewrite 返回正确类型的 QueryRewriteResult"""
        rewriter = QueryRewriter()
        mock_response = json.dumps({
            "rewritten_query": "改写后的成分查询",
            "business_type": "ingredient",
            "intent": "ingredient",
            "requires_context": True,
            "standardized_entities": ["烟酰胺", "玻色因"],
        })
        with patch.object(rewriter, "_call_llm", return_value=mock_response):
            result = rewriter.rewrite("烟酰胺和玻色因的区别？")
        assert isinstance(result, QueryRewriteResult)
        assert result.standardized_entities == ["烟酰胺", "玻色因"]

    def test_rewrite_with_dialog_history(self):
        """rewrite 传递对话历史给 LLM"""
        rewriter = QueryRewriter()
        mock_response = json.dumps({
            "rewritten_query": "ok",
            "business_type": "general",
            "intent": "general",
            "requires_context": True,
        })
        dialogs = ["第1轮对话", "第2轮对话", "第3轮对话"]
        with patch.object(rewriter, "_call_llm", return_value=mock_response) as mock:
            rewriter.rewrite("新问题", recent_dialogs=dialogs)
        # 验证 prompt 包含对话历史
        call_args = mock.call_args[0][0]  # 第一个位置参数是 prompt
        assert "第1轮对话" in call_args
        assert "第3轮对话" in call_args
