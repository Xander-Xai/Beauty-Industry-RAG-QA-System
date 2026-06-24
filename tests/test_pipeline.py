"""
管线单元测试
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from unittest.mock import patch

import pytest


class TestConfig:
    def test_config_loads(self):
        """config.json 能正确加载"""
        with open("config.json", encoding="utf-8") as f:
            config = json.load(f)
        assert "system" in config
        assert "gpu0" in config
        assert "rbac" in config
        assert config["system"]["name"] == "化妆品行业RAG问答系统"

    def test_config_has_all_required_sections(self):
        """config.json 包含所有必要字段"""
        with open("config.json", encoding="utf-8") as f:
            config = json.load(f)
        required = ["system", "gpu0", "gpu1", "embedding", "qdrant",
                    "elasticsearch", "redis", "knowledge_base", "rbac"]
        for section in required:
            assert section in config, f"Missing section: {section}"


class TestRBAC:
    def test_is_allowed_public_doc(self):
        """公开文档所有用户可访问"""
        from auth.bitmask_rbac import is_allowed
        assert is_allowed(0, 1, 0, 0) == True
        assert is_allowed(0, 0, 0, 0) == True

    def test_is_allowed_role_filter(self):
        """角色过滤：doc_role_mask=0x7FFFFFFF 表示所有角色可访问"""
        from auth.bitmask_rbac import is_allowed
        assert is_allowed(1, 2147483647, 0, 0) == True   # user_role & doc_role != 0
        assert is_allowed(2147483647, 1, 0, 0) == True   # 0x7FFFFFFF & 1 = 1 != 0

    def test_encode_role_mask(self):
        """角色掩码编码"""
        from auth.bitmask_rbac import encode_role_mask
        assert encode_role_mask(["admin"]) == 2147483647
        assert encode_role_mask(["rd"]) == 1
        assert encode_role_mask(["rd", "quality"]) == 3


class TestQueryRewriter:
    def test_simulate_rewrite_regulation(self):
        """模拟 Rewrite：法规类关键词识别"""
        from rewrite.query_rewriter import QueryRewriter
        rewriter = QueryRewriter()
        result = rewriter._simulate_rewrite("烟酰胺在化妆品中的使用法规是什么？")
        data = json.loads(result)
        assert data["business_type"] == "regulation"
        assert data["intent"] == "compliance"

    def test_simulate_rewrite_ingredient(self):
        """模拟 Rewrite：成分类关键词识别"""
        from rewrite.query_rewriter import QueryRewriter
        rewriter = QueryRewriter()
        result = rewriter._simulate_rewrite("玻色因的功效和浓度范围？")
        data = json.loads(result)
        assert data["business_type"] == "ingredient"

    def test_simulate_rewrite_development(self):
        """模拟 Rewrite：研发类关键词识别"""
        from rewrite.query_rewriter import QueryRewriter
        rewriter = QueryRewriter()
        result = rewriter._simulate_rewrite("如何设计一个保湿配方的防腐体系？")
        data = json.loads(result)
        assert data["business_type"] == "development"


class TestComplexityEvaluator:
    def test_evaluate_with_rules_simple(self):
        """复杂度评估：简单查询"""
        from models.complexity_evaluator import ComplexityEvaluator
        evaluator = ComplexityEvaluator()
        is_complex = evaluator._evaluate_with_rules("烟酰胺安全吗？")
        assert is_complex == False

    def test_evaluate_with_rules_complex(self):
        """复杂度评估：复杂查询"""
        from models.complexity_evaluator import ComplexityEvaluator
        evaluator = ComplexityEvaluator()
        is_complex = evaluator._evaluate_with_rules(
            "根据《化妆品安全技术规范》，烟酰胺和A醇的复配使用有何法规限制？"
        )
        assert is_complex == True


class TestKVAdmission:
    def test_admission_critical(self):
        """KV 准入：压力 > 0.97 + P2 时返回 critical_p2_rejected"""
        from admission.kv_admission import KVAdmissionControl
        control = KVAdmissionControl()
        with patch.object(control, "_pressure_unlocked", return_value=0.98), \
             patch("admission.kv_admission.log_audit_event"):
            admitted, reason, priority = control.admit("req_test", 1000, 512, "general")
        assert admitted == False
        assert reason == "critical_p2_rejected"
        assert priority == "P2"

    def test_admission_budget_exceeded(self):
        """KV 准入：预算未超限时 admitted"""
        from admission.kv_admission import KVAdmissionControl
        control = KVAdmissionControl()
        admitted, reason, priority = control.admit("req_1", 5000, 512, "general")
        assert admitted == True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
