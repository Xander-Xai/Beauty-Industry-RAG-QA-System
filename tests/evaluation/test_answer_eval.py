"""Answer-level evaluation scorer tests.

These exercise the scorer's arithmetic with hand-built observations. They are
unit fixtures for the metric definitions, not measured pipeline results — no
answer here comes from a model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.evaluation.answer_eval import (
    CATEGORIES,
    AnswerEvalError,
    AnswerEvalSample,
    ObservedAnswer,
    load_samples,
    score,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ANSWER_SET = REPO_ROOT / "tests" / "evaluation" / "answer_eval_set.jsonl"


def _sample(sample_id="s1", category="sufficient_evidence", must_refuse=False, high_risk=False):
    return AnswerEvalSample(
        sample_id=sample_id,
        category=category,
        question="q",
        business_type="regulation",
        must_refuse=must_refuse,
        high_risk=high_risk,
        reviewer="r",
        reviewed_at="2026-10-09",
    )


def _obs(sample_id="s1", gate="pass", support="supported", rbac_leak=False, answer="a", citations=("d1",)):
    return ObservedAnswer(
        sample_id=sample_id,
        answer_text=answer,
        citations=tuple(citations),
        gate_decision=gate,
        answer_gate_passed=True,
        evidence_support=support,
        rbac_leak=rbac_leak,
        reviewer="r",
        reviewed_at="2026-10-09",
    )


def test_committed_answer_set_loads_and_covers_every_category():
    samples = load_samples(ANSWER_SET)
    assert len(samples) == 12
    assert {s.category for s in samples} == set(CATEGORIES)
    assert any(s.must_refuse for s in samples) and any(not s.must_refuse for s in samples)


def test_support_and_unsupported_rates():
    samples = [_sample("a"), _sample("b"), _sample("c")]
    observations = [
        _obs("a", support="supported"),
        _obs("b", support="partially_supported"),
        _obs("c", support="unsupported"),
    ]
    report = score(samples, observations)
    assert report.metrics["answered_count"] == 3
    assert report.metrics["answer_evidence_support_rate"] == pytest.approx(1 / 3)
    assert report.metrics["answer_evidence_support_rate_inclusive"] == pytest.approx(2 / 3)
    assert report.metrics["unsupported_answer_rate"] == pytest.approx(1 / 3)


def test_refusal_recall_and_false_refusal():
    samples = [_sample("must", must_refuse=True), _sample("should", must_refuse=False)]
    observations = [
        _obs("must", gate="reject", support="not_applicable", answer=""),
        _obs("should", gate="reject", support="not_applicable", answer=""),
    ]
    report = score(samples, observations)
    assert report.metrics["refusal_recall"] == pytest.approx(1.0)
    assert report.metrics["false_refusal_rate"] == pytest.approx(1.0)


def test_missing_a_required_refusal_lowers_recall():
    samples = [_sample("must", must_refuse=True)]
    report = score(samples, [_obs("must", gate="pass", support="unsupported")])
    assert report.metrics["refusal_recall"] == pytest.approx(0.0)
    assert report.metrics["unsupported_answer_rate"] == pytest.approx(1.0)


def test_rbac_leak_is_counted_independently_of_support():
    samples = [_sample("a")]
    report = score(samples, [_obs("a", support="supported", rbac_leak=True)])
    assert report.metrics["rbac_leak_count"] == 1
    assert report.metrics["answer_evidence_support_rate"] == pytest.approx(1.0)


def test_high_risk_unsupported_rate():
    samples = [_sample("hr", high_risk=True), _sample("lr", high_risk=False)]
    observations = [
        _obs("hr", support="unsupported"),
        _obs("lr", support="supported"),
    ]
    report = score(samples, observations)
    assert report.metrics["high_risk_unsupported_rate"] == pytest.approx(1.0)


def test_by_category_breakdown():
    samples = [_sample("a", category="fabricated_source_trap"), _sample("b", category="sufficient_evidence")]
    observations = [
        _obs("a", gate="reject", support="not_applicable", answer=""),
        _obs("b", support="supported"),
    ]
    report = score(samples, observations)
    assert report.by_category["fabricated_source_trap"]["refused_count"] == 1
    assert report.by_category["sufficient_evidence"]["answer_evidence_support_rate"] == pytest.approx(1.0)


def test_missing_observations_are_reported_not_assumed():
    samples = [_sample("a"), _sample("b")]
    report = score(samples, [_obs("a")])
    assert report.missing_observations == ("b",)
    # Unscored samples never become a silent success.
    assert report.metrics["sample_count"] == 1


def test_unknown_sample_and_duplicate_observation_are_rejected():
    samples = [_sample("a")]
    with pytest.raises(AnswerEvalError, match="unknown sample"):
        score(samples, [_obs("zzz")])
    with pytest.raises(AnswerEvalError, match="duplicate observation"):
        score(samples, [_obs("a"), _obs("a")])


def test_invalid_gate_decision_and_support_are_rejected():
    with pytest.raises(AnswerEvalError, match="gate_decision"):
        ObservedAnswer.from_dict(
            {
                "sample_id": "a",
                "gate_decision": "maybe",
                "evidence_support": "supported",
                "reviewer": "r",
                "reviewed_at": "d",
            }
        )
    with pytest.raises(AnswerEvalError, match="evidence_support"):
        ObservedAnswer.from_dict(
            {
                "sample_id": "a",
                "gate_decision": "pass",
                "evidence_support": "looks_fine",
                "reviewer": "r",
                "reviewed_at": "d",
            }
        )


def test_llm_self_score_shape_is_not_a_human_label():
    """A pipeline record without a human reviewer cannot be scored."""
    with pytest.raises(AnswerEvalError, match="reviewer"):
        ObservedAnswer.from_dict(
            {
                "sample_id": "a",
                "gate_decision": "pass",
                "evidence_support": "supported",
                "reviewer": "",
                "reviewed_at": "2026-10-09",
            }
        )
