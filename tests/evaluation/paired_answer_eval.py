"""Pair independently reviewed answer observations on one frozen population.

Offline diagnostic only. No model calls, gate bypass or publication approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from tests.evaluation.answer_eval import (
    AnswerEvalError,
    ObservedAnswer,
    _parse_samples,
    _strict_task_success,
    score,
)

_HEX = re.compile(r"[0-9a-f]{64}")
_GIT = re.compile(r"[0-9a-f]{40}")
_SHARED = (
    "dataset_sha256",
    "git_sha",
    "corpus_sha256",
    "knowledge_epoch",
    "model_assets_sha256",
    "retrieval_contract_sha256",
    "permissions_sha256",
    "workload_sha256",
    "runtime_config_sha256",
    "review_protocol_sha256",
)
_ARMS = ("baseline", "candidate")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _validate_manifest(manifest: dict[str, Any], dataset_raw: bytes, raw: bytes, arm: str) -> None:
    if not isinstance(manifest, dict) or manifest.get("arm") != arm:
        raise AnswerEvalError("Invalid or swapped experiment arm")
    if manifest.get("environment") != "ISOLATED_EVALUATION":
        raise AnswerEvalError("Only isolated evaluation observations are accepted")
    if manifest.get("answer_gate_enabled") is not True or manifest.get("rbac_enabled") is not True:
        raise AnswerEvalError("Answer Gate and RBAC must remain enforced")
    if manifest.get("experiment_variable") != "evidence_gate_policy":
        raise AnswerEvalError("Only Evidence Gate policy comparison is supported")
    for key in (*_SHARED, "observations_sha256", "evidence_gate_policy_sha256"):
        value = manifest.get(key)
        if key == "knowledge_epoch":
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value) or value == "default":
                raise AnswerEvalError("Invalid knowledge epoch")
        elif not isinstance(value, str) or not (_GIT if key == "git_sha" else _HEX).fullmatch(value):
            raise AnswerEvalError(f"Invalid or missing manifest field: {key}")
    if manifest["dataset_sha256"] != _sha(dataset_raw) or manifest["observations_sha256"] != _sha(raw):
        raise AnswerEvalError("Input bytes do not match manifest hashes")
    if not isinstance(manifest.get("run_id"), str) or not manifest["run_id"].strip():
        raise AnswerEvalError("Missing run identity")


def _read_observations(raw: bytes) -> tuple[list[ObservedAnswer], dict[str, float | None]]:
    observations = []
    timings: dict[str, float | None] = {}
    for i, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or row.get("review_status") != "HUMAN_REVIEWED":
            raise AnswerEvalError(f"line {i}: explicit human review is required")
        if row.get("final_disposition") not in ("answered", "refused"):
            raise AnswerEvalError(f"line {i}: final disposition required")
        item = ObservedAnswer.from_dict(row, where=f"line {i}")
        if item.sample_id in timings:
            raise AnswerEvalError("Duplicate observation identity")
        elapsed = row.get("end_to_end_ms")
        if elapsed is not None and (type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0):
            raise AnswerEvalError("Invalid end-to-end timing")
        timings[item.sample_id] = elapsed
        observations.append(item)
    return observations, timings


def _p95(timings: dict[str, float | None], planned: int) -> float | None:
    if len(timings) != planned or any(value is None for value in timings.values()):
        return None
    values = sorted(value for value in timings.values() if value is not None)
    return values[math.ceil(0.95 * len(values)) - 1]


def compare(
    dataset_raw: bytes,
    baseline_raw: bytes,
    candidate_raw: bytes,
    baseline_manifest: dict[str, Any],
    candidate_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Reject mismatched declared identities, preserve missing outcomes and regressions."""
    samples = _parse_samples(dataset_raw, "frozen dataset")
    for arm, raw, manifest in zip(
        _ARMS, (baseline_raw, candidate_raw), (baseline_manifest, candidate_manifest), strict=True
    ):
        _validate_manifest(manifest, dataset_raw, raw, arm)
    if (
        any(baseline_manifest[key] != candidate_manifest[key] for key in _SHARED)
        or baseline_manifest["run_id"] == candidate_manifest["run_id"]
        or baseline_manifest["evidence_gate_policy_sha256"] == candidate_manifest["evidence_gate_policy_sha256"]
    ):
        raise AnswerEvalError("Unpaired contract or unchanged gate policy")
    left, lt = _read_observations(baseline_raw)
    right, rt = _read_observations(candidate_raw)
    reports = [score(samples, arm) for arm in (left, right)]
    maps = [{item.sample_id: item for item in arm} for arm in (left, right)]
    rows = []
    for sample in samples:
        outcomes = []
        for mapping in maps:
            obs = mapping.get(sample.sample_id)
            outcomes.append(None if obs is None else _strict_task_success(sample, obs))
        rows.append(
            {
                "sample_id": sample.sample_id,
                "category": sample.category,
                "high_risk": sample.high_risk,
                "baseline_success": outcomes[0],
                "candidate_success": outcomes[1],
            }
        )
    n = len(samples)
    complete = all(not report.missing_observations for report in reports)
    gains = [row["sample_id"] for row in rows if row["baseline_success"] is False and row["candidate_success"] is True]
    losses = [row["sample_id"] for row in rows if row["baseline_success"] is True and row["candidate_success"] is False]
    high_risk_losses = [row["sample_id"] for row in rows if row["high_risk"] and row["sample_id"] in losses]
    arm_results = {}
    for name, report, timings in zip(_ARMS, reports, (lt, rt), strict=True):
        successes = report.metrics["strict_task_success_count"]
        missing = len(report.missing_observations)
        arm_results[name] = {
            "metrics": report.metrics,
            "missing_ids": list(report.missing_observations),
            "coverage": (n - missing) / n,
            "strict_success_lower_bound": successes / n,
            "strict_success_upper_bound": (successes + missing) / n,
            "declared_end_to_end_p95_ms": _p95(timings, n),
        }
    # A zero-leak or no-regression observation on an incomplete population is
    # not sufficient to accept a candidate; no automatic architecture change.
    return {
        "kind": "PAIRED_REVIEWED_ANSWER_DIAGNOSTIC_ONLY",
        "dataset_sha256": _sha(dataset_raw),
        "baseline_observations_sha256": _sha(baseline_raw),
        "candidate_observations_sha256": _sha(candidate_raw),
        "baseline_run_id": baseline_manifest["run_id"],
        "candidate_run_id": candidate_manifest["run_id"],
        "planned": n,
        "complete": complete,
        "arms": arm_results,
        "strict_success_delta": (
            reports[1].metrics["strict_task_success_count"] - reports[0].metrics["strict_task_success_count"]
        )
        / n
        if complete
        else None,
        "improved_ids": gains,
        "regressed_ids": losses,
        "high_risk_regressed_ids": high_risk_losses,
        "candidate_has_no_observed_strict_regression": not losses if complete else None,
        "candidate_has_no_observed_rbac_leak": (reports[1].metrics["rbac_leak_count"] == 0 if complete else None),
        "cases": rows,
        "publication_eligibility": "NOT_ASSESSED",
        "runtime_provenance_independently_verified": False,
        "ragas_faithfulness": "NOT_MEASURED",
        "production_release_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("dataset", "baseline", "candidate", "baseline-manifest", "candidate-manifest", "out"):
        parser.add_argument("--" + option, required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = compare(
            args.dataset.read_bytes(),
            args.baseline.read_bytes(),
            args.candidate.read_bytes(),
            json.loads(args.baseline_manifest.read_bytes()),
            json.loads(args.candidate_manifest.read_bytes()),
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    except (OSError, ValueError, TypeError, AttributeError, AnswerEvalError) as exc:
        parser.error(f"invalid paired answer evidence ({type(exc).__name__})")
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
