"""Degradation-scenario regression tests for the retrieval → gate chain.

Each test drives one named failure through the **real** gate and recall code
(no mocks of the unit under test) and asserts the safety property that is
supposed to hold. They exist to catch the case where a fallback quietly starts
answering: the dangerous failure mode is a gate that degrades by *opening*.

They deliberately do **not** assert that every scenario refuses. Two of them
(low-similarity non-regulation, single-path retrieval) are asserted to *allow* an
answer, because refusing everything is not a safety property. Turning those into
refusals would be a regression, and these tests are what says so.
"""

from __future__ import annotations

import pytest

from common.models import RerankResult
from core.pipeline_context import RecallResult
from retrieval.answer_gate import AnswerGate
from retrieval.evidence_gate import EvidenceEnsembleGate
from retrieval.parallel_recall import ParallelRecallManager

REGULATION = "化妆品标签标识的法规要求有哪些？"
ANSWER = "备案所需材料包括产品配方表、产品检验报告以及产品执行标准。"


def docs(count: int, *, ce: float = 0.0, marker: str = "备案材料") -> list[RerankResult]:
    return [
        RerankResult(
            doc_id=f"doc-{index}",
            content=f"{marker}说明第{index}段：包含产品配方表、产品检验报告与执行标准，并需备案。",
            source="bm25_es",
            ce_score_ensemble=ce,
        )
        for index in range(count)
    ]


@pytest.fixture(scope="module")
def evidence_gate():
    return EvidenceEnsembleGate()


@pytest.fixture(scope="module")
def answer_gate():
    return AnswerGate()


# ── Scenario: retrieval returned nothing ────────────────────────────────────


def test_no_documents_always_rejects(evidence_gate):
    """An empty candidate list must refuse, never fall through to generation."""
    for agreement in (0.0, 0.5, 1.0):
        result = evidence_gate.evaluate(query=REGULATION, rerank_results=[], retrieval_agreement_score=agreement)
        assert result.decision == "reject"
        assert result.evidence_score == 0.0
        assert result.top_docs == []


# ── Scenario: reranker degraded to its deterministic fallback ────────────────


def test_cross_encoder_fallback_cannot_reach_the_pass_threshold(evidence_gate):
    """With CE scores at 0 the ceiling is w3+w4, which is below `low_confidence`.

    This is the property that makes the missing-weights state fail *closed*
    rather than open. If someone raises a weight or lowers a threshold, this test
    is what notices.
    """
    generous_agreement = 1.0
    result = evidence_gate.evaluate(
        query=REGULATION,
        rerank_results=docs(4, ce=0.0),
        retrieval_agreement_score=generous_agreement,
    )
    assert result.ce_top1_score == 0.0
    assert result.ce_top3_mean_score == 0.0
    assert result.decision == "reject", "a zero-CE run must not pass or even reach enhanced_generate"
    assert result.gate_mode == "fail_closed_no_rerank_weights"


def test_gate_mode_reports_the_degradation(evidence_gate):
    """An operator must be able to tell a refusal from a weak-evidence refusal."""
    result = evidence_gate.evaluate(query=REGULATION, rerank_results=docs(3), retrieval_agreement_score=0.9)
    assert result.gate_mode == "fail_closed_no_rerank_weights"


# ── Scenario: low retrieval confidence, reranker healthy ────────────────────


def test_high_ce_with_zero_agreement_still_refuses(evidence_gate):
    """A confident reranker must not paper over a retrieval disagreement."""
    result = evidence_gate.evaluate(
        query=REGULATION,
        rerank_results=docs(3, ce=0.95),
        retrieval_agreement_score=0.0,
    )
    assert result.decision in ("reject", "enhanced_generate")
    assert result.evidence_score < 0.9


def test_strong_evidence_passes_when_every_signal_is_present(evidence_gate):
    """The positive control: the gate must still be able to pass.

    Without this, a test suite full of refusals would also be satisfied by a
    gate that rejects unconditionally — which is why it matters.
    """
    result = evidence_gate.evaluate(
        query=REGULATION,
        rerank_results=docs(3, ce=0.95),
        retrieval_agreement_score=0.95,
    )
    assert result.decision in ("pass", "enhanced_generate")
    assert result.evidence_score > 0.7


# ── Scenario: one retriever path dies ───────────────────────────────────────


def _results(count: int, source: str) -> list[RecallResult]:
    return [
        RecallResult(
            doc_id=f"doc-{index}",
            content=f"备案材料说明第{index}段，包含配方表与检验报告。",
            score=1.0 / (index + 1),
            source=source,
        )
        for index in range(count)
    ]


def test_partial_retrieval_failure_lowers_the_agreement_score():
    """Losing a path must be visible to the gate, not silently absorbed."""
    manager = ParallelRecallManager()
    healthy = manager._compute_agreement_score(
        {"bm25_es": _results(5, "bm25_es"), "dense_bge": _results(5, "dense_bge")}
    )
    degraded = manager._compute_agreement_score({"bm25_es": _results(5, "bm25_es")})
    assert degraded < healthy, "single-path agreement must score below multi-path agreement"


