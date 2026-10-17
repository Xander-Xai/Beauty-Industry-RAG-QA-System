"""
BiEncoder wide-preservation reranker (service version)

Migrated from retrieval/bi_encoder.py.
Logic is identical; imports updated to use common.models.

Stage 1 Rerank: encodes the Union Recall Set with BiEncoder,
retains Top-150 candidates for Stage 2 CrossEncoder fine-ranking.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import TYPE_CHECKING

import numpy as np

# Ensure project root on sys.path for config.json and shared modules
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from common.config import get_config_dict

config = get_config_dict()

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from common.models import RerankResult

logger = logging.getLogger(__name__)


def _to_rerank_result(candidate, **scores) -> RerankResult:
    """Build a RerankResult, carrying the candidate's provenance through the rerank.

    Same contract as ``retrieval/bi_encoder._to_rerank_result``: the recall path resolved
    ``source_path`` / ``content_hash`` / ``chunk_id`` from the Qdrant payload, and a
    reranked result is what the evidence gate and the citations are built from.
    """
    from common.models import RerankResult

    metadata = dict(getattr(candidate, "metadata", None) or {})
    return RerankResult(
        doc_id=candidate.doc_id,
        content=candidate.content,
        source=getattr(candidate, "source", ""),
        source_path=str(metadata.get("source_path") or ""),
        content_hash=str(metadata.get("content_hash") or ""),
        chunk_id=str(metadata.get("chunk_id") or ""),
        metadata=metadata,
        **scores,
    )


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
        self._bi_encoder_model_path = (
            config.get("gpu1", {}).get("models", {}).get("bi_encoder", {}).get("model_path", None)
        )
        self._dedicated_model = None
        logger.info("BiEncoderReranker initialised")

    @property
    def embedding_service(self):
        if self._embedding_service is None:
            from models.embedding_service import EmbeddingService

            self._embedding_service = EmbeddingService()
        return self._embedding_service

    @property
    def dedicated_model_available(self) -> bool:
        """检查是否有专用的 BiEncoder 模型（不同于共享 EmbeddingService）"""
        if self._dedicated_model is not None:
            return True
        if self._bi_encoder_model_path:
            embedding_path = config.get("embedding", {}).get("text", {}).get("model_path", "")
            return self._bi_encoder_model_path != embedding_path
        return False

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
        if not candidates:
            return []

        # 如果有专用 BiEncoder 模型，优先使用
        if self.dedicated_model_available and self._bi_encoder_model_path:
            try:
                return self._rerank_with_dedicated_model(query, candidates, top_k)
            except Exception as e:
                logger.warning(f"Dedicated BiEncoder failed, falling back to EmbeddingService: {e}")

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

            results = [_to_rerank_result(candidates[idx], bi_score=float(similarities[idx])) for idx in top_indices]

            logger.info(f"BiEncoder rerank complete: {len(candidates)} -> {len(results)}")
            return results

        except Exception as e:
            logger.error(f"BiEncoder rerank failed: {e}")
            # Degradation: return original candidates top_k
            return [_to_rerank_result(c, bi_score=0.0) for c in candidates[:top_k]]

    def _rerank_with_dedicated_model(self, query: str, candidates: list, top_k: int) -> list:
        """使用专用 BiEncoder 模型进行重排序（不同于共享 EmbeddingService）"""
        if self._dedicated_model is None and self._bi_encoder_model_path:
            from sentence_transformers import SentenceTransformer

            self._dedicated_model = SentenceTransformer(self._bi_encoder_model_path)

        if self._dedicated_model is None:
            raise RuntimeError(f"Dedicated BiEncoder model not loaded from {self._bi_encoder_model_path}")

        query_emb = self._dedicated_model.encode(query, normalize_embeddings=True)
        doc_embs = self._dedicated_model.encode(
            [c.content for c in candidates],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        similarities = np.dot(doc_embs, query_emb)
        top_indices = np.argsort(similarities)[::-1][:top_k]

        return [_to_rerank_result(candidates[idx], bi_score=float(similarities[idx])) for idx in top_indices]
