"""
BiEncoder wide-preservation reranker (service version)

Migrated from retrieval/bi_encoder.py.
Logic is identical; imports updated to use common.models.

Stage 1 Rerank: encodes the Union Recall Set with BiEncoder,
retains Top-150 candidates for Stage 2 CrossEncoder fine-ranking.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Optional

import numpy as np

# Ensure project root on sys.path for config.json and shared modules
PROJECT_ROOT = "/home/dev/projects/Intelligent-Q-A-System-for-Automotive-Knowledge"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

logger = logging.getLogger(__name__)


class BiEncoderReranker:
    """
    BiEncoder wide-preservation reranker.

    Stage 1 Rerank: encodes the Union Recall Set with BiEncoder,
    retains Top-150 candidates for Stage 2 CrossEncoder fine-ranking.
    """

    def __init__(self):
        self._bi_encoder_model = None
        self._bi_encoder_tokenizer = None
        self._embedding_service = None
        logger.info("BiEncoderReranker initialised")

    @property
    def embedding_service(self):
        if self._embedding_service is None:
            from models.embedding_service import EmbeddingService
            self._embedding_service = EmbeddingService()
        return self._embedding_service

    def rerank(
        self,
        query: str,
        candidates: list,
        top_k: int = 150,
    ) -> list:
        """
        BiEncoder wide-preservation rerank.

        Args:
            query: query text
            candidates: RecallResult list (Union Recall Set)
            top_k: number to retain (default 150)

        Returns:
            list[RerankResult] sorted by bi_score descending
        """
        from common.models import RerankResult

        if not candidates:
            return []

        try:
            # Batch-encode query and documents
            query_embedding = self.embedding_service.encode_text(query).flatten()
            doc_texts = [c.content for c in candidates]
            doc_embeddings = self.embedding_service.encode_texts_batch(doc_texts)

            # Cosine similarity
            similarities = np.dot(doc_embeddings, query_embedding) / (
                np.linalg.norm(doc_embeddings, axis=1) * np.linalg.norm(query_embedding) + 1e-8
            )

            # Sort by similarity descending, keep Top-K
            top_indices = np.argsort(similarities)[::-1][:top_k]

            results = []
            for idx in top_indices:
                candidate = candidates[idx]
                results.append(RerankResult(
                    doc_id=candidate.doc_id,
                    content=candidate.content,
                    bi_score=float(similarities[idx]),
                ))

            logger.info(f"BiEncoder rerank complete: {len(candidates)} -> {len(results)}")
            return results

        except Exception as e:
            logger.error(f"BiEncoder rerank failed: {e}")
            # Degradation: return original candidates top_k
            return [
                RerankResult(doc_id=c.doc_id, content=c.content, bi_score=0.0)
                for c in candidates[:top_k]
            ]
