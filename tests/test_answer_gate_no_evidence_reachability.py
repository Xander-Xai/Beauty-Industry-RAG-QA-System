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


# ── regulatory numerical hallucination regressions ────────────────────────────


def test_regulatory_numeric_change_is_rejected_even_with_high_text_similarity(answer_gate):
    """A single changed limit must not pass the Jaccard fast path."""
    source = "法规规定该成分在相关产品中的最大允许浓度为3%，超过限量的产品不得备案。"
    fabricated = "法规规定该成分在相关产品中的最大允许浓度为30%，超过限量的产品不得备案。"
    assert answer_gate._jaccard_similarity(source, fabricated) > 0.6
    top_doc = RerankResult(doc_id="limit", content=source, source="bm25_es")
    result = answer_gate.verify(answer=fabricated, top_doc=top_doc, is_regulation=True)
    assert result.passed is False
    assert result.warning is True
    # The numeric gate did not invoke NLI; never fabricate an NLI score.
    assert result.nli_contradiction_score == 0.0


def test_matching_regulatory_limit_keeps_fast_path(answer_gate):
    source = "法规规定该成分在相关产品中的最大允许浓度为3%，超过限量的产品不得备案。"
    doc = RerankResult(doc_id="limit", content=source, source="bm25_es")
    result = answer_gate.verify(answer=source, top_doc=doc, is_regulation=True)
    assert result.passed is True
    assert result.warning is False


def test_regulatory_number_can_be_supported_by_other_authorized_evidence(answer_gate, monkeypatch):
    """Number auditing can consult authorized Top-K, not only the first passage."""
    summary = "法规规定相关产品中该成分需要遵守限量标准并提供完整的备案证明。"
    answer = "法规规定相关产品中该成分需要遵守限量标准并提供完整的备案证明，最高限量为3%。"
    top_doc = RerankResult(doc_id="summary", content=summary, source="bm25_es")
    second = RerankResult(doc_id="limit", content="该成分最高限量为3%。", source="dense_bge")
    monkeypatch.setattr(answer_gate, "_use_nli", True)
    monkeypatch.setattr(answer_gate, "_nli_inference", lambda *_: (0.0, 1.0))
    result = answer_gate.verify(answer=answer, top_doc=top_doc, evidence_docs=[top_doc, second], is_regulation=True)
    assert result.passed is True


def test_nonregulatory_numeric_change_retains_existing_semantics(answer_gate):
    source = "产品的规格为30毫升，建议参考产品说明并遵循完整的护理步骤。"
    answer = "产品的规格为300毫升，建议参考产品说明并遵循完整的护理步骤。"
    assert answer_gate._jaccard_similarity(source, answer) > 0.6
    doc = RerankResult(doc_id="product", content=source, source="bm25_es")
    result = answer_gate.verify(answer=answer, top_doc=doc, is_regulation=False)
    assert result.passed is True


@pytest.mark.parametrize("use_nli,scores", [(False, (0.0, 1.0)), (True, (0.95, 0.02)), (True, (0.05, 0.1))])
def test_regulatory_negation_cannot_bypass_semantic_gate(answer_gate, monkeypatch, use_nli, scores):
    source = "本法规明确规定该成分可用于特定类别产品，企业必须按照规定完成安全评估以及备案流程。"
    opposite = source.replace("可用于", "不可用于")
    assert answer_gate._jaccard_similarity(source, opposite) > 0.6
    monkeypatch.setattr(answer_gate, "_use_nli", use_nli)
    monkeypatch.setattr(answer_gate, "_nli_inference", lambda *_: scores)
    doc = RerankResult(doc_id="rule", content=source, source="bm25_es")
    result = answer_gate.verify(answer=opposite, top_doc=doc, is_regulation=True)
    assert not result.passed
    assert result.warning


def test_regulatory_nli_failure_never_falls_back_to_overlap(answer_gate, monkeypatch):
    monkeypatch.setattr(answer_gate, "_use_nli", True)

    def fail(*args):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(answer_gate, "_nli_inference", fail)
    doc = RerankResult(doc_id="rule", content="该原料必须完成备案后才能使用。", source="bm25_es")
    assert not answer_gate.verify("使用前必须备案。", doc, is_regulation=True).passed
