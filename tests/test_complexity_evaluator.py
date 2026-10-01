"""复杂度评估器测试（规则兜底模式，无 GPU）。"""

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


class TestComplexityEvaluator:
    """验证复杂度评估器（规则兜底模式，无 GPU）。"""

    def test_regulation_query_is_complex(self):
        """法规类查询（含多个关键词）应被判定为复杂。"""
        from models.complexity_evaluator import ComplexityEvaluator

        evaluator = ComplexityEvaluator()
        result = evaluator.evaluate("化妆品中铅含量的限量标准是什么？")
        assert isinstance(result, bool)

    def test_simple_query_is_simple(self):
        """简单问候类查询应被判定为简单。"""
        from models.complexity_evaluator import ComplexityEvaluator

        evaluator = ComplexityEvaluator()
        result = evaluator.evaluate("你好")
        assert result is False

    def test_complex_query_with_multiple_keywords(self):
        """含多个复杂度关键词的查询应判定为复杂。"""
        from models.complexity_evaluator import ComplexityEvaluator

        evaluator = ComplexityEvaluator()
        result = evaluator.evaluate("请分析这个法规的合规性，是否在禁用清单中")
        assert result is True

    def test_ingredient_query_may_be_simple(self):
        """单一成分查询关键词不足 2 个应判定为简单。"""
        from models.complexity_evaluator import ComplexityEvaluator

        evaluator = ComplexityEvaluator()
        result = evaluator.evaluate("烟酰胺浓度")
        assert result is False

    def test_rule_fallback_mode(self):
        """无 BERT 模型时应使用规则兜底。"""
        from models.complexity_evaluator import ComplexityEvaluator

        evaluator = ComplexityEvaluator()
        assert evaluator._use_model is False
        assert evaluator._evaluate_with_rules("法规合规标准") is True
        assert evaluator._evaluate_with_rules("hi") is False
