"""Fixture-only tests for paired human-review evidence, not model results."""

import hashlib
import json

import pytest

from tests.evaluation.answer_eval import AnswerEvalError
from tests.evaluation.paired_answer_eval import compare


def _raw(rows):
    return "\n".join(json.dumps(row) for row in rows).encode()


def _dataset():
    return _raw(
        [
            {
                "sample_id": name,
                "category": "sufficient_evidence",
                "question": "fixture",
                "business_type": "regulation",
                "must_refuse": False,
                "high_risk": name == "b",
                "reviewer": "fixture",
                "reviewed_at": "2026-11-25",
            }
            for name in ("a", "b")
        ]
    )


def _row(name, **changes):
    return {
        "sample_id": name,
        "gate_decision": "pass",
        "answer_gate_passed": True,
        "final_disposition": "answered",
        "answer_text": "fixture",
        "citations": ["fixture-source"],
        "evidence_support": "supported",
        "rbac_leak": False,
        "reviewer": "fixture",
        "reviewed_at": "2026-11-25",
        "review_status": "HUMAN_REVIEWED",
        "end_to_end_ms": 10,
        **changes,
    }


def _manifest(arm, dataset, raw):
    return {
        "arm": arm,
        "run_id": arm + "-fixture",
        "environment": "ISOLATED_EVALUATION",
        "experiment_variable": "evidence_gate_policy",
        "answer_gate_enabled": True,
        "rbac_enabled": True,
        "dataset_sha256": hashlib.sha256(dataset).hexdigest(),
        "observations_sha256": hashlib.sha256(raw).hexdigest(),
        "git_sha": "a" * 40,
        "knowledge_epoch": "20261125_01",
        **{
            key: "a" * 64
            for key in (
                "corpus_sha256",
                "model_assets_sha256",
                "retrieval_contract_sha256",
                "permissions_sha256",
                "workload_sha256",
                "runtime_config_sha256",
                "review_protocol_sha256",
            )
        },
        "evidence_gate_policy_sha256": ("b" if arm == "baseline" else "c") * 64,
    }


def _compare(left, right, changes=None):
    data = _dataset()
    a, b = _raw(left), _raw(right)
    ma, mb = _manifest("baseline", data, a), _manifest("candidate", data, b)
    mb.update(changes or {})
    return compare(data, a, b, ma, mb)


def test_paired_review_reports_regressions_even_when_aggregate_is_unchanged():
    result = _compare(
        [_row("a", evidence_support="unsupported"), _row("b")],
        [_row("a"), _row("b", evidence_support="unsupported")],
    )
    assert result["complete"]
    assert result["strict_success_delta"] == 0
    assert result["improved_ids"] == ["a"]
    assert result["high_risk_regressed_ids"] == ["b"]
    assert result["candidate_has_no_observed_strict_regression"] is False
    assert result["production_release_authorized"] is False


def test_missing_samples_do_not_produce_a_quality_or_p95_claim():
    result = _compare([_row("a"), _row("b")], [_row("a")])
    assert result["strict_success_delta"] is None
    assert result["candidate_has_no_observed_rbac_leak"] is None
    assert result["arms"]["candidate"]["strict_success_lower_bound"] == 0.5
    assert result["arms"]["candidate"]["declared_end_to_end_p95_ms"] is None
    assert result["arms"]["candidate"]["missing_ids"] == ["b"]


@pytest.mark.parametrize(
    "changes",
    [
        {"permissions_sha256": "d" * 64},
        {"knowledge_epoch": "20261125_02"},
        {"knowledge_epoch": 1},
        {"knowledge_epoch": "default"},
        {"evidence_gate_policy_sha256": "b" * 64},
        {"observations_sha256": "0" * 64},
        {"answer_gate_enabled": False},
        {"rbac_enabled": False},
        {"run_id": "baseline-fixture"},
    ],
)
def test_unpaired_or_unsafe_manifest_is_rejected(changes):
    with pytest.raises(AnswerEvalError):
        _compare([_row("a"), _row("b")], [_row("a"), _row("b")], changes)
