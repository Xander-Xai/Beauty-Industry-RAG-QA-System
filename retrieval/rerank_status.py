"""Rerank / Evidence-Gate configuration precheck.

Why this module exists
----------------------
The Evidence Gate is fail-closed: without CrossEncoder weights the ensemble
falls back deterministically, ``ce_top1_score`` and ``ce_top3_mean_score`` stay
``0``, and the maximum attainable Evidence Score collapses to
``w3·agreement + w4·doc_consistency``.  With the shipped ``config.json`` that
ceiling is ``0.2 + 0.2 = 0.40``, below ``low_confidence = 0.55``, so **every
query is refused**.  That is the correct failure direction, but it is invisible
until an operator reads the code: the system looks like it is running while it
answers nothing.

This module turns that consequence into an explicit, runnable precheck.  It is
pure and deterministic: it stats the configured model paths and reads the
configured thresholds, and it never imports ``sentence_transformers`` or loads
a model.  It deliberately does **not** change the gate arithmetic or the
thresholds — the degradation is reported, not "fixed" by lowering the bar.

Usage::

    python3 -m retrieval.rerank_status

or programmatically::

    from retrieval.rerank_status import rerank_weights_status
    status = rerank_weights_status()
    if status["status"] == "BLOCKED":
        ...
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from common.config import get_config_dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Files that a sentence-transformers CrossEncoder directory must contain before
#: it can be loaded.  At least one must be present.
_MODEL_MARKER_FILES = (
    "config.json",
    "modules.json",
    "sentence_bert_config.json",
    "pytorch_model.bin",
    "model.safetensors",
)


def _resolve_model_path(path_str: str) -> Path:
    path = Path(path_str)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _model_available(path_str: str) -> tuple[bool, str]:
    """Return ``(available, reason)`` for one configured model path."""
    if not path_str:
        return False, "model_path is empty"
    path = _resolve_model_path(path_str)
    if not path.exists():
        return False, f"path does not exist: {path_str}"
    if path.is_file():
        return True, ""
    present = [name for name in _MODEL_MARKER_FILES if (path / name).exists()]
    if present:
        return True, ""
    return False, f"directory has no model config/weight file: {path_str}"


def rerank_weights_status(config: dict | None = None) -> dict:
    """Precheck the CrossEncoder weights and the Evidence-Gate consequence.

    Returns a JSON-serialisable dict.  ``status`` is ``"AVAILABLE"`` only when
    both configured CrossEncoder paths resolve to a loadable asset **and**
    ``sentence_transformers`` is importable; otherwise it is ``"BLOCKED"``.
    ``failure_mode`` is always ``"fail_closed"``: this function reports the
    degradation, it never authorises bypassing it.
    """
    cfg = config if config is not None else get_config_dict()
    models = cfg.get("gpu1", {}).get("models", {})

    checks: dict[str, dict] = {}
    reasons: list[str] = []
    for key, label in (("cross_encoder_a", "CE-A"), ("cross_encoder_b", "CE-B")):
        path_str = models.get(key, {}).get("model_path", "")
        available, reason = _model_available(path_str)
        checks[key] = {"model_path": path_str, "available": available, "reason": reason}
        if not available:
            reasons.append(f"{label}: {reason}")

    st_available = importlib.util.find_spec("sentence_transformers") is not None
    if not st_available:
        reasons.append("sentence_transformers is not installed")

    available = st_available and all(c["available"] for c in checks.values())

    gate_cfg = cfg.get("retrieval", {}).get("evidence_gate", {})
    weights = gate_cfg.get("weights", {})
    thresholds = gate_cfg.get("thresholds", {})
    low_confidence = float(thresholds.get("low_confidence", 0.55))
    high_confidence = float(thresholds.get("high_confidence", 0.75))
    # With no CrossEncoder signal the two CE terms are 0; agreement and
    # doc-consistency can each reach at most 1.0.
    no_ce_ceiling = float(weights.get("w3", 0.0)) + float(weights.get("w4", 0.0))
    fail_closed = (not available) and no_ce_ceiling < low_confidence

    if available:
        mode = "normal"
        effect = "CrossEncoder weights available; Evidence Gate operates normally."
    else:
        mode = "fail_closed_no_rerank_weights"
        effect = (
            f"CrossEncoder weights unavailable, so the Evidence Gate ceiling is "
            f"{no_ce_ceiling:.2f} < low_confidence {low_confidence:.2f}: every query is refused. "
            f"Provide the configured weights to restore generation."
        )

    return {
        "status": "AVAILABLE" if available else "BLOCKED",
        "available": available,
        "failure_mode": "fail_closed",
        "reason": "; ".join(reasons) if reasons else "",
        "sentence_transformers_available": st_available,
        "models": checks,
        "evidence_gate": {
            "mode": mode,
            "no_ce_ceiling": no_ce_ceiling,
            "low_confidence": low_confidence,
            "high_confidence": high_confidence,
            "fail_closed": fail_closed,
            "effect": effect,
        },
    }


def describe(config: dict | None = None) -> str:
    """Human-readable one-block summary for startup logs and operators."""
    status = rerank_weights_status(config)
    lines = [
        f"Rerank weights: {status['status']} (failure_mode={status['failure_mode']})",
    ]
    if status["reason"]:
        lines.append(f"  reason: {status['reason']}")
    gate = status["evidence_gate"]
    lines.append(
        f"  Evidence Gate mode={gate['mode']} ceiling_without_ce="
        f"{gate['no_ce_ceiling']:.2f} low_confidence={gate['low_confidence']:.2f}"
    )
    lines.append(f"  effect: {gate['effect']}")
    return "\n".join(lines)


def main() -> int:
    """Entry point for ``python3 -m retrieval.rerank_status``."""
    print(json.dumps(rerank_weights_status(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
