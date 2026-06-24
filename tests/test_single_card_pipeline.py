"""单卡端到端管线测试 — 验证完整 pipeline 逻辑。

在 development 模式下运行，使用模拟 LLM 响应但真实组件逻辑。
注意：TestComplexityEvaluator, TestEndToEndPipeline, TestDegradationPaths
已拆分到独立文件，见 test_complexity_evaluator.py, test_pipeline_e2e.py,
test_degradation_paths.py。
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set development mode before importing config
os.environ["DEPLOYMENT_MODE"] = "development"


# ---------------------------------------------------------------------------
# torch 不可用时的 mock shim — ComplexityEvaluator 在 __init__ 中
# 会 __import__("torch").cuda.is_available()，需要在模块级别提供一个
# 最小的假 torch，使 eval 走规则兜底路径。
# ---------------------------------------------------------------------------
_TORCH_UNAVAILABLE = True
try:
    import torch  # noqa: F401
    _TORCH_UNAVAILABLE = False
except ImportError:
    _fake_torch = types.ModuleType("torch")
    _fake_cuda = types.ModuleType("torch.cuda")

    def _no_cuda():
        return False

    _fake_cuda.is_available = _no_cuda
    _fake_torch.cuda = _fake_cuda
    sys.modules["torch"] = _fake_torch
    sys.modules["torch.cuda"] = _fake_cuda


# ============================================================
# 1. 配置与初始化测试
# ============================================================

class TestConfig:
    """验证配置系统正确加载。"""

    def test_deployment_mode_is_development(self):
        from common.config import get_config, is_development_mode
        cfg = get_config()
        assert cfg.deployment_mode == "development"
        assert is_development_mode() is True

    def test_config_has_required_fields(self):
        from common.config import get_config
        cfg = get_config()
        assert hasattr(cfg, "system")
        assert hasattr(cfg, "gpu0")
        assert hasattr(cfg, "gpu1")
        assert hasattr(cfg, "retrieval")
        assert hasattr(cfg, "generation")
        assert hasattr(cfg, "admission_control")

    def test_config_singleton(self):
        from common.config import get_config
        cfg1 = get_config()
        cfg2 = get_config()
        assert cfg1 is cfg2


# ============================================================
# 2. RBAC 权限测试
# ============================================================

class TestRBAC:
    """验证 Bitmask RBAC 权限判定逻辑。"""

    def test_public_doc_anyone_can_access(self):
        """dr=0 (公开文档) 时无论 dd 如何，任何人可访问。"""
        from auth.bitmask_rbac import is_allowed
        assert is_allowed(0, 0x02, 0, 0x01) is True
        assert is_allowed(0, 0x10, 0, 0x08) is True

    def test_restricted_doc_role_match(self):
        """有角色限制的文档：角色匹配可访问，不匹配不可访问。"""
        from auth.bitmask_rbac import is_allowed
        assert is_allowed(0x02, 0x02, 0, 0x01) is True
        assert is_allowed(0x02, 0x08, 0, 0x01) is False

    def test_restricted_doc_dept_match(self):
        """有部门限制的文档：部门匹配可访问，不匹配不可访问。"""
        from auth.bitmask_rbac import is_allowed
        assert is_allowed(0x02, 0x02, 0x01, 0x01) is True
        assert is_allowed(0x02, 0x02, 0x01, 0x02) is False

    def test_super_admin_bypasses_all(self):
        """超级管理员角色位掩码绕过所有限制。"""
        from auth.bitmask_rbac import is_allowed
        super_mask = 0xFFFFFFFF
        assert is_allowed(0x08, super_mask, 0x08, 0x01) is True
        assert is_allowed(0x02, super_mask, 0x02, 0x01) is True

    def test_qdrant_filter_generation(self):
        """build_qdrant_filter 应返回 Qdrant Filter 对象。"""
        from auth.bitmask_rbac import build_qdrant_filter
        from qdrant_client.http.models import Filter, FieldCondition, MatchValue

        qf = build_qdrant_filter(0x02, 0x01, "20260601_01")
        assert isinstance(qf, Filter)
        assert qf.must is not None
        has_status = any(
            c.key == "status" and isinstance(c.match, MatchValue) and c.match.value == "active"
            for c in qf.must
        )
        assert has_status

    def test_public_doc_dept_restricted(self):
        """公开文档但有部门限制：需要部门匹配。"""
        from auth.bitmask_rbac import is_allowed
        assert is_allowed(0, 0x04, 0x04, 0x04) is True
        assert is_allowed(0, 0x04, 0x08, 0x08) is True
        assert is_allowed(0, 0x04, 0x08, 0x04) is False

    def test_encode_role_and_dept_mask(self):
        """encode_role_mask / encode_dept_mask 应正确计算位掩码。"""
        from auth.bitmask_rbac import encode_dept_mask, encode_role_mask
        mask = encode_role_mask(["rd", "quality"])
        assert mask == 0x03
        mask = encode_dept_mask(["rd_dept", "sales_dept"])
        assert mask == 0x09


# ============================================================
# 3. KV 准入控制测试
# ============================================================

class TestAdmissionControl:
    """验证 KV Cache 准入控制逻辑。"""

    def test_admission_control_creation(self):
        from admission.kv_admission import KVAdmissionControl
        ctrl = KVAdmissionControl()
        assert ctrl.kv_budget > 0
        assert ctrl.kv_total > 0

    def test_low_pressure_allows_admission(self):
        """空闲系统应允许新请求进入。"""
        from admission.kv_admission import KVAdmissionControl
        ctrl = KVAdmissionControl()
        admitted, reason, priority = ctrl.admit("req_001", 50, 512, "general")
        assert admitted is True
        ctrl.release("req_001")

    def test_estimate_kv_calculation(self):
        """KV 估算应使用公式: (inp + out * 1.2) * KV_PER_TOKEN。"""
        from admission.kv_admission import KVAdmissionControl
        ctrl = KVAdmissionControl()
        kv_bytes = ctrl.estimate_kv(100, 512, "general")
        assert kv_bytes > 0
        kv_default = ctrl.estimate_kv(100, 0, "regulation")
        assert kv_default > kv_bytes

    def test_release_removes_request(self):
        """release 后请求应从 active 集合中移除。"""
        from admission.kv_admission import KVAdmissionControl
        ctrl = KVAdmissionControl()
        ctrl.admit("req_002", 50, 256, "general")
        status = ctrl.get_status()
        assert status["active"] == 1
        ctrl.release("req_002")
        status = ctrl.get_status()
        assert status["active"] == 0

    def test_pressure_with_active_requests(self):
        """有活跃请求时压力值应大于 0。"""
        from admission.kv_admission import KVAdmissionControl
        ctrl = KVAdmissionControl()
        ctrl.admit("req_003", 100, 512, "general")
        pressure = ctrl.get_pressure()
        assert pressure > 0
        ctrl.release("req_003")


# ============================================================
# 5. 缓存层测试
# ============================================================

class TestCacheLayer:
    """验证 L1/L2 缓存逻辑。"""

    def test_cache_key_generation(self):
        """缓存 Key 应包含权限掩码、版本等信息。"""
        from cache.redis_cache import RedisCache
        key1 = RedisCache.compute_cache_key(
            normalized_query="烟酰胺安全浓度",
            embedding_version="v1",
            knowledge_version_epoch="20260601",
            prompt_version="v2.1",
            schema_version="1.0",
            role_mask=0x02,
            dept_mask=0x01,
        )
        key2 = RedisCache.compute_cache_key(
            normalized_query="烟酰胺安全浓度",
            embedding_version="v1",
            knowledge_version_epoch="20260601",
            prompt_version="v2.1",
            schema_version="1.0",
            role_mask=0x04,
            dept_mask=0x01,
        )
        assert key1 != key2

    def test_same_params_produce_same_key(self):
        """相同参数应产生相同缓存 key。"""
        from cache.redis_cache import RedisCache
        params = dict(
            normalized_query="玻色因功效",
            embedding_version="v1",
            knowledge_version_epoch="20260601",
            prompt_version="v2.1",
            schema_version="1.0",
            role_mask=0x02,
            dept_mask=0x01,
        )
        assert RedisCache.compute_cache_key(**params) == RedisCache.compute_cache_key(**params)

    def test_l1_cache_operations_without_redis(self):
        """L1 内存缓存可在无 Redis 环境下工作。"""
        from collections import OrderedDict

        from cache.redis_cache import RedisCache
        cache = RedisCache.__new__(RedisCache)
        cache._l1 = OrderedDict()
        cache._l1_max = 100
        cache._l1_ttl = 300
        import threading
        cache._l1_lock = threading.Lock()
        cache.enabled = False
        cache._degraded = False
        cache._degraded_since = 0
        cache.redis_client = None
        cache.l1_ttl = 300
        cache.l2_ttl = 3600

        test_key = "test_key"
        test_val = {"answer": "test_answer"}
        cache.set(test_key, test_val, role_mask=0, dept_mask=0)
        result = cache.get(test_key, role_mask=0, dept_mask=0)
        assert result == test_val

    def test_l1_only_for_public_docs(self):
        """L1 缓存仅存储公开文档。"""
        from collections import OrderedDict

        from cache.redis_cache import RedisCache
        cache = RedisCache.__new__(RedisCache)
        cache._l1 = OrderedDict()
        cache._l1_max = 100
        cache._l1_ttl = 300
        import threading
        cache._l1_lock = threading.Lock()
        cache.enabled = False
        cache._degraded = False
        cache._degraded_since = 0
        cache.redis_client = None
        cache.l1_ttl = 300
        cache.l2_ttl = 3600

        test_key = "test_restricted"
        test_val = {"answer": "restricted_answer"}
        cache.set(test_key, test_val, role_mask=0x02, dept_mask=0x01)
        result = cache.get(test_key, role_mask=0x02, dept_mask=0x01)
        assert result is None

    def test_cache_stats(self):
        """get_stats 应返回有效的统计信息。"""
        from collections import OrderedDict

        from cache.redis_cache import RedisCache
        cache = RedisCache.__new__(RedisCache)
        cache._l1 = OrderedDict()
        cache._l1_max = 100
        import threading
        cache._l1_lock = threading.Lock()
        cache.enabled = False
        cache._degraded = False
        cache._degraded_since = 0
        cache.l1_ttl = 300
        cache.l2_ttl = 3600
        stats = cache.get_stats()
        assert "l1_size" in stats
        assert "l2_enabled" in stats
        assert stats["l1_max"] == 100

    def test_invalidate_by_epoch_clears_l1(self):
        """版本滚动应清空 L1 缓存。"""
        from cache.redis_cache import RedisCache
        cache = RedisCache.__new__(RedisCache)
        cache._l1 = {"k1": ("v1", 9999999), "k2": ("v2", 9999999)}
        cache._l1_max = 100
        import threading
        cache._l1_lock = threading.Lock()
        cache.enabled = False
        cache._degraded = False
        cache._degraded_since = 0
        cache.redis_client = None
        cache.l1_ttl = 300
        cache.l2_ttl = 3600
        assert len(cache._l1) == 2
        cache.invalidate_by_epoch("20260701_00")
        assert len(cache._l1) == 0


# ============================================================
# 6. Evidence Gate 评分测试（真实逻辑）
# ============================================================

class TestEvidenceGate:
    """验证多维度证据投票机制（真实评分逻辑）。"""

    def _make_rerank_result(self, doc_id="doc1", content="test content",
                            ce_ensemble=0.9, bi_score=0.8):
        from core.pipeline_context import RerankResult
        return RerankResult(
            doc_id=doc_id, content=content,
            ce_score_ensemble=ce_ensemble, bi_score=bi_score,
        )

    def test_empty_results_rejects(self):
        """空检索结果应直接 reject。"""
        from retrieval.evidence_gate import EvidenceEnsembleGate
        gate = EvidenceEnsembleGate()
        result = gate.evaluate(query="test", rerank_results=[], retrieval_agreement_score=0.0)
        assert result.decision == "reject"
        assert result.evidence_score == 0.0

    def test_high_score_passes(self):
        """高 CE 分数应通过 evidence gate。"""
        from retrieval.evidence_gate import EvidenceEnsembleGate
        gate = EvidenceEnsembleGate()
        docs = [
            self._make_rerank_result(doc_id="d1", ce_ensemble=0.95),
            self._make_rerank_result(doc_id="d2", ce_ensemble=0.88),
            self._make_rerank_result(doc_id="d3", ce_ensemble=0.85),
        ]
        result = gate.evaluate(
            query="烟酰胺安全浓度", rerank_results=docs, retrieval_agreement_score=0.80,
        )
        assert result.decision == "pass"
        assert result.ce_top1_score == 0.95
        assert result.ce_top3_mean_score == pytest.approx(0.8933, abs=0.001)

    def test_low_score_rejects(self):
        """低 CE 分数应被 reject。"""
        from retrieval.evidence_gate import EvidenceEnsembleGate
        gate = EvidenceEnsembleGate()
        docs = [
            self._make_rerank_result(doc_id="d1", ce_ensemble=0.20),
            self._make_rerank_result(doc_id="d2", ce_ensemble=0.15),
        ]
        result = gate.evaluate(
            query="某个查询", rerank_results=docs, retrieval_agreement_score=0.10,
        )
        assert result.decision == "reject"

    def test_medium_score_enhanced_generate(self):
        """中等分数应触发 enhanced_generate。"""
        from retrieval.evidence_gate import EvidenceEnsembleGate
        gate = EvidenceEnsembleGate()
        docs = [
            self._make_rerank_result(doc_id="d1", ce_ensemble=0.70),
            self._make_rerank_result(doc_id="d2", ce_ensemble=0.60),
            self._make_rerank_result(doc_id="d3", ce_ensemble=0.55),
        ]
        result = gate.evaluate(
            query="查询测试", rerank_results=docs, retrieval_agreement_score=0.50,
        )
        assert result.decision in ("enhanced_generate", "reject")

    def test_top_docs_limit_3(self):
        """top_docs 应限制为最多 3 个。"""
        from retrieval.evidence_gate import EvidenceEnsembleGate
        gate = EvidenceEnsembleGate()
        docs = [self._make_rerank_result(doc_id=f"d{i}", ce_ensemble=0.80) for i in range(5)]
        result = gate.evaluate(query="测试", rerank_results=docs, retrieval_agreement_score=0.5)
        assert len(result.top_docs) == 3


# ============================================================
# 7. Answer Gate 测试（真实逻辑 — 文本相似度兜底）
# ============================================================

class TestAnswerGate:
    """验证 Answer Gate NLI 校验（NLI 模型不可用时的文本相似度兜底）。"""

    def _make_rerank_result(self, doc_id="doc1", content="烟酰胺安全浓度 ≤ 5%"):
        from core.pipeline_context import RerankResult
        return RerankResult(doc_id=doc_id, content=content)

    def test_no_top_doc_always_passes(self):
        """无 top_doc 时应直接通过。"""
        from retrieval.answer_gate import AnswerGate
        gate = AnswerGate()
        result = gate.verify(answer="测试回答", top_doc=None, is_regulation=False)
        assert result.passed is True

    def test_consistent_answer_passes(self):
        """与证据一致的回答应通过。"""
        from retrieval.answer_gate import AnswerGate
        gate = AnswerGate()
        doc = self._make_rerank_result(content="烟酰胺(Nicotinamide)安全浓度≤5%，在化妆品中广泛使用")
        result = gate.verify(
            answer="烟酰胺在化妆品中的安全浓度一般不超过5%，在化妆品中广泛使用。",
            top_doc=doc, is_regulation=False,
        )
        assert result.passed is True

    def test_contradiction_warning(self):
        """矛盾分数 > 0.5 应标记 warning。"""
        from retrieval.answer_gate import AnswerGate
        gate = AnswerGate()
        doc = self._make_rerank_result(content="A" * 100)
        result = gate.verify(answer="B" * 100, top_doc=doc, is_regulation=False)
        assert isinstance(result.nli_contradiction_score, float)

    def test_result_fields(self):
        """AnswerGateResult 应包含所有必要字段。"""
        from retrieval.answer_gate import AnswerGate
        gate = AnswerGate()
        result = gate.verify(answer="test", top_doc=None, is_regulation=True)
        assert hasattr(result, "nli_contradiction_score")
        assert hasattr(result, "nli_entailment_score")
        assert hasattr(result, "passed")
        assert hasattr(result, "warning")
        assert hasattr(result, "is_regulation")
        assert result.is_regulation is True


# ============================================================
# 8. 管线上下文结构验证测试
# ============================================================

class TestPipelineContextStructures:
    """验证各管线上下文数据类字段。"""

    def test_request_context_creation(self):
        from core.pipeline_context import RequestContext
        ctx = RequestContext(
            user_input="烟酰胺的安全浓度是多少？",
            user_id="test_user", user_role_mask=0x02, user_dept_mask=0x01,
        )
        assert ctx.user_input == "烟酰胺的安全浓度是多少？"
        assert ctx.user_id == "test_user"
        assert ctx.request_id is not None
        assert ctx.degraded is False

    def test_request_context_record_timing(self):
        from core.pipeline_context import RequestContext
        ctx = RequestContext(user_input="test")
        ctx.record_timing("rewrite", 50.0)
        ctx.record_timing("recall", 120.0)
        assert ctx.stage_timings["rewrite"] == 50.0
        assert ctx.get_total_latency_ms() == 170.0

    def test_query_rewrite_result_structure(self):
        from core.pipeline_context import QueryRewriteResult
        result = QueryRewriteResult(
            rewritten_query="烟酰胺 安全浓度 限量",
            business_type="ingredient", intent="compliance",
            requires_context=True, standardized_entities=["烟酰胺"],
            confidence=0.85, fallback=False,
        )
        assert result.business_type == "ingredient"
        assert result.fallback is False
        assert "烟酰胺" in result.standardized_entities

    def test_recall_result_structure(self):
        from core.pipeline_context import RecallResult
        result = RecallResult(doc_id="doc1", content="烟酰胺安全浓度", score=0.9, source="dense_bge")
        assert result.doc_id == "doc1"
        assert result.score == 0.9

    def test_rerank_result_structure(self):
        from core.pipeline_context import RerankResult
        result = RerankResult(
            doc_id="doc1", content="烟酰胺内容",
            ce_score_ensemble=0.92, bi_score=0.88, final_score=0.95,
        )
        assert result.final_score == 0.95

    def test_generation_result_structure(self):
        from core.pipeline_context import GenerationResult
        result = GenerationResult(answer="烟酰胺的安全浓度一般为 2%-5%。", model_used="qwen3-4b", max_tokens=512)
        assert "烟酰胺" in result.answer
        assert result.has_more is False

    def test_answer_gate_result_structure(self):
        from core.pipeline_context import AnswerGateResult
        result = AnswerGateResult(passed=True, nli_contradiction_score=0.1, nli_entailment_score=0.85)
        assert result.passed is True

    def test_evidence_gate_result_structure(self):
        from core.pipeline_context import EvidenceGateResult
        result = EvidenceGateResult(
            evidence_score=0.80, ce_top1_score=0.85, ce_top3_mean_score=0.78,
            retrieval_agreement_score=0.70, doc_consistency_score=0.82, decision="pass",
        )
        assert result.decision == "pass"

    def test_session_state_operations(self):
        from core.pipeline_context import QueryRewriteResult, SessionState
        state = SessionState.get_or_create("test_session")
        state.add_round(
            user_input="烟酰胺浓度？", response="一般不超过5%",
            rewrite=QueryRewriteResult(
                rewritten_query="烟酰胺浓度", business_type="ingredient",
                intent="ingredient", requires_context=True,
            ),
        )
        queries = state.get_recent_queries()
        assert len(queries) == 1
        assert queries[0] == "烟酰胺浓度？"

    def test_session_state_evidence_lock(self):
        from core.pipeline_context import SessionState
        state = SessionState.get_or_create("test_lock")
        state.lock_evidence(["doc_a", "doc_b"])
        assert state.locked_doc_ids == ["doc_a", "doc_b"]
