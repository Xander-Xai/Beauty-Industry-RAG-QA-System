"""
Evidence Ensemble Gate (service version)

Migrated from retrieval/evidence_gate.py.
Logic is identical; imports updated to use common.models.

Multi-dimensional evidence voting mechanism:

Composite confidence formula:
  Evidence Score =
    w1 * CE_Top1_Score +
    w2 * CE_Top3_Mean_Score +
    w3 * Retrieval_Agreement_Score +
    w4 * Doc_Consistency_Score

Decision rules:
  >= 0.75 -> pass (high confidence, enter LLM generation directly)
  0.55-0.75 -> enhanced_generate (inject Top-3 document summaries)
  <  0.55 -> reject (return guided rejection, HTTP 200)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Optional

# Ensure project root on sys.path for config.json and shared modules
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

logger = logging.getLogger(__name__)


class EvidenceEnsembleGate:
    """
    Evidence Ensemble Gate - multi-dimensional evidence voting.

    Weights w1-w4 are learned from offline logs, optimised to maximise the
    correlation between human-annotated "answer usability" and "click-through
    rate".  Updated weekly.
    """

    def __init__(self):
        self.weights = config["retrieval"]["evidence_gate"]["weights"]
        self.thresholds = config["retrieval"]["evidence_gate"]["thresholds"]
        self._answer_gate = None
        logger.info("EvidenceEnsembleGate initialised")

    @property
    def answer_gate(self):
        """Reuse AnswerGate's NLI model instance."""
        if self._answer_gate is None:
            from retrieval_service.rerank.answer_gate import AnswerGate
            self._answer_gate = AnswerGate()
        return self._answer_gate

    def evaluate(
        self,
        query: str,
        rerank_results: list,
        retrieval_agreement_score: float = 0.0,
    ) -> "EvidenceGateResult":
        """
        Evaluate composite evidence confidence.

        Args:
            query: query text
            rerank_results: CrossEncoder output RerankResult list
            retrieval_agreement_score: retrieval agreement score (readme 7.2)

        Returns:
            EvidenceGateResult with decision
        """
        from common.models import EvidenceGateResult, RerankResult

        if not rerank_results:
            return EvidenceGateResult(
                evidence_score=0.0,
                ce_top1_score=0.0,
                ce_top3_mean_score=0.0,
                retrieval_agreement_score=0.0,
                doc_consistency_score=0.0,
                decision="reject",
                top_docs=[],
            )

        # (1) CE_Top1_Score
        ce_top1_score = rerank_results[0].ce_score_ensemble

        # (2) CE_Top3_Mean_Score
        top3_scores = [r.ce_score_ensemble for r in rerank_results[:3]]
        ce_top3_mean_score = sum(top3_scores) / len(top3_scores) if top3_scores else 0.0

        # (3) Retrieval_Agreement_Score (computed externally, passed in)

        # (4) Doc_Consistency_Score (NLI cross-check Top-3 doc logical consistency)
        doc_consistency_score = self._compute_doc_consistency(query, rerank_results[:3])

        # Composite Evidence Score
        evidence_score = (
            self.weights["w1"] * ce_top1_score +
            self.weights["w2"] * ce_top3_mean_score +
            self.weights["w3"] * retrieval_agreement_score +
            self.weights["w4"] * doc_consistency_score
        )

        # Decision
        if evidence_score >= self.thresholds["high_confidence"]:
            decision = "pass"
        elif evidence_score >= self.thresholds["low_confidence"]:
            decision = "enhanced_generate"
        else:
            decision = "reject"

        result = EvidenceGateResult(
            evidence_score=evidence_score,
            ce_top1_score=ce_top1_score,
            ce_top3_mean_score=ce_top3_mean_score,
            retrieval_agreement_score=retrieval_agreement_score,
            doc_consistency_score=doc_consistency_score,
            decision=decision,
            top_docs=rerank_results[:3],
        )

        logger.info(
            f"Evidence Gate: score={evidence_score:.3f}, decision={decision} | "
            f"CE_Top1={ce_top1_score:.3f}, CE_Top3_Mean={ce_top3_mean_score:.3f}, "
            f"Agreement={retrieval_agreement_score:.3f}, Consistency={doc_consistency_score:.3f}"
        )
        return result

    def _compute_doc_consistency(self, query: str, top_docs: list) -> float:
        """
        Inter-document logical contradiction detection (NLI cross-check, GPU Batch).

        For each pair in Top-3 candidates, run NLI
        (premise=doc_i content, hypothesis=doc_j content).
        Higher proportion of non-contradictory pairs -> higher consistency score.
        """
        if len(top_docs) < 2:
            return 1.0

        try:
            doc_texts = [doc.content[:512] for doc in top_docs]

            contradiction_count = 0
            total_pairs = 0

            for i in range(len(doc_texts)):
                for j in range(i + 1, len(doc_texts)):
                    # NLI: premise=doc_i, hypothesis=doc_j
                    contradiction, entailment = self.answer_gate._nli_inference(
                        doc_texts[i], doc_texts[j]
                    )
                    if contradiction > 0.5:
                        contradiction_count += 1
                    total_pairs += 1

            if total_pairs == 0:
                return 1.0

            # Consistency = 1 - proportion of contradictory doc pairs
            consistency = 1.0 - (contradiction_count / total_pairs)
            return consistency

        except Exception as e:
            logger.warning(f"NLI doc consistency check failed, using default: {e}")
            return 0.8
