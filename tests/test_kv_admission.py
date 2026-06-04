"""
KV 准入控制测试 (admission/kv_admission.py)

覆盖 §5.2 KV Cache 准入控制逻辑：
- KV 压力计算
- 准入/拒绝决策（critical / soft_stop / admitted_with_pressure / budget_exceeded / admitted）
- 压力阈值分层降级
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
    """测试 KV 估算逻辑"""

    def test_estimate_kv_with_explicit_output(self):
        """指定 output tokens 时的 KV 估算"""
        ctrl = KVAdmissionControl()
        # estimate_kv(inp, out, bt) = (inp + out * 1.2) * KV_PER_TOKEN
        kv_per_token = 0.45 * 1024  # 460.8
        expected = (100 + 200 * 1.2) * kv_per_token  # 340 * 460.8 = 156672
        result = ctrl.estimate_kv(100, 200, "general")
        assert result == pytest.approx(expected, rel=1e-6)

    def test_estimate_kv_with_zero_output_uses_default(self):
        """output=0 时使用 business_type 的默认值"""
        ctrl = KVAdmissionControl()
        # general 默认 output=256
        expected = (50 + 256 * 1.2) * ctrl.KV_PER_TOKEN
        result = ctrl.estimate_kv(50, 0, "general")
        assert result == pytest.approx(expected, rel=1e-6)

    def test_estimate_kv_regulation_default_output(self):
        """regulation 类型默认 output=1024"""
        ctrl = KVAdmissionControl()
        expected = (50 + 1024 * 1.2) * ctrl.KV_PER_TOKEN
        result = ctrl.estimate_kv(50, 0, "regulation")
        assert result == pytest.approx(expected, rel=1e-6)

    def test_estimate_kv_ingredient_default_output(self):
        """ingredient 类型默认 output=512"""
        ctrl = KVAdmissionControl()
        expected = (50 + 512 * 1.2) * ctrl.KV_PER_TOKEN
        result = ctrl.estimate_kv(50, 0, "ingredient")
        assert result == pytest.approx(expected, rel=1e-6)

    def test_estimate_kv_formulation_default_output(self):
        """formulation 类型默认 output=768"""
        ctrl = KVAdmissionControl()
        expected = (50 + 768 * 1.2) * ctrl.KV_PER_TOKEN
        result = ctrl.estimate_kv(50, 0, "formulation")
        assert result == pytest.approx(expected, rel=1e-6)


# ── 压力阈值分层决策 ──

class TestAdmitDecision:
    """测试各压力等级的准入/拒绝决策

    通过 mock get_pressure() 来精确模拟不同压力等级，
    因为直接填充 KV 条目到 8GB 需要 ~10000 次迭代，不现实。
    """

    def test_admit_at_low_pressure(self):
        """低压力 (<0.8) 且预算未超：admitted"""
        ctrl = KVAdmissionControl()
        admitted, reason = ctrl.admit("req_001", 50, 128, "general")
        assert admitted is True
        assert reason == "admitted"

    def test_reject_at_critical_pressure(self):
        """压力 >0.95：critical 拒绝"""
        ctrl = KVAdmissionControl()
        with patch.object(ctrl, "_pressure_unlocked", return_value=0.96), \
             patch("admission.kv_admission.log_audit_event"):
            admitted, reason = ctrl.admit("req_test", 100, 256, "general")
        assert admitted is False
        assert reason == "critical"

    def test_reject_at_soft_stop_pressure(self):
        """压力在 0.9~0.95 之间：soft_stop 拒绝"""
        ctrl = KVAdmissionControl()
        with patch.object(ctrl, "_pressure_unlocked", return_value=0.92), \
             patch("admission.kv_admission.log_audit_event"):
            admitted, reason = ctrl.admit("req_test", 100, 256, "general")
        assert admitted is False
        assert reason == "soft_stop"

    def test_admitted_with_pressure(self):
        """压力在 0.8~0.9 之间：admitted_with_pressure"""
        ctrl = KVAdmissionControl()
        with patch.object(ctrl, "_pressure_unlocked", return_value=0.85):
            admitted, reason = ctrl.admit("req_test", 100, 256, "general")
        assert admitted is True
        assert reason == "admitted_with_pressure"

    def test_boundary_exactly_at_095(self):
        """压力恰好 = 0.95：不触发 critical（仅 > 0.95 才触发）"""
        ctrl = KVAdmissionControl()
        with patch.object(ctrl, "_pressure_unlocked", return_value=0.95):
            admitted, reason = ctrl.admit("req_test", 100, 256, "general")
        # 0.95 不 > 0.95，也不 > 0.9，进入 budget check
        # 但 0.95 > 0.9 -> True -> soft_stop
        assert admitted is False
        assert reason == "soft_stop"

    def test_boundary_exactly_at_09(self):
        """压力恰好 = 0.9：不触发 soft_stop（仅 > 0.9 才触发），但触发 admitted_with_pressure"""
        ctrl = KVAdmissionControl()
        with patch.object(ctrl, "_pressure_unlocked", return_value=0.9):
            admitted, reason = ctrl.admit("req_test", 100, 256, "general")
        # 0.9 不 > 0.9，不触发 soft_stop；但 0.9 > 0.8 -> admitted_with_pressure
        assert admitted is True
        assert reason == "admitted_with_pressure"

    def test_boundary_exactly_at_08(self):
        """压力恰好 = 0.8：不触发 admitted_with_pressure（仅 > 0.8 才触发）"""
        ctrl = KVAdmissionControl()
        with patch.object(ctrl, "_pressure_unlocked", return_value=0.8):
            admitted, reason = ctrl.admit("req_test", 100, 256, "general")
        # 0.8 不 > 0.8，进入 budget check
        assert admitted is True
        assert reason == "admitted"


# ── 预算限制 ──

class TestBudgetExceeded:
    """测试 KV 预算超限拒绝"""

    def test_budget_exceeded_at_normal_pressure(self):
        """压力 <0.8 但超出预算时拒绝"""
        ctrl = KVAdmissionControl()
        # 直接填充 active 使当前 KV 用量接近预算
        # budget = kv_budget (约 6GB)。预填充约 5.9GB，新请求约 0.4GB
        prefill_kv = ctrl.estimate_kv(1000, 512, "general")  # ~744KB
        needed = int(0.98 * ctrl.kv_budget / prefill_kv)
        for i in range(needed):
            ctrl.active[f"prefill_{i}"] = (1000, 512, "general")

        # 新请求估算约 0.4GB，当前用量 98% of budget，会超限
        admitted, reason = ctrl.admit("req_test", 1000000, 100000, "general")
        assert admitted is False
        assert reason == "budget_exceeded"

    def test_multiple_requests_within_budget(self):
        """多个小请求在预算内：全部 admitted"""
        ctrl = KVAdmissionControl()
        admitted1, _ = ctrl.admit("req_1", 50, 128, "general")
        admitted2, _ = ctrl.admit("req_2", 50, 128, "general")
        admitted3, _ = ctrl.admit("req_3", 50, 128, "general")
        assert admitted1 is True
        assert admitted2 is True
        assert admitted3 is True


# ── Release 与状态 ──

class TestReleaseAndStatus:
    """测试请求释放和状态查询"""

    def test_release_removes_active_request(self):
        """release 会移除活跃请求"""
        ctrl = KVAdmissionControl()
        ctrl.admit("req_001", 100, 256, "general")
        assert "req_001" in ctrl.active
        ctrl.release("req_001")
        assert "req_001" not in ctrl.active

    def test_release_nonexistent_is_safe(self):
        """release 不存在的请求 ID 不抛异常"""
        ctrl = KVAdmissionControl()
        ctrl.release("nonexistent_id")  # 不应抛出异常

    def test_get_status_returns_structure(self):
        """get_status 返回正确的状态结构"""
        ctrl = KVAdmissionControl()
        status = ctrl.get_status()
        assert "kv_pressure" in status
        assert "active" in status
        assert isinstance(status["kv_pressure"], float)
        assert isinstance(status["active"], int)

    def test_pressure_decreases_after_release(self):
        """释放请求后压力应降低"""
        ctrl = KVAdmissionControl()
        ctrl.admit("req_001", 1000, 512, "general")
        p_before = ctrl.get_pressure()
        ctrl.release("req_001")
        p_after = ctrl.get_pressure()
        assert p_after < p_before

    def test_initial_pressure_is_zero(self):
        """初始状态下压力为 0"""
        ctrl = KVAdmissionControl()
        assert ctrl.get_pressure() == 0.0

    def test_active_count_matches_admitted(self):
        """活跃请求计数与 admit 成功数匹配"""
        ctrl = KVAdmissionControl()
        ctrl.admit("a", 10, 10, "general")
        ctrl.admit("b", 10, 10, "general")
        ctrl.release("a")
        assert len(ctrl.active) == 1


# ── 并发安全性 ──

class TestConcurrency:
    """测试并发场景下的线程安全"""

    def test_concurrent_admit_and_release(self):
        """并发 admit/release 不导致数据竞争"""
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
        assert len(ctrl.active) == 0  # 所有请求已释放

    def test_concurrent_admit_unique_ids(self):
        """并发 admit 不同 ID：全部成功入库"""
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
