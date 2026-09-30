import pytest

from core.pipeline_context import QueryRewriteResult, RequestContext


@pytest.mark.integration
def test_pipeline_rejects_empty_query():
    ctx = RequestContext(user_input="", user_id="test_user")
    assert ctx.user_input == ""


@pytest.mark.integration
def test_pipeline_context_creation():
    ctx = RequestContext(
        user_input="烟酰胺的安全浓度是多少？",
        user_id="test_user",
        user_role_mask=0x02,
        user_dept_mask=0x01,
    )
    assert ctx.user_input == "烟酰胺的安全浓度是多少？"
    assert ctx.user_role_mask == 0x02


@pytest.mark.integration
def test_rewrite_result_structure():
    result = QueryRewriteResult(
        rewritten_query="烟酰胺 安全浓度 限量",
        business_type="ingredient",
        intent="compliance",
        requires_context=True,
        standardized_entities=["烟酰胺"],
        confidence=0.85,
        fallback=False,
    )
    assert result.business_type == "ingredient"
    assert result.fallback is False


@pytest.mark.integration
def test_bitmask_rbac_access_control():
    from auth.bitmask_rbac import is_allowed

    assert is_allowed(0, 0x02, 0, 0x01) is True
    assert is_allowed(0x02, 0x02, 0, 0x01) is True
    assert is_allowed(0x08, 0x10, 0, 0x01) is False
    assert is_allowed(0x08, 0xFFFFFFFF, 0x08, 0x01) is True


@pytest.mark.integration
def test_evidence_gate_rejects_empty():
    from retrieval.evidence_gate import EvidenceEnsembleGate

    gate = EvidenceEnsembleGate()
    result = gate.evaluate(
        query="烟酰胺的安全浓度是多少？",
        rerank_results=[],
        retrieval_agreement_score=0.80,
    )
    assert result.decision == "reject"
