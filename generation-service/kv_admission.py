"""
KV Cache 准入控制

核心机制：
- 每个请求估算 KV Cache 消耗
- 全局 KV Budget 上限管控
- 三级拒绝策略：soft_stop / critical / budget_exceeded
"""

import json
import logging
import os
import threading

sys_path_done = False
try:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys_path_done = True
except Exception:
    pass

with open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"),
    encoding="utf-8",
) as f:
    config = json.load(f)

logger = logging.getLogger(__name__)


class KVAdmissionControl:
    """
    KV Cache 准入控制

    机制：
    - 估算每个请求的 KV Cache 消耗（基于 input_tokens + output_tokens）
    - 维护全局 active requests 的 KV 占用
    - 超过 budget 时拒绝新请求
    """

    KV_PER_TOKEN = 0.45 * 1024  # 每 token 的 KV 大小（字节）

    OUTPUT_MAP = {
        "regulation": 1024,
        "formulation": 768,
        "ingredient": 512,
        "general": 256,
    }

    def __init__(self):
        kv_gb = config["gpu0"]["models"]["gen_14b"]["kv_cache_budget_gb"]
        sf = config["admission_control"]["safety_factor"]
        self.kv_budget = int(kv_gb * 1024**3 * sf)
        self.kv_total = int(kv_gb * 1024**3)
        self.active = {}  # {request_id: (input_tokens, output_tokens, business_type)}
        self._lock = threading.Lock()
        logger.info(
            f"KVAdmissionControl 初始化: budget={self.kv_budget / 1024**3:.2f}GB, "
            f"total={self.kv_total / 1024**3:.2f}GB"
        )

    def estimate_kv(self, inp: int, out: int, bt: str) -> float:
        """估算单个请求的 KV Cache 消耗（字节）"""
        if out == 0:
            out = self.OUTPUT_MAP.get(bt, 512)
        return (inp + out * 1.2) * self.KV_PER_TOKEN

    def get_pressure(self) -> float:
        """获取当前 KV Cache 压力（0.0 - 1.0）"""
        usage = sum(
            self.estimate_kv(v[0], v[1], v[2]) for v in self.active.values()
        )
        return usage / self.kv_total if self.kv_total > 0 else 0.0

    def admit(
        self, request_id: str, input_tokens: int, output_tokens: int, business_type: str
    ) -> tuple[bool, str]:
        """
        准入判断

        Returns:
            (admitted: bool, reason: str)
        """
        pressure = self.get_pressure()

        # 三级拒绝策略
        if pressure > 0.95:
            return False, "critical"
        if pressure > 0.9:
            return False, "soft_stop"
        if pressure > 0.8:
            return True, "admitted_with_pressure"

        # Budget 检查
        est = self.estimate_kv(input_tokens, output_tokens, business_type)
        cur = sum(self.estimate_kv(v[0], v[1], v[2]) for v in self.active.values())
        if cur + est > self.kv_budget:
            return False, "budget_exceeded"

        # 准入
        with self._lock:
            self.active[request_id] = (input_tokens, output_tokens, business_type)
        return True, "admitted"

    def release(self, request_id: str):
        """释放请求的 KV Cache"""
        with self._lock:
            self.active.pop(request_id, None)

    def get_status(self) -> dict:
        """获取当前状态"""
        return {
            "kv_pressure": round(self.get_pressure(), 3),
            "active": len(self.active),
            "kv_budget_gb": round(self.kv_budget / 1024**3, 2),
            "kv_total_gb": round(self.kv_total / 1024**3, 2),
        }
