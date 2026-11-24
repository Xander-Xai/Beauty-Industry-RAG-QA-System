"""Answer-level evaluation scorer tests.

These exercise the scorer's arithmetic with hand-built observations. They are
unit fixtures for the metric definitions, not measured pipeline results — no
answer here comes from a model.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from tests.evaluation.answer_eval import (
    CATEGORIES,
    AnswerEvalError,
    AnswerEvalSample,
    ObservedAnswer,
    load_samples,
    main,
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


def _reviewed_row(sample_id="s1", **changes):
    return {
        "sample_id": sample_id,
        "gate_decision": "pass",
        "answer_gate_passed": True,
        "final_disposition": "answered",
        "answer_text": "observed fixture response",
        "citations": ["d1"],
        "evidence_support": "supported",
        "rbac_leak": False,
        "reviewer": "fixture-reviewer",
        "reviewed_at": "2026-11-24",
        "review_status": "HUMAN_REVIEWED",
        **changes,
    }


def _cli_inputs(tmp_path, samples, observations):
    dataset = tmp_path / "samples.jsonl"
    observed = tmp_path / "observations.jsonl"
    out = tmp_path / "result.json"
    dataset.write_text("\n".join(json.dumps(asdict(s)) for s in samples), encoding="utf-8")
    observed.write_text("\n".join(json.dumps(o) for o in observations), encoding="utf-8")
    return ["--dataset", str(dataset), "--observations", str(observed), "--out", str(out)], out


def test_cli_scores_actual_final_refusal_after_evidence_gate_pass(tmp_path):
    row = _reviewed_row(
        final_disposition="refused", evidence_support="not_applicable", answer_gate_passed=False, citations=[]
    )
    args, out = _cli_inputs(tmp_path, [_sample(must_refuse=True)], [row])
    assert main(args) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["metrics"]["refusal_recall"] == 1
    assert report["coverage"] == 1
    assert report["publication_eligibility"] == "NOT_ASSESSED"
    assert report["answered_count"] == 0


def test_cli_incomplete_coverage_returns_nonzero_and_records_missing_ids(tmp_path):
    args, out = _cli_inputs(tmp_path, [_sample("s1"), _sample("s2")], [_reviewed_row()])
    assert main(args) == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["missing_observations"] == ["s2"]
    assert report["coverage"] == 0.5
    assert report["status"] == "INCOMPLETE_DIAGNOSTIC"


@pytest.mark.parametrize(
    "changes",
    [
        {"rbac_leak": "false"},
        {"final_disposition": None},
        {"review_status": "MODEL_SCORED"},
        {"final_disposition": "answered", "evidence_support": "not_applicable"},
        {"citations": []},
        {"answer_text": ""},
    ],
)
def test_cli_rejects_unreviewed_or_malformed_observations(tmp_path, changes):
    args, out = _cli_inputs(tmp_path, [_sample()], [_reviewed_row(**changes)])
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    assert not out.exists()


def test_cli_rejects_string_boolean_in_gold(tmp_path):
    args, out = _cli_inputs(tmp_path, [_sample(must_refuse="false")], [_reviewed_row()])
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    assert not out.exists()
