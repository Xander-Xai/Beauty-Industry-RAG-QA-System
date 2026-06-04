import time, json, threading
from common.audit import log_audit_event
try:
    from common.config import get_config as _get_config
    _cfg = _get_config()
    _config = {
        "gpu0": {"models": {"gen_14b": {"kv_cache_budget_gb": _cfg.gpu.kv_cache_budget_gb}}},
        "admission_control": {"safety_factor": _cfg.admission.safety_factor},
    }
except Exception:
    _config_path = __import__("os").path.join(__import__("os").path.dirname(__file__), "..", "config.json")
    with open(_config_path, encoding="utf-8") as f:
        _config = json.load(f)

class KVAdmissionControl:
    KV_PER_TOKEN = 0.45 * 1024
    OUTPUT_MAP = {'regulation': 1024, 'formulation': 768, 'ingredient': 512, 'general': 256}

    def __init__(self):
        kv_gb = _config['gpu0']['models']['gen_14b']['kv_cache_budget_gb']
        sf = _config['admission_control']['safety_factor']
        self.kv_budget = int(kv_gb * 1024**3 * sf)
        self.kv_total = int(kv_gb * 1024**3)
        self.active = {}
        self._lock = threading.RLock()  # 可重入锁，admit 内调用 get_pressure 不会死锁

    def estimate_kv(self, inp, out, bt):
        if out == 0:
            out = self.OUTPUT_MAP.get(bt, 512)
        return (inp + out * 1.2) * self.KV_PER_TOKEN

    def _pressure_unlocked(self):
        """无锁版本的 pressure 计算（供锁内调用）"""
        u = sum(self.estimate_kv(v[0], v[1], v[2]) for v in self.active.values())
        return u / self.kv_total if self.kv_total > 0 else 0.0

    def get_pressure(self):
        with self._lock:
            return self._pressure_unlocked()

    def admit(self, rid, inp, out, bt):
        """线程安全的准入检查：整个 check-then-act 过程在锁内执行"""
        est = self.estimate_kv(inp, out, bt)
        admitted = True
        reason = 'admitted'
        p = 0.0
        with self._lock:
            p = self._pressure_unlocked()
            if p > 0.95:
                admitted, reason = False, 'critical'
            elif p > 0.9:
                admitted, reason = False, 'soft_stop'
            else:
                cur = sum(self.estimate_kv(v[0], v[1], v[2]) for v in self.active.values())
                if cur + est > self.kv_budget:
                    admitted, reason = False, 'budget_exceeded'
                else:
                    self.active[rid] = (inp, out, bt)
                    if p > 0.8:
                        reason = 'admitted_with_pressure'
        if not admitted:
            log_audit_event(
                event_type="admission_rejected",
                request_id=rid,
                reject_reason=reason,
                extra={"kv_pressure": round(p, 3)},
            )
        return admitted, reason

    def release(self, rid):
        with self._lock:
            self.active.pop(rid, None)

    def get_status(self):
        with self._lock:
            return {'kv_pressure': round(self._pressure_unlocked(), 3), 'active': len(self.active)}
