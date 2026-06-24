"""
CLIP 视觉语义检索模块（readme 7.1 并行多路召回第 3 路）

基于 CLIP-ViT-B/16 图像向量 (512d) + Qdrant 检索
受 CLIP 判别式路由控制（readme 4.5）
"""

from __future__ import annotations

import logging

from common.config import get_config_dict

config = get_config_dict()

logger = logging.getLogger(__name__)


class CLIPRetriever:
    """
    CLIP 视觉语义检索器

    检索已离线向量化的图像（CLIP 512d），
    使用 CLIP Text Encoder 将查询编码后在 Qdrant rag_image_512 Collection 中检索
    """

    def __init__(self):
        self._embedding_service = None
        logger.info("CLIPRetriever 初始化完成")

    @property
    def embedding_service(self):
        if self._embedding_service is None:
            from models.embedding_service import EmbeddingService
            self._embedding_service = EmbeddingService()
        return self._embedding_service

    def search(self, clip_embedding, qdrant_filter=None, top_k: int = 20) -> list[dict]:
        """
        CLIP 图像向量检索

        Args:
            clip_embedding: CLIP 文本向量 (512d)
            qdrant_filter: Qdrant Filter 对象（状态/版本过滤）
            top_k: 召回数量

        Returns:
            [{"doc_id": str, "content": str, "image_uri": str, "score": float}]
        """
        try:
            hits = self.embedding_service.search_qdrant_image(
                query_embedding=clip_embedding,
                top_k=top_k,
                qdrant_filter=qdrant_filter,
            )
            return hits
        except Exception as e:
            logger.error(f"CLIP 图像检索失败: {e}")
            return []