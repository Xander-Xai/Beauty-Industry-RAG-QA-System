"""The paired answer arm manifest must be derived from the artifacts it declares.

These tests build a manifest from real files and feed it to the paired scorer,
so a digest that does not match its artifact cannot be produced silently.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from tests.evaluation.answer_eval import AnswerEvalError
from tests.evaluation.paired_answer_eval import compare
from tests.evaluation.paired_answer_manifest import _DECLARED_DIGESTS, build_manifest, main

_DECLARED = {key: "a" * 64 for key in _DECLARED_DIGESTS}
_DECLARED["evidence_gate_policy_sha256"] = "b" * 64


def _raw(rows):
    return "\n".join(json.dumps(row) for row in rows).encode()


def _dataset_rows():
    return [
        {
            "sample_id": name,
            "category": "sufficient_evidence",
            "question": "fixture",
            "business_type": "regulation",
            "must_refuse": False,
            "high_risk": False,
            "reviewer": "fixture",
            "reviewed_at": "2026-11-25",
        }
        for name in ("a", "b")
    ]


def _observation_rows():
    return [
        {
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
        }
        for name in ("a", "b")
    ]


def _write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def _build(tmp_path, arm="baseline", declared=None, **overrides):
    dataset = _write(tmp_path, "dataset.jsonl", _raw(_dataset_rows()))
    observations = _write(tmp_path, "observations.jsonl", _raw(_observation_rows()))
    kwargs = {
        "arm": arm,
        "run_id": f"{arm}-run",
        "dataset_path": dataset,
        "observations_path": observations,
        "declared": dict(declared if declared is not None else _DECLARED),
        "git_sha": "c" * 40,
        "knowledge_epoch": "20261125_01",
    }
    kwargs.update(overrides)
    return build_manifest(**kwargs)


def test_manifest_digests_are_derived_from_the_artifact_bytes(tmp_path):
    manifest = _build(tmp_path)

    dataset = (tmp_path / "dataset.jsonl").read_bytes()
    observations = (tmp_path / "observations.jsonl").read_bytes()

    assert manifest["dataset_sha256"] == hashlib.sha256(dataset).hexdigest()
    assert manifest["observations_sha256"] == hashlib.sha256(observations).hexdigest()


def test_built_manifest_is_accepted_by_the_paired_scorer(tmp_path):
    """A manifest built from real artifacts must satisfy the scorer's contract."""
    _build(tmp_path)
    dataset = (tmp_path / "dataset.jsonl").read_bytes()
    observations = (tmp_path / "observations.jsonl").read_bytes()

    baseline = build_manifest(
        arm="baseline",
        run_id="baseline-run",
        dataset_path=tmp_path / "dataset.jsonl",
        observations_path=tmp_path / "observations.jsonl",
        declared=_DECLARED,
        git_sha="c" * 40,
        knowledge_epoch="20261125_01",
    )
    candidate_declared = dict(_DECLARED)
    candidate_declared["evidence_gate_policy_sha256"] = "d" * 64
    candidate = build_manifest(
        arm="candidate",
        run_id="candidate-run",
        dataset_path=tmp_path / "dataset.jsonl",
        observations_path=tmp_path / "observations.jsonl",
        declared=candidate_declared,
        git_sha="c" * 40,
        knowledge_epoch="20261125_01",
    )

    report = compare(dataset, observations, observations, baseline, candidate)

    assert report["complete"]
    assert report["publication_eligibility"] == "NOT_ASSESSED"
    assert report["production_release_authorized"] is False


def test_manifest_refuses_an_unknown_arm(tmp_path):
    with pytest.raises(AnswerEvalError):
        _build(tmp_path, arm="champion")


def test_manifest_refuses_a_default_knowledge_epoch(tmp_path):
    with pytest.raises(AnswerEvalError):
        _build(tmp_path, knowledge_epoch="default")


def test_manifest_refuses_an_empty_knowledge_epoch(tmp_path):
    with pytest.raises(AnswerEvalError):
        _build(tmp_path, knowledge_epoch="")


def test_manifest_refuses_a_missing_declared_digest(tmp_path):
    declared = dict(_DECLARED)
    declared.pop("permissions_sha256")
    with pytest.raises(AnswerEvalError):
        _build(tmp_path, declared=declared)


def test_manifest_refuses_a_malformed_declared_digest(tmp_path):
    declared = dict(_DECLARED)
    declared["workload_sha256"] = "not-a-digest"
    with pytest.raises(AnswerEvalError):
        _build(tmp_path, declared=declared)


def test_manifest_refuses_a_missing_artifact(tmp_path):
    _build(tmp_path)
    with pytest.raises(AnswerEvalError):
        build_manifest(
            arm="baseline",
            run_id="baseline-run",
            dataset_path=tmp_path / "absent.jsonl",
            observations_path=tmp_path / "observations.jsonl",
            declared=_DECLARED,
            git_sha="c" * 40,
            knowledge_epoch="20261125_01",
        )


def test_manifest_refuses_an_empty_run_identity(tmp_path):
    with pytest.raises(AnswerEvalError):
        _build(tmp_path, run_id="   ")


def test_manifest_refuses_a_non_git_sha(tmp_path):
    with pytest.raises(AnswerEvalError):
        _build(tmp_path, git_sha="not-a-sha")


def test_cli_writes_a_manifest_and_refuses_to_overwrite_it(tmp_path, capsys):
    _build(tmp_path)
    out = tmp_path / "manifest.json"
    argv = [
        "--arm",
        "baseline",
        "--run-id",
        "baseline-run",
        "--dataset",
        str(tmp_path / "dataset.jsonl"),
        "--observations",
        str(tmp_path / "observations.jsonl"),
        "--out",
        str(out),
        "--git-sha",
        "c" * 40,
        "--knowledge-epoch",
        "20261125_01",
    ]
    for key in _DECLARED_DIGESTS:
        argv.extend(["--" + key.replace("_", "-"), _DECLARED[key]])

    assert main(argv) == 0
    manifest = json.loads(out.read_text())
    assert manifest["arm"] == "baseline"
    assert manifest["environment"] == "ISOLATED_EVALUATION"

    # A second run must not silently replace an already declared manifest.
    with pytest.raises(SystemExit):
        main(argv)
