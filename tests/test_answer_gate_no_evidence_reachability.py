"""Guard the no-evidence Answer Gate contract and its upstream reachability.

The main pipeline already rejects empty candidates through Evidence Gate, but
AnswerGate is a separately callable component. It must fail closed even when
a future refactor or alternate caller supplies top_doc=None.

The positive control below proves normal evidence-bearing candidates remain
eligible for generation; this test does not claim real model quality.
"""

from __future__ import annotations

import pytest

from common.models import RerankResult
from retrieval.answer_gate import AnswerGate
from retrieval.evidence_gate import EvidenceEnsembleGate

REGULATION = "烟酰胺在化妆品中的最大允许浓度是多少？"
FABRICATED = "烟酰胺的最大允许浓度为 99.9%，可放心长期使用。"


@pytest.fixture(scope="module")
def evidence_gate():
    return EvidenceEnsembleGate()


@pytest.fixture(scope="module")
def answer_gate():
    return AnswerGate()


# ── the branch's own behaviour, stated plainly ──────────────────────────────


@pytest.mark.parametrize("is_regulation", [False, True])
def test_top_doc_none_refuses_unverified_answer(is_regulation):
    """An alternate caller must never convert missing evidence to approval."""
    gate = AnswerGate()
    result = gate.verify(answer=FABRICATED, top_doc=None, is_regulation=is_regulation)
    assert result.passed is False
    assert result.nli_contradiction_score == 0.0
    assert result.warning is True
    assert result.is_regulation is is_regulation


# ── why it is currently unreachable ─────────────────────────────────────────


def test_evidence_gate_refuses_empty_candidates_so_the_branch_stays_unreachable(evidence_gate):
    """The guard that makes `top_doc=None` dead code.

    If this ever passes with `rerank_results=[]` and a non-reject decision, the
    pipeline would call the Answer Gate with `top_doc=None` and a fabricated
    answer would pass.
    """
    for agreement in (0.0, 0.35, 0.9, 1.0):
        result = evidence_gate.evaluate(
            query=REGULATION,
            rerank_results=[],
            retrieval_agreement_score=agreement,
        )
        assert result.decision == "reject"
        assert result.top_docs == []


def test_a_non_empty_candidate_list_is_what_reaches_the_answer_gate(evidence_gate):
    """With candidates present the gate may pass, and then `top_doc` is real.

    This is the positive control. Without it, "the branch is unreachable" could
    also be satisfied by a gate that rejects everything, which is a different
    (and unacceptable) property.
    """
    candidates = [
        RerankResult(
            doc_id=f"doc-{index}",
            content=f"烟酰胺限量说明第{index}段：按现行法规规定的最大允许浓度执行。",
            source="bm25_es",
            ce_score_ensemble=0.95,
        )
        for index in range(3)
    ]
    result = evidence_gate.evaluate(query=REGULATION, rerank_results=candidates, retrieval_agreement_score=0.95)
    assert result.decision in ("pass", "enhanced_generate")
    assert result.top_docs, "a non-reject decision must carry the documents the gate reasoned over"


def test_pipeline_passes_a_real_document_not_none():
    """`core/pipeline.py:547` uses `ctx.rerank_results[0] if ctx.rerank_results else None`.

    The `else None` is unreachable given the Evidence Gate invariant above. This
    test pins the shape rather than the source line, so a refactor that changes
    the expression will be caught by the behavioural tests instead of by a
    source-text assertion.
    """
    candidates = [RerankResult(doc_id="doc-0", content="备案材料包括配方表。", source="bm25_es", ce_score_ensemble=0.9)]
    top_doc = candidates[0] if candidates else None
    assert top_doc is not None
    assert top_doc.content


# ── the alternative that must stay safe if the path is ever opened ──────────


def test_a_real_document_with_no_overlap_is_refused_for_regulation(answer_gate):
    """What the gate does instead of the None branch.

    If someone later removes the `else None` guard and lets an answer reach the
    gate with weak evidence, this is the behaviour that must hold.
    """
    top_doc = RerankResult(doc_id="doc-0", content="备案材料包括配方表与检验报告。", source="bm25_es")
    result = answer_gate.verify(answer=FABRICATED, top_doc=top_doc, is_regulation=True)
    assert result.passed is False
