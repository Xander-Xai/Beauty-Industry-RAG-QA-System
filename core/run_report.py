"""Auditable end-to-end run report for a single request.

Why this exists
---------------
`core/pipeline.py` records per-stage latency into ``ctx.stage_timings`` and
prints a one-line summary, but the result of a run is otherwise invisible. Three
things an operator needs cannot be recovered from the answer or from that log
line:

1. **Which stages actually ran.** A stage absent from ``stage_timings`` was
   either skipped by a routing decision or never reached because an earlier gate
   refused. Those are completely different events and a log line cannot tell them
   apart.
2. **Whether the reranker was real.** With the CrossEncoder weights absent, the
   ensemble returns BiEncoder order with ``ce_score_ensemble = 0``. The request
   is then refused at the Evidence Gate — but nothing in the request itself says
   the reranker was dead.
3. **Why it failed.** ``ctx.fallback_reason`` holds a string; whether the system
   degraded, at which gate, and under which gate mode is not recorded together.

This module builds that record from the *observed* context. It never declares a
stage as executed because the code contains it — only because the context shows
evidence that it ran.

Stage status vocabulary:

``executed``
    The stage recorded a latency and left its result on the context.
``skipped``
    A routing decision deliberately bypassed it (e.g. CLIP on a text-only query).
``not_reached``
    An earlier gate refused, so the stage never ran. This is the status that
    distinguishes "the gate worked" from "the stage was omitted".
``degraded``
    The stage ran but through a fallback path rather than the real dependency.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

STATUS_EXECUTED = "executed"
STATUS_SKIPPED = "skipped"
STATUS_NOT_REACHED = "not_reached"
STATUS_DEGRADED = "degraded"

#: Stage order on the canonical request path, with what evidences each one.
#: ``evidence`` names the context attribute that proves the stage ran; the report
#: derives status from it rather than from the stage list alone.
STAGE_SPEC: tuple[tuple[str, str | None], ...] = (
    ("cache_lookup", "cache_hit_level"),
    ("rewrite", "rewrite_result"),
    ("complexity_eval", None),
    ("admission_check", None),
    ("embedding_route", None),
    ("parallel_recall", "recall_results"),
    ("bi_encoder", "rerank_results"),
    ("cross_encoder_ensemble", "rerank_results"),
    ("evidence_gate", "evidence_result"),
    ("generation", "generation_result"),
    ("answer_gate", "answer_gate_result"),
)

#: Stages that only run for a visual query, so their absence is a routing
#: decision rather than a failure.
CONDITIONAL_STAGES = {"blip_inference"}


@dataclass
class StageReport:
    stage: str
    status: str
    latency_ms: float | None = None
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunReport:
    """One request, fully accounted for."""

    run_id: str
    timestamp_utc: str
    outcome: str  # "answered" | "rejected_by_evidence_gate" | "rejected_by_answer_gate" | "error"
    stages: list[StageReport] = field(default_factory=list)
    degraded: bool = False
    fallback_reason: str | None = None
    gate_mode: str | None = None
    evidence_score: float | None = None
    evidence_decision: str | None = None
    answer_gate_passed: bool | None = None
    rerank: dict[str, Any] = field(default_factory=dict)
    routing: dict[str, Any] = field(default_factory=dict)
    hardware: dict[str, Any] = field(default_factory=dict)
    git_sha: str | None = None
    git_dirty: bool | None = None
    total_latency_ms: float | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["answered_with_real_rerank"] = bool(
            self.outcome == "answered" and self.rerank.get("provenance") == "cross_encoder"
        )
        return payload

    def executed_stages(self) -> list[str]:
        return [stage.stage for stage in self.stages if stage.status == STATUS_EXECUTED]

    def not_reached_stages(self) -> list[str]:
        return [stage.stage for stage in self.stages if stage.status == STATUS_NOT_REACHED]


# ── environment ─────────────────────────────────────────────────────────────


def git_provenance(repo_root: Path = PROJECT_ROOT) -> tuple[str | None, bool | None]:
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_root, capture_output=True, text=True, timeout=10
        )
        return (
            sha.stdout.strip() or None,
            bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
        )
    except Exception:  # noqa: BLE001
        return None, None


def hardware_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "cuda_available": False,
        "gpus": [],
    }
    try:
        import torch

        info["torch"] = torch.__version__
        info["cuda_available"] = bool(torch.cuda.is_available())
        if info["cuda_available"]:
            info["gpus"] = [
                {"index": index, "name": torch.cuda.get_device_name(index)}
                for index in range(torch.cuda.device_count())
            ]
    except Exception as exc:  # noqa: BLE001
        info["torch_error"] = f"{type(exc).__name__}: {exc}"
    return info


def rerank_evidence() -> dict[str, Any]:
    """What the reranker actually did, derived from the real weights state."""
    from retrieval.rerank_status import rerank_weights_status

    status = rerank_weights_status()
    available = bool(status["available"])
    return {
        "weights_status": status["status"],
        "provenance": "cross_encoder" if available else "deterministic_fallback",
        "models": status["models"],
        "evidence_gate_mode": status["evidence_gate"]["mode"],
        "no_ce_ceiling": status["evidence_gate"]["no_ce_ceiling"],
        "reason": status["reason"],
    }


# ── report construction ─────────────────────────────────────────────────────


def _timing(ctx: Any, stage: str) -> float | None:
    value = getattr(ctx, "stage_timings", None) or {}
    return value.get(stage)


def _classify(ctx: Any, stage: str, evidence_attr: str | None, rerank_provenance: str | None) -> tuple[str, str | None]:
    """Derive a stage's status from what the context actually shows.

    A recorded latency means the stage ran. ``degraded`` is reserved for a stage
    that ran but through a fallback — which is only known for the reranker, so
    it is passed in rather than guessed from an empty result list.
    """
    latency = _timing(ctx, stage)

    if stage == "cache_lookup":
        level = getattr(ctx, "cache_hit_level", None)
        if level in ("L1", "L2"):
            return STATUS_EXECUTED, f"cache_hit={level}"
        return STATUS_EXECUTED, "cache_miss"

    if stage == "cross_encoder_ensemble" and latency is not None and rerank_provenance != "cross_encoder":
        return STATUS_DEGRADED, "ran on the deterministic fallback (no CrossEncoder weights)"

    if stage == "complexity_eval":
        # Folded into the rewrite timing by the pipeline (pipeline.py:314), so it
        # shares the rewrite's fate rather than having a latency of its own.
        if getattr(ctx, "rewrite_result", None) is None:
            return STATUS_NOT_REACHED, "rewrite did not run"
        return STATUS_EXECUTED, "folded into the rewrite stage timing"

    if latency is not None:
        if evidence_attr:
            value = getattr(ctx, evidence_attr, None)
            empty = value is None or (isinstance(value, (list, tuple)) and not value)
            if empty:
                # Ran and returned nothing. That is an observation, not a fault.
                return STATUS_EXECUTED, "ran and returned no results"
        return STATUS_EXECUTED, None

    if evidence_attr:
        value = getattr(ctx, evidence_attr, None)
        if value is not None and not (isinstance(value, (list, tuple)) and not value):
            return STATUS_DEGRADED, "result present without a recorded latency"
        return STATUS_NOT_REACHED, "an earlier gate returned before this stage"

    if stage in CONDITIONAL_STAGES:
        return STATUS_SKIPPED, "conditional stage not selected by routing"
    return STATUS_NOT_REACHED, "no recorded timing and no result"


def build_run_report(ctx: Any, run_id: str | None = None) -> RunReport:
    """Build the report from an observed :class:`RequestContext`."""
    git_sha, git_dirty = git_provenance()
    rerank = rerank_evidence()
    provenance = rerank.get("provenance")

    stages: list[StageReport] = []
    for stage, evidence_attr in STAGE_SPEC:
        status, detail = _classify(ctx, stage, evidence_attr, provenance)
        stages.append(StageReport(stage=stage, status=status, latency_ms=_timing(ctx, stage), detail=detail))
    for stage in sorted(CONDITIONAL_STAGES):
        if stage not in {item.stage for item in stages}:
            status, detail = _classify(ctx, stage, None, provenance)
            stages.append(StageReport(stage=stage, status=status, latency_ms=_timing(ctx, stage), detail=detail))

    evidence = getattr(ctx, "evidence_result", None)
    answer_gate = getattr(ctx, "answer_gate_result", None)
    answer_gate_result = getattr(ctx, "generation_result", None)

    if answer_gate_result is None and getattr(ctx, "evidence_result", None) is not None:
        outcome = "rejected_by_evidence_gate"
    elif answer_gate is not None and not getattr(answer_gate, "passed", True):
        outcome = "rejected_by_answer_gate"
    elif getattr(ctx, "degraded", False):
        outcome = "error"
    elif getattr(ctx, "generation_result", None) is not None:
        outcome = "answered"
    else:
        outcome = "rejected_by_evidence_gate"

    rewrite = getattr(ctx, "rewrite_result", None)
    routing = {
        "business_type": getattr(rewrite, "business_type", None),
        "intent": getattr(rewrite, "intent", None),
        "is_fallback": getattr(rewrite, "fallback", None),
        "clip_use": getattr(ctx, "clip_use", None),
        "clip_top_k": getattr(ctx, "clip_top_k", None),
        "max_output_tokens": getattr(ctx, "max_output_tokens", None),
    }

    return RunReport(
        run_id=run_id or getattr(ctx, "request_id", "unknown"),
        timestamp_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        outcome=outcome,
        stages=stages,
        degraded=bool(getattr(ctx, "degraded", False)),
        fallback_reason=getattr(ctx, "fallback_reason", None),
        gate_mode=getattr(evidence, "gate_mode", None),
        evidence_score=getattr(evidence, "evidence_score", None),
        evidence_decision=getattr(evidence, "decision", None),
        answer_gate_passed=getattr(answer_gate, "passed", None),
        rerank=rerank,
        routing=routing,
        hardware=hardware_info(),
        git_sha=git_sha,
        git_dirty=git_dirty,
        total_latency_ms=(getattr(ctx, "stage_timings", None) or {}).get("total"),
    )


def report_to_json(report: RunReport) -> str:
    return json.dumps(report.as_dict(), ensure_ascii=False, indent=2, default=str)
