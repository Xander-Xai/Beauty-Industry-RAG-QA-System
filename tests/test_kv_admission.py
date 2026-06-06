"""
KV 准入控制测试 (admission/kv_admission.py)

覆盖 §5.2 KV Cache 准入控制逻辑：
- KV 压力计算
- 准入/拒绝决策（critical / soft_stop / admitted_with_truncation / admitted_with_tighten / budget_exceeded / admitted）
- PRD §5.2.5 / §9 阈值对齐 (0.70/0.80/0.90/0.95)
- P0/P1/P2 优先级差异化降级
- get_effective_max_tokens Prefix Caching 保护（不修改 max_tokens）
- get_truncation_tokens 应用层流式截断
- should_force_downgrade / should_reject_503 降级判定
- 并发安全性（threading.Lock）
- 释放与状态查询
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import threading
from unittest.mock import patch


from admission.kv_admission import KVAdmissionControl


# ── 辅助 fixtures ──

@pytest.fixture
def admission():
    """创建一个全新的 KVAdmissionControl 实例"""
    return KVAdmissionControl()


# ── KV 压力计算 ──

class TestEstimateKV:
    """测试 KV 估算逻辑（PRD §5.2.3 双因子模型）"""

    def test_estimate_kv_with_explicit_output(self):
        """指定 output tokens 时的 KV 估算: inp × prefill + out × decode"""
        ctrl = KVAdmissionControl()
        expected = 100 * ctrl.KV_PER_TOKEN_PREFILL + 200 * ctrl.KV_PER_TOKEN_DECODE
        result = ctrl.estimate_kv(100, 200, "general")
        assert result == pytest.approx(expected, rel=1e-6)

    def test_estimate_kv_with_zero_output_uses_default(self):
        """output=0 时使用 business_type 的默认值 (general=512)"""
        ctrl = KVAdmissionControl()
        expected = 50 * ctrl.KV_PER_TOKEN_PREFILL + 512 * ctrl.KV_PER_TOKEN_DECODE
        result = ctrl.estimate_kv(50, 0, "general")
        assert result == pytest.approx(expected, rel=1e-6)

    def test_estimate_kv_regulation_default_output(self):
        """regulation 类型默认 output=1024"""
        ctrl = KVAdmissionControl()
        expected = 50 * ctrl.KV_PER_TOKEN_PREFILL + 1024 * ctrl.KV_PER_TOKEN_DECODE
        result = ctrl.estimate_kv(50, 0, "regulation")
        assert result == pytest.approx(expected, rel=1e-6)


# ── PRD §9 阈值对齐测试 ──

class TestThresholdAlignment:
    """验证阈值与 PRD §5.2.5 / §9 规格一致"""

    def test_thresholds_match_prd(self):
        """PRD §5.2.5 规定: 70% tighten, 80% truncate, 90% soft_stop, 95% critical"""
        ctrl = KVAdmissionControl()
        assert ctrl.threshold_tighten == 0.70
        assert ctrl.threshold_truncate == 0.80
        assert ctrl.threshold_soft_stop == 0.90
        assert ctrl.threshold_critical == 0.95


# ── 压力阈值分层决策（PRD §5.2.5 优先级差异化）──

class TestAdmitDecision:
    """测试各压力等级的准入/拒绝决策（含 P0/P1/P2 优先级差异化，PRD §5.2.5 阈值）"""

    def test_admit_at_low_pressure(self):
        """低压力 (<0.70) 且预算未超：admitted"""
        ctrl = KVAdmissionControl()
        admitted, reason, priority = ctrl.admit("req_001", 50, 128, "general")
        assert admitted is True
        assert reason == "admitted"
        assert priority == "P2"

    def test_reject_at_critical_pressure_p2(self):
        """压力 >0.95 + P2：critical_p2_rejected"""
        ctrl = KVAdmissionControl()
        with patch.object(ctrl, "_pressure_unlocked", return_value=0.97), \
             patch("admission.kv_admission.log_audit_event"):
            admitted, reason, priority = ctrl.admit("req_test", 100, 256, "general")
        assert admitted is False
        assert reason == "critical_p2_rejected"
        assert priority == "P2"

    def test_admit_at_critical_pressure_p0(self):
        """压力 >0.95 + P0：admitted with critical (强制降级)"""
        ctrl = KVAdmissionControl()
        with patch.object(ctrl, "_pressure_unlocked", return_value=0.97), \
             patch("admission.kv_admission.log_audit_event"):
            admitted, reason, priority = ctrl.admit("req_test", 100, 256, "regulation")
        assert admitted is True
        assert reason == "critical"
        assert priority == "P0"

    def test_reject_at_critical_pressure_p1(self):
        """压力 >0.95 + P1：critical_p1_queued"""
        ctrl = KVAdmissionControl()
        with patch.object(ctrl, "_pressure_unlocked", return_value=0.97), \
             patch("admission.kv_admission.log_audit_event"):
            admitted, reason, priority = ctrl.admit("req_test", 100, 256, "development")
        assert admitted is False
        assert reason == "critical_p1_queued"
        assert priority == "P1"

    def test_reject_at_soft_stop_pressure_p2(self):
        """压力在 0.90~0.95 + P2：soft_stop 拒绝"""
        ctrl = KVAdmissionControl()
        with patch.object(ctrl, "_pressure_unlocked", return_value=0.94), \
             patch("admission.kv_admission.log_audit_event"):
            admitted, reason, priority = ctrl.admit("req_test", 100, 256, "general")
        assert admitted is False
        assert reason == "soft_stop"
        assert priority == "P2"

    def test_admit_at_soft_stop_pressure_p0(self):
        """压力在 0.90~0.95 + P0：admitted (P0 受保护)"""
        ctrl = KVAdmissionControl()
        with patch.object(ctrl, "_pressure_unlocked", return_value=0.94):
            admitted, reason, priority = ctrl.admit("req_test", 100, 256, "regulation")
        assert admitted is True
        assert reason == "admitted"
        assert priority == "P0"

    def test_admit_at_soft_stop_pressure_p1_downgrade(self):
        """压力在 0.90~0.95 + P1：admitted with downgrade_to_4b"""
        ctrl = KVAdmissionControl()
        with patch.object(ctrl, "_pressure_unlocked", return_value=0.94):
            admitted, reason, priority = ctrl.admit("req_test", 100, 256, "development")
        assert admitted is True
        assert reason == "downgrade_to_4b"
        assert priority == "P1"

    def test_admitted_with_truncation(self):
        """压力在 0.80~0.90：admitted_with_truncation（应用层截断）"""
        ctrl = KVAdmissionControl()
        with patch.object(ctrl, "_pressure_unlocked", return_value=0.85):
            admitted, reason, priority = ctrl.admit("req_test", 100, 256, "general")
        assert admitted is True
        assert reason == "admitted_with_truncation"

    def test_admitted_with_tighten(self):
        """压力在 0.70~0.80：admitted_with_tighten（限流器收紧）"""
        ctrl = KVAdmissionControl()
        with patch.object(ctrl, "_pressure_unlocked", return_value=0.75):
            admitted, reason, priority = ctrl.admit("req_test", 100, 256, "general")
        assert admitted is True
        assert reason == "admitted_with_tighten"

    def test_boundary_exactly_at_095(self):
        """压力恰好 = 0.95：不触发 critical（仅 > 0.95 才触发），但触发 soft_stop"""
        ctrl = KVAdmissionControl()
        with patch.object(ctrl, "_pressure_unlocked", return_value=0.95):
            admitted, reason, priority = ctrl.admit("req_test", 100, 256, "general")
        # 0.95 不 > 0.95，不触发 critical；但 0.95 > 0.90 -> soft_stop
        assert admitted is False
        assert reason == "soft_stop"

    def test_boundary_exactly_at_090(self):
        """压力恰好 = 0.90：不触发 soft_stop（仅 > 0.90 才触发），但触发 truncation"""
        ctrl = KVAdmissionControl()
        with patch.object(ctrl, "_pressure_unlocked", return_value=0.90):
            admitted, reason, priority = ctrl.admit("req_test", 100, 256, "general")
        # 0.90 不 > 0.90，不触发 soft_stop；但 0.90 > 0.80 -> admitted_with_truncation
        assert admitted is True
        assert reason == "admitted_with_truncation"

    def test_boundary_exactly_at_080(self):
        """压力恰好 = 0.80：不触发 truncation（仅 > 0.80 才触发），但触发 tighten"""
        ctrl = KVAdmissionControl()
        with patch.object(ctrl, "_pressure_unlocked", return_value=0.80):
            admitted, reason, priority = ctrl.admit("req_test", 100, 256, "general")
        # 0.80 不 > 0.80，不触发 truncation；但 0.80 > 0.70 -> tighten
        assert admitted is True
        assert reason == "admitted_with_tighten"

    def test_boundary_exactly_at_070(self):
        """压力恰好 = 0.70：不触发 tighten（仅 > 0.70 才触发），返回 admitted"""
        ctrl = KVAdmissionControl()
        with patch.object(ctrl, "_pressure_unlocked", return_value=0.70):
            admitted, reason, priority = ctrl.admit("req_test", 100, 256, "general")
        # 0.70 不 > 0.70，不触发 tighten
        assert admitted is True
        assert reason == "admitted"


# ── 预算限制 ──

class TestBudgetExceeded:
    """测试 KV 预算超限拒绝"""

    def test_budget_exceeded_at_normal_pressure(self):
        """压力正常但超出预算时拒绝"""
        ctrl = KVAdmissionControl()
        prefill_kv = ctrl.estimate_kv(1000, 512, "general")
        needed = int(0.98 * ctrl.kv_budget / prefill_kv)
        for i in range(needed):
            ctrl.active[f"prefill_{i}"] = (1000, 512, "general")
        # 填充后压力可能超过阈值触发 critical/soft_stop 而非 budget_exceeded
        # 因此 mock 压力为低值，单独测试 budget 逻辑
        with patch.object(ctrl, "_pressure_unlocked", return_value=0.50):
            admitted, reason, _ = ctrl.admit("req_test", 1000000, 100000, "general")
        assert admitted is False
        assert reason == "budget_exceeded"

    def test_multiple_requests_within_budget(self):
        """多个小请求在预算内：全部 admitted"""
        ctrl = KVAdmissionControl()
        admitted1, _, _ = ctrl.admit("req_1", 50, 128, "general")
        admitted2, _, _ = ctrl.admit("req_2", 50, 128, "general")
        admitted3, _, _ = ctrl.admit("req_3", 50, 128, "general")
        assert admitted1 is True
        assert admitted2 is True
        assert admitted3 is True


# ── Release 与状态 ──

class TestReleaseAndStatus:
    """测试请求释放和状态查询"""

    def test_release_removes_active_request(self):
        ctrl = KVAdmissionControl()
        ctrl.admit("req_001", 100, 256, "general")
        assert "req_001" in ctrl.active
        ctrl.release("req_001")
        assert "req_001" not in ctrl.active

    def test_release_nonexistent_is_safe(self):
        ctrl = KVAdmissionControl()
        ctrl.release("nonexistent_id")

    def test_initial_pressure_is_zero(self):
        ctrl = KVAdmissionControl()
        assert ctrl.get_pressure() == 0.0

    def test_pressure_decreases_after_release(self):
        ctrl = KVAdmissionControl()
        ctrl.admit("req_001", 1000, 512, "general")
        p_before = ctrl.get_pressure()
        ctrl.release("req_001")
        p_after = ctrl.get_pressure()
        assert p_after < p_before

    def test_active_count_matches_admitted(self):
        ctrl = KVAdmissionControl()
        ctrl.admit("a", 10, 10, "general")
        ctrl.admit("b", 10, 10, "general")
        ctrl.release("a")
        assert len(ctrl.active) == 1

    def test_get_status_returns_structure(self):
        ctrl = KVAdmissionControl()
        status = ctrl.get_status()
        assert "kv_pressure" in status
        assert "active" in status
        assert "kv_budget_mb" in status


# ── Prefix Caching 保护 + 应用层截断 ──

class TestPrefixCachingProtection:
    """PRD §9: get_effective_max_tokens 不修改 max_tokens（Prefix Caching 保护）"""

    def test_effective_max_tokens_unchanged_for_truncation(self):
        """admitted_with_truncation: max_tokens 不变（由流式截断控制）"""
        ctrl = KVAdmissionControl()
        assert ctrl.get_effective_max_tokens(1024, "admitted_with_truncation") == 1024
        assert ctrl.get_effective_max_tokens(512, "admitted_with_truncation") == 512

    def test_effective_max_tokens_unchanged_for_critical(self):
        """critical: max_tokens 不变"""
        ctrl = KVAdmissionControl()
        assert ctrl.get_effective_max_tokens(1024, "critical") == 1024
        assert ctrl.get_effective_max_tokens(2048, "critical") == 2048

    def test_effective_max_tokens_unchanged_for_all_reasons(self):
        """所有 reason: max_tokens 均不变"""
        ctrl = KVAdmissionControl()
        for reason in ["admitted", "admitted_with_tighten", "admitted_with_truncation",
                        "critical", "downgrade_to_4b", "soft_stop", "budget_exceeded"]:
            assert ctrl.get_effective_max_tokens(1024, reason) == 1024

    def test_truncation_tokens_for_truncation(self):
        """admitted_with_truncation: truncation_tokens = max_tokens // 2"""
        ctrl = KVAdmissionControl()
        assert ctrl.get_truncation_tokens(1024, "admitted_with_truncation") == 512
        assert ctrl.get_truncation_tokens(512, "admitted_with_truncation") == 256
        assert ctrl.get_truncation_tokens(256, "admitted_with_truncation") == 256

    def test_truncation_tokens_for_critical(self):
        """critical: truncation_tokens = max_tokens // 4"""
        ctrl = KVAdmissionControl()
        assert ctrl.get_truncation_tokens(1024, "critical") == 256
        assert ctrl.get_truncation_tokens(2048, "critical") == 512

    def test_truncation_tokens_none_for_normal(self):
        """admitted / tighten: 无需截断"""
        ctrl = KVAdmissionControl()
        assert ctrl.get_truncation_tokens(1024, "admitted") is None
        assert ctrl.get_truncation_tokens(1024, "admitted_with_tighten") is None


