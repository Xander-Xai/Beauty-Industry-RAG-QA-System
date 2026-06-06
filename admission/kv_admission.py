"""
KV-aware admission control for vLLM continuous batching.

PRD §5.2: Concurrency is determined by KV budget + sequence length
distribution, NOT a static concurrency number.

PRD §5.2.5 / §9 thresholds:
  > 0.85  → tighten token bucket, queue requests
  > 0.90  → app-layer stream truncation (do NOT modify vLLM max_tokens
             to preserve Prefix Caching)
  > 0.95  → P0 stays 14B with shrunk tokens, P1 downgrades to 4B,
             P2 is dropped
  > 0.97  → extreme overload: P0 downgrades + shrinks, P1 queued,
             P2 returns 503

Prefix Caching protection (PRD §9):
  ``get_effective_max_tokens()`` returns the ORIGINAL max_tokens unchanged.
  Instead it returns an ``action`` string ("truncate", "critical", etc.)
  that the pipeline uses to limit output via application-layer stream
  truncation (early stop / token counting) WITHOUT changing the vLLM
  ``max_tokens`` parameter, which would invalidate the Prefix Cache key
  hash and trigger a Prefill storm.
"""
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
    """
    KV-aware admission control with priority-based degradation (PRD §9).

    Business type → priority mapping (PRD §5.2.5):
      P0: regulation     — highest priority, protected during overload
      P1: development, ingredient — medium priority, downgraded first
      P2: product, general, chat, short — lowest priority, dropped first
    """

    # PRD §5.2.3 双因子 KV 成本模型
    KV_PER_TOKEN = 0.45 * 1024           # 兼容旧代码
    KV_PER_TOKEN_PREFILL = 0.45 * 1024   # 0.45 KB per token (prefill phase)
    KV_PER_TOKEN_DECODE = 0.54 * 1024    # 0.54 KB per token (decode phase = 1.2x prefill)
    OUTPUT_MAP = {
        'regulation': 1024, 'development': 768, 'formulation': 768,
        'ingredient': 512, 'product': 512, 'general': 512, 'short': 256,
    }

    # PRD §5.2.5 / §9 thresholds — unified with PRD spec
    THRESHOLD_TIGHTEN = 0.70     # 70%: token bucket tightened, requests queued
    THRESHOLD_TRUNCATE = 0.80    # 80%: app-layer stream truncation
    THRESHOLD_SOFT_STOP = 0.90   # 90%: P1 downgrade to 4B, P2 dropped
    THRESHOLD_CRITICAL = 0.95    # 95%: extreme overload, P0 downgrade + shrink

    # PRD §5.2.5 — Priority levels
    PRIORITY_MAP = {
        "regulation":  "P0",
        "development": "P1",
        "ingredient":  "P1",
        "product":     "P2",
        "general":     "P2",
        "chat":        "P2",
        "short":       "P2",
    }

    def __init__(self):
        kv_gb = _config['gpu0']['models']['gen_14b']['kv_cache_budget_gb']
        sf = _config['admission_control']['safety_factor']
        self.kv_budget = int(kv_gb * 1024**3 * sf)
        self.kv_total = int(kv_gb * 1024**3)
        # PRD §5.2.5 / §9: four-level thresholds, all configurable
        ac = _config.get('admission_control', {})
        self.threshold_tighten = ac.get('kv_pressure_tighten', self.THRESHOLD_TIGHTEN)
        self.threshold_truncate = ac.get('kv_pressure_truncate', self.THRESHOLD_TRUNCATE)
        self.threshold_soft_stop = ac.get('kv_pressure_soft_stop', self.THRESHOLD_SOFT_STOP)
        self.threshold_critical = ac.get('kv_pressure_critical', self.THRESHOLD_CRITICAL)
        self.active = {}
        self._lock = threading.RLock()

    def estimate_kv(self, inp, out, bt):
        """PRD §5.2.3 双因子 KV 成本估算: input_tokens × prefill_factor + output_tokens × decode_factor"""
        if out == 0:
            out = self.OUTPUT_MAP.get(bt, 512)
        return inp * self.KV_PER_TOKEN_PREFILL + out * self.KV_PER_TOKEN_DECODE

    def _pressure_unlocked(self):
        u = sum(self.estimate_kv(v[0], v[1], v[2]) for v in self.active.values())
        return u / self.kv_budget if self.kv_budget > 0 else 0.0

    def get_pressure(self):
        with self._lock:
            return self._pressure_unlocked()

    def _get_priority(self, business_type: str) -> str:
        """Return priority level for a business type (P0/P1/P2)."""
        return self.PRIORITY_MAP.get(business_type, "P2")

    def admit(self, rid, inp, out, bt):
        """
        Thread-safe admission check: entire check-then-act runs under lock.

        Returns (admitted: bool, reason: str, priority: str).

        Priority-based degradation (PRD §5.2.5 / §9):
          > 0.95 (critical):
            P0 → admitted with forced downgrade (14B→4B) + shrink tokens
            P1 → queued (not admitted)
            P2 → rejected with 503
          > 0.90 (soft_stop):
            P0 → admitted normally
            P1 → admitted with forced downgrade (14B→4B)
            P2 → rejected (not admitted)
          > 0.80 (truncate):
            P0/P1/P2 → admitted with app-layer truncation
          > 0.70 (tighten):
            P0/P1/P2 → admitted with tighten flag
        """
        est = self.estimate_kv(inp, out, bt)
        priority = self._get_priority(bt)
        admitted = True
        reason = 'admitted'
        p = 0.0

        with self._lock:
            p = self._pressure_unlocked()

            if p > self.threshold_critical:
                # ── 97% extreme overload: priority-based handling ──
                if priority == "P0":
                    # P0: retained with forced downgrade (14B→4B) + shrink
                    cur = sum(self.estimate_kv(v[0], v[1], v[2]) for v in self.active.values())
                    if cur + est > self.kv_budget:
                        admitted, reason = False, 'critical_p0_budget'
                    else:
                        self.active[rid] = (inp, out, bt)
                        admitted, reason = True, 'critical'
                elif priority == "P1":
                    # P1: queued (not admitted)
                    admitted, reason = False, 'critical_p1_queued'
                else:
                    # P2: rejected with 503
                    admitted, reason = False, 'critical_p2_rejected'

            elif p > self.threshold_soft_stop:
                # ── 95%: priority-based handling ──
                if priority == "P0":
                    # P0: admitted normally (protected)
                    cur = sum(self.estimate_kv(v[0], v[1], v[2]) for v in self.active.values())
                    if cur + est > self.kv_budget:
                        admitted, reason = False, 'budget_exceeded'
                    else:
                        self.active[rid] = (inp, out, bt)
                        admitted, reason = True, 'admitted'
                elif priority == "P1":
                    # P1: downgraded (14B→4B)
                    cur = sum(self.estimate_kv(v[0], v[1], v[2]) for v in self.active.values())
                    if cur + est > self.kv_budget:
                        admitted, reason = False, 'budget_exceeded'
                    else:
                        self.active[rid] = (inp, out, bt)
                        admitted, reason = True, 'downgrade_to_4b'
                else:
                    # P2: rejected
                    admitted, reason = False, 'soft_stop'

            else:
                cur = sum(self.estimate_kv(v[0], v[1], v[2]) for v in self.active.values())
                if cur + est > self.kv_budget:
                    admitted, reason = False, 'budget_exceeded'
                else:
                    self.active[rid] = (inp, out, bt)
                    if p > self.threshold_truncate:
                        # 80%: app-layer stream truncation
                        reason = 'admitted_with_truncation'
                    elif p > self.threshold_tighten:
                        # 70%: token bucket tightened
                        reason = 'admitted_with_tighten'

        if not admitted:
            log_audit_event(
                event_type="admission_rejected",
                request_id=rid,
                reject_reason=reason,
                extra={"kv_pressure": round(p, 3), "priority": priority, "business_type": bt},
            )
        return admitted, reason, priority

    def get_effective_max_tokens(self, requested_max_tokens: int, reason: str) -> int:
        """
        Return the vLLM max_tokens parameter — UNCHANGED for Prefix Caching.

        PRD §9 Prefix Caching protection:
          During degradation we do NOT modify max_tokens. Instead we return
          the original value and set an action flag so the pipeline can
          implement application-layer stream truncation (e.g., early stop
          tokens, response truncation after streaming). This preserves the
          Prefix Cache key hash and prevents Prefill storms.

        The ``reason`` parameter is used by the pipeline to decide the
        stream truncation strategy, NOT to alter this value.
        """
        # PRD §9: Prefix Caching 保护 — 不修改 max_tokens
        return requested_max_tokens

    def get_truncation_tokens(self, requested_max_tokens: int, reason: str) -> int | None:
        """
        Return the actual token limit for application-layer stream truncation.

        This is the *output* token limit used by the pipeline to truncate
        the streamed response. It does NOT change the vLLM max_tokens.

        Returns None when no truncation is needed.
        """
        if reason == 'admitted_with_truncation':
            return max(requested_max_tokens // 2, 256)
        if reason == 'critical':
            return max(requested_max_tokens // 4, 256)
        return None

    def should_force_downgrade(self, reason: str) -> bool:
        """Whether to force model downgrade (14B→4B)"""
        return reason in ('critical', 'downgrade_to_4b')

    def should_reject_503(self, reason: str) -> bool:
        """Whether to return HTTP 503 (extreme overload for P2)"""
        return reason == 'critical_p2_rejected'

    def release(self, rid):
        with self._lock:
            self.active.pop(rid, None)

    def get_status(self):
        with self._lock:
            return {
                'kv_pressure': round(self._pressure_unlocked(), 3),
                'active': len(self.active),
                'kv_budget_mb': round(self.kv_budget / 1024**2, 1),
            }
