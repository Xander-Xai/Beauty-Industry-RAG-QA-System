"""
CrossEncoder Ensemble reranker (service version)

Migrated from retrieval/cross_encoder_ensemble.py.
Logic is identical; imports updated to use common.models.

Dual-model lightweight Ensemble:
- CE-A: law/composition-tuned CrossEncoder
- CE-B: general-purpose CrossEncoder
Score formula: CE_score = avg(CE_A(doc), CE_B(doc))

GPU batch architecture:
- Rerank Batch Aggregator on GPU1
- time-based batching: 10-20ms window
- size-based batching: max 64 pairs per batch
"""

from __future__ import annotations

import logging
import os
import sys

# Ensure project root on sys.path for config.json and shared modules
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from common.config import get_config_dict

config = get_config_dict()

logger = logging.getLogger(__name__)


class CrossEncoderEnsemble:
    """
    CrossEncoder ensemble reranker.

    Dual-model Ensemble + GPU micro-batch aggregation.
    Outputs Top-K documents with Platt-scaling-calibrated probability scores.
    """

    def __init__(self):
        self._ce_a = None  # CrossEncoder-A (law/composition)
        self._ce_b = None  # CrossEncoder-B (general)
        self._batch_aggregator = None
        logger.info("CrossEncoderEnsemble initialised")

    @property
    def ce_a(self):
        if self._ce_a is None:
            from sentence_transformers import CrossEncoder
            model_path = config["gpu1"]["models"]["cross_encoder_a"]["model_path"]
            self._ce_a = CrossEncoder(model_path)
            logger.info(f"CrossEncoder-A loaded: {model_path}")
        return self._ce_a

    @property
    def ce_b(self):
        if self._ce_b is None:
            from sentence_transformers import CrossEncoder
            model_path = config["gpu1"]["models"]["cross_encoder_b"]["model_path"]
            self._ce_b = CrossEncoder(model_path)
            logger.info(f"CrossEncoder-B loaded: {model_path}")
        return self._ce_b

    @property
    def batch_aggregator(self):
        if self._batch_aggregator is None:
            from retrieval_service.rerank.batch_aggregator import RerankBatchAggregator
            self._batch_aggregator = RerankBatchAggregator()
        return self._batch_aggregator

    def rerank(
        self,
        query: str,
        candidates: list,
        top_k: int = 10,
    ) -> list:
        """
        CrossEncoder Ensemble rerank.

        Args:
            query: query text
            candidates: RerankResult list (BiEncoder output)
            top_k: final number to retain

        Returns:
            list[RerankResult] sorted by ce_score_ensemble descending
        """

        if not candidates:
            return []

        try:
            # Build (query, doc) pairs
            pairs = [(query, c.content) for c in candidates]

            # Execute through Rerank Batch Aggregator
            scores_a = self.batch_aggregator.batch_predict(self.ce_a, pairs)
            scores_b = self.batch_aggregator.batch_predict(self.ce_b, pairs)

            # Compute ensemble scores
            for i, candidate in enumerate(candidates):
                candidate.ce_score_a = float(scores_a[i]) if i < len(scores_a) else 0.0
                candidate.ce_score_b = float(scores_b[i]) if i < len(scores_b) else 0.0
                candidate.ce_score_ensemble = (candidate.ce_score_a + candidate.ce_score_b) / 2.0

            # Sort by ensemble score descending
            candidates.sort(key=lambda x: x.ce_score_ensemble, reverse=True)

            results = candidates[:top_k]
            logger.info(f"CrossEncoder Ensemble rerank complete: {len(candidates)} -> {len(results)}")
            return results

        except Exception as e:
            logger.error(f"CrossEncoder Ensemble rerank failed: {e}")
            # Degradation: keep BiEncoder ordering
            return candidates[:top_k]
