"""The run report must describe what happened, not what the code contains.

`core/run_report.py` exists to answer three questions a log line cannot: which
stages ran, whether the reranker was real, and why the run ended the way it did.

These tests build `RequestContext` objects directly — no network, no models —
and assert the report derives each stage status from the context rather than
from the pipeline's control flow. The positive and negative controls matter
together: without the positive one, "everything is not_reached" would pass every
safety assertion.
"""

from __future__ import annotations

import pytest

from common.models import RerankResult
from core.pipeline_context import EvidenceGateResult, RecallResult, RequestContext
from core.run_report import (
    STATUS_DEGRADED,
    STATUS_EXECUTED,
    STATUS_NOT_REACHED,
    STATUS_SKIPPED,
    RunReport,
    build_run_report,
    report_to_json,
)


class _Rewrite:
    business_type = "regulation"
    intent = "fact"
    fallback = False


def _ctx() -> RequestContext:
    return RequestContext(user_input="烟酰胺限量?", user_id="u1", user_role_mask=1, user_dept_mask=1)


def _refused_run() -> RequestContext:
    """The state this repository is actually in: evidence gate refuses."""
    ctx = _ctx()
    ctx.rewrite_result = _Rewrite()
    ctx.stage_timings = {
        "rewrite": 8.0,
        "complexity_eval": 0.0,
        "admission_check": 0.2,
        "embedding_route": 2.0,
        "parallel_recall": 12.0,
        "bi_encoder": 3.0,
        "cross_encoder_ensemble": 0.4,
        "evidence_gate": 1.2,
        "total": 26.8,
    }
    ctx.recall_results = [RecallResult(doc_id="d0", content="烟酰胺限量2.0%", score=1.0, source="bm25_es")]
    ctx.rerank_results = [RerankResult(doc_id="d0", content="烟酰胺限量2.0%", source="bm25_es")]
    ctx.evidence_result = EvidenceGateResult(
        evidence_score=0.38,
        ce_top1_score=0.0,
        ce_top3_mean_score=0.0,
        retrieval_agreement_score=0.49,
        doc_consistency_score=0.8,
        decision="reject",
        gate_mode="fail_closed_no_rerank_weights",
        top_docs=ctx.rerank_results,
    )
    ctx.final_response = "无法确认相关信息，请补充更多细节或换个方式提问。"
    return ctx


def _answered_run() -> RequestContext:
    """Positive control: a run that reaches generation."""
    ctx = _refused_run()
    ctx.evidence_result = EvidenceGateResult(
        evidence_score=0.84,
        ce_top1_score=0.95,
        ce_top3_mean_score=0.93,
        retrieval_agreement_score=0.95,
        doc_consistency_score=0.9,
        decision="pass",
        gate_mode="fail_closed_no_rerank_weights",
        top_docs=ctx.rerank_results,
    )
    ctx.stage_timings.update({"generation": 420.0, "answer_gate": 2.1, "total": 449.0})

    class _Gen:
        answer = "烟酰胺在驻留类产品中的最大允许浓度为 2.0%。"

    class _AG:
        passed = True

    ctx.generation_result = _Gen()
    ctx.answer_gate_result = _AG()
    ctx.final_response = ctx.generation_result.answer
    return ctx


def _stage(report: RunReport, name: str):
    return next(stage for stage in report.stages if stage.stage == name)


# ── stage accounting ────────────────────────────────────────────────────────


def test_executed_stages_are_reported_from_recorded_latency():
    report = build_run_report(_refused_run())
    for stage in ("rewrite", "admission_check", "embedding_route", "parallel_recall", "bi_encoder"):
        assert _stage(report, stage).status == STATUS_EXECUTED, stage
        assert _stage(report, stage).latency_ms is not None


def test_generation_is_not_reached_when_the_evidence_gate_refused():
    """The distinction that matters: refused, not slow, not skipped."""
    report = build_run_report(_refused_run())
    assert _stage(report, "generation").status == STATUS_NOT_REACHED
    assert _stage(report, "answer_gate").status == STATUS_NOT_REACHED
    assert "generation" in report.not_reached_stages()


def test_generation_is_reported_executed_when_the_run_reached_it():
    """Positive control: the report is not uniformly `not_reached`."""
    report = build_run_report(_answered_run())
    assert _stage(report, "generation").status == STATUS_EXECUTED
    assert _stage(report, "answer_gate").status == STATUS_EXECUTED
    assert report.not_reached_stages() == []