# ── should_force_downgrade / should_reject_503 ──

class TestDowngradeAndReject:
    """测试降级与 503 拒绝判定"""

    def test_should_force_downgrade(self):
        ctrl = KVAdmissionControl()
        assert ctrl.should_force_downgrade("critical") is True
        assert ctrl.should_force_downgrade("downgrade_to_4b") is True
        assert ctrl.should_force_downgrade("admitted") is False
        assert ctrl.should_force_downgrade("admitted_with_truncation") is False
        assert ctrl.should_force_downgrade("soft_stop") is False

    def test_should_reject_503(self):
        ctrl = KVAdmissionControl()
        assert ctrl.should_reject_503("critical_p2_rejected") is True
        assert ctrl.should_reject_503("critical") is False
        assert ctrl.should_reject_503("soft_stop") is False
        assert ctrl.should_reject_503("admitted") is False


# ── 并发安全性 ──

class TestConcurrency:
    """测试并发场景下的线程安全"""

    def test_concurrent_admit_and_release(self):
        ctrl = KVAdmissionControl()
        errors = []

        def worker(idx):
            try:
                rid = f"thread_req_{idx}"
                ctrl.admit(rid, 10, 10, "general")
                ctrl.release(rid)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(ctrl.active) == 0

    def test_concurrent_admit_unique_ids(self):
        ctrl = KVAdmissionControl()
        errors = []

        def worker(idx):
            try:
                rid = f"concurrent_{idx}"
                ctrl.admit(rid, 10, 10, "general")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(ctrl.active) == 50


# ── P0/P1/P2 优先级映射 ──

class TestPriorityMapping:
    """测试业务类型到优先级的映射"""

    def test_regulation_is_p0(self):
        ctrl = KVAdmissionControl()
        assert ctrl._get_priority("regulation") == "P0"

    def test_development_is_p1(self):
        ctrl = KVAdmissionControl()
        assert ctrl._get_priority("development") == "P1"

    def test_ingredient_is_p1(self):
        ctrl = KVAdmissionControl()
        assert ctrl._get_priority("ingredient") == "P1"

    def test_general_is_p2(self):
        ctrl = KVAdmissionControl()
        assert ctrl._get_priority("general") == "P2"

    def test_unknown_defaults_to_p2(self):
        ctrl = KVAdmissionControl()
        assert ctrl._get_priority("unknown_type") == "P2"
