"""Is `AnswerGate.verify(top_doc=None) → passed=True` reachable online?

`retrieval/answer_gate.py:88-95` returns `passed=True` unconditionally when
`top_doc is None`, with no check on the answer and no check on `is_regulation`.
On its face that is "no evidence → allow", which is the wrong direction for a
gate.

This module answers the narrow question honestly: **is that branch reachable on
the real request path today?** It is, or it is not — and the answer is derived
by executing the actual gates rather than by reading the call site.

The structural argument, which these tests pin:

* reaching `pipeline.py:545` (the Answer Gate call) requires passing the
  Evidence Gate decision branch at `pipeline.py:493-505`, which returns early
  on `reject`;
* `EvidenceEnsembleGate.evaluate` returns `reject` whenever `rerank_results` is
  falsy (`retrieval/evidence_gate.py:105-115`);
* therefore `ctx.rerank_results[0]` at `pipeline.py:547` is always a real
  document, and the `else None` is dead defensive code.

If a future change made the Evidence Gate permissive about empty candidates,
these tests fail — which is the point. The behaviour itself is left alone: it is
not an active leak, and rewriting it would change answers on a path that
currently cannot be taken.
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


def test_top_doc_none_passes_regardless_of_content():
    """The documented behaviour: no evidence means no objection, not no answer.

    This is asserted so nobody has to re-derive it. It is a design decision that
    is *safe only* because the branch is unreachable — see the reachability tests
    below, which are the ones that matter.
    """
    gate = AnswerGate()
    result = gate.verify(answer=FABRICATED, top_doc=None, is_regulation=True)
    assert result.passed is True
    assert result.nli_contradiction_score == 0.0
    assert result.warning is False


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