def test_recall_returning_nothing_is_observed_not_called_degraded():
    """Running and finding nothing is an observation, not a degradation."""
    ctx = _refused_run()
    ctx.recall_results = []
    report = build_run_report(ctx)
    stage = _stage(report, "parallel_recall")
    assert stage.status == STATUS_EXECUTED
    assert "no results" in stage.detail


def test_conditional_stage_is_skipped_not_failed():
    report = build_run_report(_refused_run())
    assert _stage(report, "blip_inference").status == STATUS_SKIPPED


def test_complexity_eval_inherits_the_rewrite_fate():
    """It shares the rewrite timing, so it must not claim to have run alone."""
    ctx = _refused_run()
    report = build_run_report(ctx)
    assert _stage(report, "complexity_eval").status == STATUS_EXECUTED

    # No rewrite result means the rewrite/complexity block never ran.
    ctx.rewrite_result = None
    ctx.stage_timings.pop("rewrite")
    ctx.stage_timings.pop("complexity_eval")
    report = build_run_report(ctx)
    assert _stage(report, "complexity_eval").status == STATUS_NOT_REACHED
    assert _stage(report, "rewrite").status == STATUS_NOT_REACHED


# ── reranker provenance ─────────────────────────────────────────────────────


def test_fallback_rerank_is_reported_as_degraded():
    """With the weights absent the CE stage ran, but on the fallback."""
    report = build_run_report(_refused_run())
    stage = _stage(report, "cross_encoder_ensemble")
    assert stage.status == STATUS_DEGRADED
    assert "deterministic fallback" in stage.detail


def test_report_states_the_rerank_provenance_explicitly():
    from retrieval.rerank_status import rerank_weights_status

    report = build_run_report(_refused_run())
    assert report.rerank["provenance"] in ("cross_encoder", "deterministic_fallback")
    if rerank_weights_status()["status"] == "BLOCKED":
        assert report.rerank["provenance"] == "deterministic_fallback"
        assert report.rerank["reason"]


def test_a_refusal_with_a_fallback_rerank_is_never_called_real():
    report = build_run_report(_refused_run())
    assert report.as_dict()["answered_with_real_rerank"] is False


# ── outcome ─────────────────────────────────────────────────────────────────


def test_outcome_names_the_gate_that_refused():
    assert build_run_report(_refused_run()).outcome == "rejected_by_evidence_gate"
    assert build_run_report(_answered_run()).outcome == "answered"


def test_answer_gate_rejection_is_named_distinctly():
    ctx = _answered_run()

    class _AG:
        passed = False

    ctx.answer_gate_result = _AG()
    report = build_run_report(ctx)
    assert report.outcome == "rejected_by_answer_gate"
    assert report.answer_gate_passed is False


def test_degraded_run_is_reported_as_an_error_not_an_answer():
    ctx = _answered_run()
    ctx.degraded = True
    ctx.fallback_reason = "vLLM connection refused"
    report = build_run_report(ctx)
    assert report.outcome == "error"
    assert report.fallback_reason == "vLLM connection refused"
    assert report.degraded is True


# ── provenance and serialization ────────────────────────────────────────────


def test_report_carries_git_and_hardware():
    report = build_run_report(_refused_run())
    assert report.git_sha is not None
    assert isinstance(report.git_dirty, bool)
    assert "cuda_available" in report.hardware
    assert report.total_latency_ms == 26.8


def test_report_is_json_serialisable():
    payload = report_to_json(build_run_report(_refused_run()))
    assert '"outcome"' in payload
    assert '"answered_with_real_rerank"' in payload


def test_report_is_deterministic_for_the_same_context():
    ctx = _refused_run()
    first = build_run_report(ctx, run_id="x")
    second = build_run_report(ctx, run_id="x")
    assert [s.as_dict() for s in first.stages] == [s.as_dict() for s in second.stages]
    assert first.outcome == second.outcome


@pytest.mark.parametrize("stage_count", [1])
def test_every_spec_stage_appears_exactly_once(stage_count):
    report = build_run_report(_refused_run())
    names = [stage.stage for stage in report.stages]
    assert len(names) == len(set(names))
    assert "evidence_gate" in names