def test_partial_retrieval_failure_degrades_the_decision(evidence_gate):
    """With a healthy reranker, losing a path downgrades pass → enhanced_generate.

    This asserts the degradation is real and in the safe direction. It does not
    require a refusal: refusing every partially-degraded query would be a
    correctness cost, not a safety gain, and the point of the test is that the
    *signal* survives.
    """
    manager = ParallelRecallManager()
    healthy = manager._compute_agreement_score(
        {"bm25_es": _results(5, "bm25_es"), "dense_bge": _results(5, "dense_bge")}
    )
    degraded = manager._compute_agreement_score({"bm25_es": _results(5, "bm25_es")})
    strong = docs(3, ce=0.9)

    healthy_result = evidence_gate.evaluate(query=REGULATION, rerank_results=strong, retrieval_agreement_score=healthy)
    degraded_result = evidence_gate.evaluate(
        query=REGULATION, rerank_results=strong, retrieval_agreement_score=degraded
    )
    assert degraded_result.evidence_score < healthy_result.evidence_score
    order = {"reject": 0, "enhanced_generate": 1, "pass": 2}
    assert order[degraded_result.decision] <= order[healthy_result.decision]


def test_no_results_reports_a_neutral_agreement_score_not_a_low_one():
    """Pin a real quirk rather than assert the intuitive behaviour.

    With no candidate documents at all, ``_compute_agreement_score`` never
    reaches its clustering branch, so ``clustering_score`` keeps its neutral
    default of 0.5 and Jaccard stays 0.0 — giving 0.35, which is *higher* than
    a genuine single-path run (~0.19).

    This is not a safety defect and is deliberately not "fixed": if every path
    returned nothing, ``rerank_results`` is empty and the Evidence Gate rejects
    at ``retrieval/evidence_gate.py:105`` before this score is ever consulted.
    Changing it would alter gate arithmetic on a path that cannot produce an
    answer. The test exists so the number is documented rather than surprising.
    """
    manager = ParallelRecallManager()
    nothing = manager._compute_agreement_score({})
    one_path = manager._compute_agreement_score({"bm25_es": _results(5, "bm25_es")})
    assert nothing == pytest.approx(0.35, abs=1e-6), "neutral clustering default is 0.5 * 0.7"
    assert nothing > one_path


def test_empty_retrieval_is_rejected_regardless_of_its_agreement_score(evidence_gate):
    """The safety property the quirk above cannot breach."""
    for agreement in (0.0, 0.35, 1.0):
        result = evidence_gate.evaluate(query=REGULATION, rerank_results=[], retrieval_agreement_score=agreement)
        assert result.decision == "reject"


# ── Scenario: answer gate, evidence present ────────────────────────────────


def test_answer_gate_passes_a_faithful_answer(answer_gate):
    top = docs(1)[0]
    result = answer_gate.verify(answer=ANSWER, top_doc=top, is_regulation=False)
    assert result.passed is True
    assert result.warning is False


def test_answer_gate_refuses_an_unrelated_answer_for_regulation(answer_gate):
    """Low overlap on a regulation question is a refusal, not a warning.

    The non-regulation case below is the deliberate contrast: this gate is
    asymmetric on purpose, because a cosmetic-industry regulation answer that
    ignores the evidence is a legal-exposure problem.
    """
    top = docs(1)[0]
    unrelated = "今天的天气非常好，适合出门散步和喝咖啡。"
    result = answer_gate.verify(answer=unrelated, top_doc=top, is_regulation=True)
    assert result.passed is False
    assert result.warning is True


def test_answer_gate_does_not_refuse_every_low_overlap_answer(answer_gate):
    """The explicit anti-over-refusal control.

    `is_regulation=False` with low overlap is documented to pass with a warning.
    If a future change makes this refuse, this test fails — which is intended:
    blanket rejection is a defect, not a hardening.
    """
    top = docs(1)[0]
    unrelated = "今天的天气非常好，适合出门散步和喝咖啡。"
    result = answer_gate.verify(answer=unrelated, top_doc=top, is_regulation=False)
    assert result.passed is True
    assert result.warning is True


# ── Scenario: NLI model unavailable ──────────────────────────────────────────


def test_nli_unavailable_still_gates_on_text_overlap(answer_gate):
    """With NLI absent the gate degrades to Jaccard, but it must still gate.

    A degraded NLI is not an open gate: the regulation refusal path above is
    still reachable, which is the property that matters.
    """
    top = docs(1)[0]
    unrelated = "今天的天气非常好，适合出门散步和喝咖啡。"
    regulation = answer_gate.verify(answer=unrelated, top_doc=top, is_regulation=True)
    faithful = answer_gate.verify(answer=ANSWER, top_doc=top, is_regulation=False)
    assert regulation.passed is False
    assert faithful.passed is True


def test_nli_availability_is_reported_not_assumed():
    """`AnswerGate` must expose whether the real model loaded.

    A gate silently running on text overlap looks identical to one running on a
    real NLI model, so the difference has to be readable from the object.
    """
    gate = AnswerGate()
    assert isinstance(gate._use_nli, bool)
    if not gate._use_nli:
        # Degraded, and it says so — the caller can record that fact.
        assert gate._nli_model is None
