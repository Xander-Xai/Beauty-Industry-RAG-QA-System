"""Evidence Gate fail-closed regression tests (deferred validation VAL-DEGRADE-001).

Issue #84 item 5 and issue #85 ``VAL-DEGRADE-001`` describe the largest
interpretation gap in the repository: with no CrossEncoder weights the rerank
ensemble falls back deterministically, ``ce_top1_score`` and
``ce_top3_mean_score`` stay ``0``, the Evidence Gate ceiling becomes
``w3·agreement + w4·doc_consistency`` and every query is refused.  That is the
correct failure direction, but until this test existed nothing asserted the
end-to-end consequence.

These tests read the thresholds and weights from ``config.json`` (never
hardcoded) and use the fallback-produced rerank results, so they fail if the
ceiling ever rises above ``low_confidence`` without real weights — i.e. if the
fail-closed property is silently lost.  The gate arithmetic and thresholds are
**not** modified; the precheck reports the degradation instead of bypassing it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from common.config import get_config_dict
from core.pipeline_context import RerankResult
from retrieval.cross_encoder_ensemble import CrossEncoderEnsemble
from retrieval.evidence_gate import EvidenceEnsembleGate
from retrieval.rerank_status import rerank_weights_status


def _gate_cfg() -> tuple[dict, dict]:
    cfg = get_config_dict()["retrieval"]["evidence_gate"]
    return cfg["weights"], cfg["thresholds"]


def _no_ce_ceiling(weights: dict) -> float:
    # CE terms are 0 without weights; agreement and doc-consistency max out at 1.
    return float(weights.get("w3", 0.0)) + float(weights.get("w4", 0.0))


# ── precheck ────────────────────────────────────────────────────────────────


class TestRerankPrecheck:
    def test_ceiling_is_the_configured_arithmetic_identity(self):
        """The no-CE ceiling is exactly w3+w4 from config.json, not a literal."""
        weights, thresholds = _gate_cfg()
        status = rerank_weights_status()
        assert status["evidence_gate"]["no_ce_ceiling"] == pytest.approx(_no_ce_ceiling(weights))
        assert status["evidence_gate"]["low_confidence"] == pytest.approx(float(thresholds["low_confidence"]))

    def test_shipped_config_is_fail_closed(self):
        """With the shipped config and no committed weights the gate is BLOCKED."""
        weights, thresholds = _gate_cfg()
        status = rerank_weights_status()
        assert status["failure_mode"] == "fail_closed"
        assert status["status"] == "BLOCKED"
        assert status["evidence_gate"]["fail_closed"] is True
        # The property that makes it fail closed, stated as an inequality.
        assert _no_ce_ceiling(weights) < float(thresholds["low_confidence"])

    def test_status_reports_the_missing_models_by_name(self):
        status = rerank_weights_status()
        assert status["models"]["cross_encoder_a"]["available"] is False
        assert status["models"]["cross_encoder_b"]["available"] is False
        assert "cross_encoder_a" in status["models"]
        assert status["reason"]


# ── the end-to-end consequence on the mainline fallback output ────────────────


class TestFailClosedConsequence:
    def _fallback_rerank_results(self) -> list[RerankResult]:
        """Rerank results exactly as the deterministic fallback produces them."""
        ensemble = CrossEncoderEnsemble()
        aggregator = MagicMock()
        aggregator.batch_predict.side_effect = RuntimeError("CrossEncoder weights missing")
        ensemble._batch_aggregator = aggregator
        candidates = [
            RerankResult(doc_id="d1", content="证据一", bi_score=0.9),
            RerankResult(doc_id="d2", content="证据二", bi_score=0.8),
            RerankResult(doc_id="d3", content="证据三", bi_score=0.7),
        ]
        return ensemble.rerank(query="烟酰胺浓度", candidates=candidates, top_k=10)

    def test_fallback_leaves_ce_scores_at_zero(self):
        results = self._fallback_rerank_results()
        assert results, "fallback must still return the BiEncoder candidates"
        assert all(r.ce_score_ensemble == 0.0 for r in results)
        assert all(r.ce_score_a == 0.0 and r.ce_score_b == 0.0 for r in results)

    def test_every_fixture_is_refused_with_fallback_scores(self):
        """Best possible non-CE inputs (agreement=1, consistency=1) still refuse."""
        results = self._fallback_rerank_results()
        gate = EvidenceEnsembleGate()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(gate, "_compute_doc_consistency", lambda *a, **k: 1.0)
            result = gate.evaluate(
                query="烟酰胺浓度",
                rerank_results=results,
                retrieval_agreement_score=1.0,
            )
        assert result.decision == "reject"
        assert result.evidence_score <= rerank_weights_status()["evidence_gate"]["no_ce_ceiling"] + 1e-9
        assert result.gate_mode == "fail_closed_no_rerank_weights"


# ── scenario matrix: normal / conflicting / no evidence / model missing ──────


class TestDecisionScenarios:
    def _gate_with_weights_available(self) -> EvidenceEnsembleGate:
        """A gate on a host where the CrossEncoder weights are present."""
        gate = EvidenceEnsembleGate()
        available = {
            "status": "AVAILABLE",
            "available": True,
            "failure_mode": "fail_closed",
            "reason": "",
            "evidence_gate": {
                "mode": "normal",
                "no_ce_ceiling": 0.4,
                "low_confidence": 0.55,
                "high_confidence": 0.75,
                "fail_closed": False,
                "effect": "",
            },
        }
        gate.rerank_status = available
        return gate

    def test_normal_evidence_can_generate_when_weights_present(self):
        gate = self._gate_with_weights_available()
        results = [
            RerankResult(doc_id="d1", content="a", ce_score_ensemble=0.95, ce_score_a=3.1, ce_score_b=3.0),
            RerankResult(doc_id="d2", content="b", ce_score_ensemble=0.9, ce_score_a=3.0, ce_score_b=2.9),
            RerankResult(doc_id="d3", content="c", ce_score_ensemble=0.85, ce_score_a=2.9, ce_score_b=2.8),
        ]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(gate, "_compute_doc_consistency", lambda *a, **k: 0.9)
            result = gate.evaluate("q", results, retrieval_agreement_score=0.9)
        assert result.decision == "pass"
        assert result.gate_mode == "normal"

    def test_conflicting_evidence_is_refused(self):
        gate = self._gate_with_weights_available()
        results = [
            RerankResult(doc_id="d1", content="低分a", ce_score_ensemble=0.45, ce_score_a=0.1, ce_score_b=0.1),
            RerankResult(doc_id="d2", content="低分b", ce_score_ensemble=0.40, ce_score_a=0.1, ce_score_b=0.1),
        ]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(gate, "_compute_doc_consistency", lambda *a, **k: 0.0)
            result = gate.evaluate("q", results, retrieval_agreement_score=0.1)
        assert result.decision == "reject"

    def test_no_evidence_is_refused(self):
        gate = EvidenceEnsembleGate()
        result = gate.evaluate(query="q", rerank_results=[], retrieval_agreement_score=1.0)
        assert result.decision == "reject"
        assert result.evidence_score == 0.0
        assert result.gate_mode == "fail_closed_no_rerank_weights"

    def test_gate_reports_runtime_status(self):
        status = EvidenceEnsembleGate().runtime_status()
        assert status["status"] in {"AVAILABLE", "BLOCKED"}
        assert "evidence_gate" in status
