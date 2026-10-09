"""Answer-level, human-judged evaluation.

This is the answer-quality counterpart to the retrieval benchmark. It scores
answers against **human** judgments about evidence support and the gate's
decision — never an LLM self-score.

Design boundaries:

* The labeled set (`answer_eval_set.jsonl`) declares, per query, the expected
  behaviour (``must_refuse``), whether the query is high-risk, and the category.
  It does **not** invent corpus identifiers: the concrete ``doc_id``/``chunk_id``
  a pipeline returns depend on the corpus and are judged per recorded answer.
* The observation record for one query keeps the raw answer, the cited evidence,
  the gate decision and the human judgment together, so a claim can always be
  traced to what was actually produced and how a reviewer rated it.
* Metrics are computed from the human ``evidence_support`` / ``rbac_leak``
  fields. An LLM judge, embedding similarity or the gate's own decision is never
  used as the ground-truth label.

Metrics:

    answer_evidence_support_rate   supported (strict) and supported+partial answers / answered
    unsupported_answer_rate        unsupported answers / answered
    refusal_recall                 correctly-refused / should-refuse
    false_refusal_rate             wrongly-refused / should-answer
    rbac_leak_count                answers citing evidence outside the user's scope
    high_risk_unsupported_rate     unsupported answers on high-risk queries / answered high-risk
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

CATEGORIES = (
    "sufficient_evidence",
    "insufficient_evidence",
    "conflicting_regulation",
    "fabricated_source_trap",
    "unauthorized_document",
    "image_text_conflict",
)

GATE_PASS = "pass"
GATE_ENHANCED = "enhanced_generate"
GATE_REJECT = "reject"
GATE_DECISIONS = (GATE_PASS, GATE_ENHANCED, GATE_REJECT)

EVIDENCE_SUPPORT_LEVELS = ("supported", "partially_supported", "unsupported", "not_applicable")


class AnswerEvalError(RuntimeError):
    """Raised when the answer-eval data is malformed or not attributable."""


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_str(payload: Mapping[str, Any], key: str, where: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AnswerEvalError(f"{where}: {key!r} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class AnswerEvalSample:
    """One labeled query: expected behaviour, not a model output."""

    sample_id: str
    category: str
    question: str
    business_type: str
    must_refuse: bool
    high_risk: bool
    reviewer: str
    reviewed_at: str
    expected_behavior: str = ""
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], where: str = "sample") -> AnswerEvalSample:
        category = _require_str(payload, "category", where)
        if category not in CATEGORIES:
            raise AnswerEvalError(f"{where}: unknown category {category!r}")
        return cls(
            sample_id=_require_str(payload, "sample_id", where),
            category=category,
            question=_require_str(payload, "question", where),
            business_type=_require_str(payload, "business_type", where),
            must_refuse=bool(payload.get("must_refuse")),
            high_risk=bool(payload.get("high_risk")),
            reviewer=_require_str(payload, "reviewer", where),
            reviewed_at=_require_str(payload, "reviewed_at", where),
            expected_behavior=str(payload.get("expected_behavior") or ""),
            notes=str(payload.get("notes") or ""),
        )


@dataclass(frozen=True)
class ObservedAnswer:
    """One recorded answer plus its human judgment."""

    sample_id: str
    answer_text: str
    citations: tuple[str, ...]
    gate_decision: str
    answer_gate_passed: bool | None
    evidence_support: str
    rbac_leak: bool
    reviewer: str
    reviewed_at: str
    notes: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], where: str = "observation") -> ObservedAnswer:
        decision = _require_str(payload, "gate_decision", where)
        if decision not in GATE_DECISIONS:
            raise AnswerEvalError(f"{where}: gate_decision must be one of {GATE_DECISIONS}, got {decision!r}")
        support = _require_str(payload, "evidence_support", where)
        if support not in EVIDENCE_SUPPORT_LEVELS:
            raise AnswerEvalError(
                f"{where}: evidence_support must be one of {EVIDENCE_SUPPORT_LEVELS}, got {support!r}"
            )
        citations = payload.get("citations") or []
        if not isinstance(citations, list):
            raise AnswerEvalError(f"{where}: citations must be a list")
        return cls(
            sample_id=_require_str(payload, "sample_id", where),
            answer_text=str(payload.get("answer_text") or ""),
            citations=tuple(str(item) for item in citations),
            gate_decision=decision,
            answer_gate_passed=payload.get("answer_gate_passed"),
            evidence_support=support,
            rbac_leak=bool(payload.get("rbac_leak")),
            reviewer=_require_str(payload, "reviewer", where),
            reviewed_at=_require_str(payload, "reviewed_at", where),
            notes=str(payload.get("notes") or ""),
            raw=dict(payload.get("raw") or {}),
        )

    @property
    def answered(self) -> bool:
        return self.gate_decision != GATE_REJECT


def load_samples(path: str | Path) -> list[AnswerEvalSample]:
    samples: list[AnswerEvalSample] = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            samples.append(AnswerEvalSample.from_dict(payload, where=f"{path}:{line_number}"))
    if not samples:
        raise AnswerEvalError(f"{path}: answer-eval set is empty")
    ids = [s.sample_id for s in samples]
    if len(ids) != len(set(ids)):
        raise AnswerEvalError(f"{path}: duplicate sample_id(s)")
    return samples


@dataclass(frozen=True)
class AnswerEvalReport:
    dataset_sha256: str
    sample_count: int
    answered_count: int
    refused_count: int
    metrics: dict[str, Any]
    by_category: dict[str, dict[str, Any]]
    missing_observations: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _block(records: Sequence[tuple[AnswerEvalSample, ObservedAnswer]]) -> dict[str, Any]:
    answered = [(s, o) for s, o in records if o.answered]
    strict_supported = sum(1 for _, o in answered if o.evidence_support == "supported")
    partial = sum(1 for _, o in answered if o.evidence_support == "partially_supported")
    unsupported = sum(1 for _, o in answered if o.evidence_support == "unsupported")
    must_refuse = [(s, o) for s, o in records if s.must_refuse]
    should_answer = [(s, o) for s, o in records if not s.must_refuse]
    refused = [(s, o) for s, o in records if not o.answered]
    high_risk_answered = [(s, o) for s, o in answered if s.high_risk]
    high_risk_unsupported = sum(1 for _, o in high_risk_answered if o.evidence_support == "unsupported")
    return {
        "sample_count": len(records),
        "answered_count": len(answered),
        "refused_count": len(refused),
        "answer_evidence_support_rate": _rate(strict_supported, len(answered)),
        "answer_evidence_support_rate_inclusive": _rate(strict_supported + partial, len(answered)),
        "unsupported_answer_rate": _rate(unsupported, len(answered)),
        "refusal_recall": _rate(sum(1 for _, o in must_refuse if not o.answered), len(must_refuse)),
        "false_refusal_rate": _rate(sum(1 for _, o in should_answer if not o.answered), len(should_answer)),
        "rbac_leak_count": sum(1 for _, o in records if o.rbac_leak),
        "high_risk_sample_count": len([s for s, _ in answered if s.high_risk]),
        "high_risk_unsupported_rate": _rate(high_risk_unsupported, len(high_risk_answered)),
    }


def score(
    samples: Sequence[AnswerEvalSample],
    observations: Sequence[ObservedAnswer],
    dataset_sha256: str = "",
) -> AnswerEvalReport:
    """Score observations against labeled samples using human judgments only."""
    by_id = {sample.sample_id: sample for sample in samples}
    observed: dict[str, ObservedAnswer] = {}
    for observation in observations:
        if observation.sample_id not in by_id:
            raise AnswerEvalError(f"observation references unknown sample {observation.sample_id!r}")
        if observation.sample_id in observed:
            raise AnswerEvalError(f"duplicate observation for sample {observation.sample_id!r}")
        observed[observation.sample_id] = observation
    missing = tuple(sample.sample_id for sample in samples if sample.sample_id not in observed)
    records = [(by_id[sid], obs) for sid, obs in observed.items()]
    categories: dict[str, list[tuple[AnswerEvalSample, ObservedAnswer]]] = {}
    for sample, observation in records:
        categories.setdefault(sample.category, []).append((sample, observation))
    return AnswerEvalReport(
        dataset_sha256=dataset_sha256,
        sample_count=len(samples),
        answered_count=sum(1 for _, o in records if o.answered),
        refused_count=sum(1 for _, o in records if not o.answered),
        metrics=_block(records),
        by_category={name: _block(items) for name, items in sorted(categories.items())},
        missing_observations=missing,
    )


def score_files(
    dataset_path: str | Path,
    observations_path: str | Path,
) -> AnswerEvalReport:
    samples = load_samples(dataset_path)
    observations: list[ObservedAnswer] = []
    with open(observations_path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            observations.append(ObservedAnswer.from_dict(json.loads(line), where=f"{observations_path}:{line_number}"))
    return score(samples, observations, dataset_sha256=_sha256(dataset_path))
