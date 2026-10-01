"""
Embedding 服务模块

负责文本向量化，支持两种编码器：
- BGE-base-zh-v1.5: 768维文本向量（主检索路径）
- CLIP Text Encoder: 512维文本向量（视觉语义路）

所有向量存储在 Qdrant 多 Collection 中
"""

from __future__ import annotations

import logging

import numpy as np

from common.config import get_config_dict

config = get_config_dict()

logger = logging.getLogger(__name__)


class EmbeddingService:
    """
    Embedding 编码服务

    职责：
    - BGE 文本编码（768d，用于 Dense 语义检索）
    - CLIP 文本编码（512d，用于视觉语义检索）
    - Qdrant 向量检索封装
    - 统一接口封装，支持模型热切换
    """

    def __init__(self):
        self._bge_model = None
        self._bge_tokenizer = None
        self._clip_model = None
        self._clip_processor = None
        self._qdrant_client = None
        logger.info("EmbeddingService 初始化完成（模型懒加载）")

    @property
    def qdrant_client(self):
        if self._qdrant_client is None:
            from qdrant_client import QdrantClient

            self._qdrant_client = QdrantClient(
                host=config["qdrant"]["host"],
                port=config["qdrant"]["port"],
                grpc_port=config["qdrant"]["grpc_port"],
                prefer_grpc=True,
            )
            logger.info(f"Qdrant 连接完成: {config['qdrant']['host']}:{config['qdrant']['port']}")
        return self._qdrant_client

    def encode_text(self, text: str) -> np.ndarray:
        """
        BGE 文本编码（768d）

        Args:
            text: 输入文本

        Returns:
            768维向量 (1, 768)
        """
        import torch

        inputs = self.bge_tokenizer(text, return_tensors="pt", padding=True, truncation=True, max_length=512)
        with torch.no_grad():
            outputs = self.bge_model(**inputs)
        # mean pooling
        embedding = outputs.last_hidden_state.mean(dim=1).numpy()
        return embedding

    def encode_texts_batch(self, texts: list[str]) -> np.ndarray:
        """
        BGE 批量文本编码

        Args:
            texts: 输入文本列表

        Returns:
            (N, 768) 向量矩阵
        """
        import torch

        inputs = self.bge_tokenizer(texts, return_tensors="pt", padding=True, truncation=True, max_length=512)
        with torch.no_grad():
            outputs = self.bge_model(**inputs)
        embeddings = outputs.last_hidden_state.mean(dim=1).numpy()
        return embeddings

    def encode_text_clip(self, text: str) -> np.ndarray:
        """
        CLIP 文本编码（512d）

        Args:
            text: 输入文本

        Returns:
            512维向量
        """
        import torch

        inputs = self.clip_processor(text=[text], return_tensors="pt", padding=True)
        with torch.no_grad():
            text_features = self.clip_model.get_text_features(**inputs)
        return text_features.cpu().numpy().flatten()

    def encode_texts_clip_batch(self, texts: list[str]) -> np.ndarray:
        """
        CLIP 文本批量编码（512d）— PRD §5.1 CLIP Text Encoder 批处理

        Args:
            texts: 输入文本列表

        Returns:
            (N, 512) 向量矩阵
        """
        import torch

        inputs = self.clip_processor(text=texts, return_tensors="pt", padding=True, truncation=True)
        with torch.no_grad():
            text_features = self.clip_model.get_text_features(**inputs)
        # L2 归一化
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        return text_features.cpu().numpy()

    def get_image_centroid(self) -> np.ndarray | None:
        """
        PRD §4.5: 获取图像库质心向量

        用于 CLIP 判别器的第三个分量: query_emb vs image_centroid_sim。
        从 Qdrant rag_image_512 采样向量计算质心。
        """
        try:
            collection_name = config["embedding"]["image_clip"]["collection"]

            # 从 Qdrant scroll 采样向量
            records, _ = self.qdrant_client.scroll(
                collection_name=collection_name,
                limit=100,
                with_payload=False,
                with_vectors=True,
            )

            if not records:
                return None

            embeddings = np.array([r.vector for r in records], dtype=np.float32)
            centroid = np.mean(embeddings, axis=0)
            # 归一化
            norm = np.linalg.norm(centroid)
            if norm > 0:
                centroid = centroid / norm
            return centroid
        except Exception as e:
            logger.debug(f"图像质心计算失败: {e}")
            return None

    def search_qdrant_text(
        self,
        query_embedding: np.ndarray,
        collection_name: str = None,
        top_k: int = 50,
        qdrant_filter=None,
    ) -> list[dict]:
        """
        Qdrant Dense 语义检索

        Args:
            query_embedding: 查询向量 (768d)
            collection_name: Collection 名称（默认 rag_text_768）
            top_k: 召回数量
            qdrant_filter: Qdrant Filter 对象（状态/版本过滤，不含 RBAC）

        Returns:
            [{"doc_id": str, "content": str, "score": float, "metadata": dict}]
        """
        collection_name = collection_name or config["embedding"]["text"]["collection"]
        client = self.qdrant_client

        results = client.search(
            collection_name=collection_name,
            query_vector=query_embedding.flatten().tolist(),
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=["doc_id", "content", "doc_type", "embedding_type", "role_mask", "dept_mask"],
        )

        hits = []
        for point in results:
            hits.append(
                {
                    "doc_id": str(point.payload.get("doc_id", "")),
                    "content": point.payload.get("content", ""),
                    "score": point.score,
                    "metadata": {
                        "doc_type": point.payload.get("doc_type", ""),
                        "embedding_type": point.payload.get("embedding_type", ""),
                        "role_mask": point.payload.get("role_mask"),
                        "dept_mask": point.payload.get("dept_mask"),
                    },
                }
            )
        return hits

    def search_qdrant_image(
        self,
        query_embedding: np.ndarray,
        top_k: int = 20,
        qdrant_filter=None,
    ) -> list[dict]:
        """
        Qdrant CLIP 图像向量检索

        Args:
            query_embedding: CLIP 文本向量（512d）
            top_k: 召回数量
            qdrant_filter: Qdrant Filter 对象（状态/版本过滤，不含 RBAC）

        Returns:
            [{"doc_id": str, "content": str, "image_uri": str, "score": float}]
        """
        collection_name = config["embedding"]["image_clip"]["collection"]
        client = self.qdrant_client

        results = client.search(
            collection_name=collection_name,
            query_vector=query_embedding.flatten().tolist(),
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=["doc_id", "content", "image_uri", "role_mask", "dept_mask"],
        )

        hits = []
        for point in results:
            hits.append(
                {
                    "doc_id": str(point.payload.get("doc_id", "")),
                    "content": point.payload.get("content", ""),
                    "image_uri": point.payload.get("image_uri", ""),
                    "score": point.score,
                    "metadata": {
                        "role_mask": point.payload.get("role_mask"),
                        "dept_mask": point.payload.get("dept_mask"),
                    },
                }
            )
        return hits
