"""
BiEncoder 宽保留重排模块（readme 7.3 Stage 1）

输入：Union Recall Set（去重后约 150~200 条候选）
行为：从各路召回 Top100 中，合并保留 Top 150 条
目标：保证关键证据不会在早期被过滤，为后续投票留出冗余空间
执行方式：GPU1 上的 BiEncoder Batch Service

这是本仓库唯一的 BiEncoder 重排实现。``retrieval-service/rerank/bi_encoder.py``
不再维护第二份拷贝：它是微服务部署入口，直接转发到本模块，因此在线管线
（``core/pipeline.py``）与已部署的 ``retrieval-service`` 使用同一份排序逻辑。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from common.config import get_config_dict

config = get_config_dict()

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from core.pipeline_context import RerankResult

logger = logging.getLogger(__name__)


def _to_rerank_result(candidate, **scores) -> RerankResult:
    """Build a RerankResult, carrying the candidate's provenance through the rerank.

    Without this the ``source_path`` / ``content_hash`` / ``chunk_id`` the recall path
    resolved from the Qdrant payload were discarded here, so the evidence gate and the
    citations built downstream could not name where a passage came from.
    """
    from core.pipeline_context import RerankResult

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
    BiEncoder 宽保留重排器

    Stage 1 Rerank：对 Union Recall Set 进行 BiEncoder 编码，
    保留 Top-150 候选进入 Stage 2 CrossEncoder 精排。

    两条编码路径：
    - 若配置了专用 BiEncoder（``gpu1.bi_encoder.model_path`` 且与共享
      ``embedding.text.model_path`` 不同），优先用该模型编码；失败则回退。
    - 否则使用共享 ``EmbeddingService``。
    两条路径都按余弦相似度排序并保留 top_k；任一步骤抛错都降级为原始候选顺序。
    """

    def __init__(self):
        self._bi_encoder_model = None
        self._bi_encoder_tokenizer = None
        self._embedding_service = None
        self._bi_encoder_model_path = (
            config.get("gpu1", {}).get("models", {}).get("bi_encoder", {}).get("model_path", None)
        )
        self._dedicated_model = None
        logger.info("BiEncoderReranker 初始化完成")

    @property
    def embedding_service(self):
        if self._embedding_service is None:
            from models.embedding_service import EmbeddingService

            self._embedding_service = EmbeddingService()
        return self._embedding_service

    @property
    def dedicated_model_available(self) -> bool:
        """检查是否有专用的 BiEncoder 模型（不同于共享 EmbeddingService）。"""
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
        BiEncoder 宽保留重排

        Args:
            query: 查询文本
            candidates: RecallResult 列表（Union Recall Set）
            top_k: 保留数量（默认 150）

        Returns:
            list[RerankResult] 按 bi_score 降序排列
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
            # 批量编码查询和文档
            query_embedding = self.embedding_service.encode_text(query).flatten()
            doc_texts = [c.content for c in candidates]
            doc_embeddings = self.embedding_service.encode_texts_batch(doc_texts)

            # 计算余弦相似度
            similarities = np.dot(doc_embeddings, query_embedding) / (
                np.linalg.norm(doc_embeddings, axis=1) * np.linalg.norm(query_embedding) + 1e-8
            )

            # 按相似度降序排序，保留 Top-K
            top_indices = np.argsort(similarities)[::-1][:top_k]

            results = [_to_rerank_result(candidates[idx], bi_score=float(similarities[idx])) for idx in top_indices]

            logger.info(f"BiEncoder 重排完成: {len(candidates)} → {len(results)}")
            return results

        except Exception as e:
            logger.error(f"BiEncoder 重排失败: {e}")
            # 降级：返回原始候选的前 top_k 条（同样保留溯源字段）
            return [_to_rerank_result(c, bi_score=0.0) for c in candidates[:top_k]]

    def _rerank_with_dedicated_model(self, query: str, candidates: list, top_k: int) -> list:
        """使用专用 BiEncoder 模型进行重排序（不同于共享 EmbeddingService）。"""
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
