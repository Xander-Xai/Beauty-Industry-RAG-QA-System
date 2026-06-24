"""
Dense 语义检索器 - BGE → Qdrant (rag_text_768)

对应 readme 7.1 并行多路召回的第 1 路
"""

from __future__ import annotations

import logging

from common.config import get_config_dict

config = get_config_dict()

logger = logging.getLogger(__name__)


class DenseRetriever:
    """
    Dense 语义检索器

    基于 BGE-base-zh-v1.5 (768d) 向量 + Qdrant Cosine 索引
    """

    def __init__(self):
        self._embedding_service = None
        logger.info("DenseRetriever 初始化完成")

    @property
    def embedding_service(self):
        if self._embedding_service is None:
            from models.embedding_service import EmbeddingService
            self._embedding_service = EmbeddingService()
        return self._embedding_service

    def search(self, query_embedding, qdrant_filter=None, top_k: int = 50) -> list[dict]:
        """
        Dense 语义检索

        Args:
            query_embedding: BGE 查询向量 (768d)
            qdrant_filter: Qdrant Filter 对象（状态/版本过滤）
            top_k: 召回数量

        Returns:
            [{"doc_id": str, "content": str, "score": float, "metadata": dict}]
        """
        try:
            hits = self.embedding_service.search_qdrant_text(
                query_embedding=query_embedding,
                top_k=top_k,
                qdrant_filter=qdrant_filter,
            )
            return hits
        except Exception as e:
            logger.error(f"Dense 检索失败: {e}")
            return []

    def encode(self, text: str):
        """编码文本为向量（供改写泛化路使用）"""
        return self.embedding_service.encode_text(text)