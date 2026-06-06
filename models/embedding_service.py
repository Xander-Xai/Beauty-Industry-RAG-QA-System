"""
Embedding 服务模块

负责文本向量化，支持两种编码器：
- BGE-base-zh-v1.5: 768维文本向量（主检索路径）
- CLIP Text Encoder: 512维文本向量（视觉语义路）

所有向量存储在 Milvus 多 Collection 中（readme 3.4）
"""

from __future__ import annotations

import logging
from typing import Optional

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
    - 统一接口封装，支持模型热切换
    """

    def __init__(self):
        self._bge_model = None
        self._bge_tokenizer = None
        self._clip_model = None
        self._clip_processor = None
        self._milvus_client = None
        logger.info("EmbeddingService 初始化完成（模型懒加载）")

    @property
    def bge_model(self):
        if self._bge_model is None:
            from transformers import AutoTokenizer, AutoModel
            model_path = config["embedding"]["text"]["model_path"]
            self._bge_tokenizer = AutoTokenizer.from_pretrained(model_path)
            self._bge_model = AutoModel.from_pretrained(model_path)
            logger.info(f"BGE 模型加载完成: {model_path}")
        return self._bge_model

    @property
    def bge_tokenizer(self):
        if self._bge_tokenizer is None:
            _ = self.bge_model  # 触发加载
        return self._bge_tokenizer

    @property
    def clip_model(self):
        if self._clip_model is None:
            from transformers import CLIPProcessor, CLIPModel
            model_path = config["embedding"]["image_clip"]["model_path"]
            self._clip_processor = CLIPProcessor.from_pretrained(model_path)
            self._clip_model = CLIPModel.from_pretrained(model_path)
            logger.info(f"CLIP 模型加载完成: {model_path}")
        return self._clip_model

    @property
    def clip_processor(self):
        if self._clip_processor is None:
            _ = self.clip_model  # 触发加载
        return self._clip_processor

    @property
    def milvus_client(self):
        if self._milvus_client is None:
            from pymilvus import connections
            connections.connect(
                alias="default",
                host=config["milvus"]["host"],
                port=config["milvus"]["port"],
            )
            self._milvus_client = True
            logger.info("Milvus 连接完成")
        return self._milvus_client

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
        从 Milvus rag_image_512 采样向量计算质心。
        """
        try:
            from pymilvus import MilvusClient
            collection = config.get("milvus", {}).get("image_collection", "rag_image_512")
            milvus_cfg = config.get("milvus", {})
            host = milvus_cfg.get("host", "localhost")
            port = milvus_cfg.get("port", 19530)

            client = MilvusClient(uri=f"http://{host}:{port}")

            # 采样最多 100 条向量计算质心
            import random
            sample_ids = [str(i) for i in random.sample(range(1, 200), min(100, 199))]
            results = client.get(
                collection_name=collection,
                ids=sample_ids,
                output_fields=["embedding"],
            )

            if not results:
                return None

            embeddings = np.array([r["entity"]["embedding"] for r in results], dtype=np.float32)
            centroid = np.mean(embeddings, axis=0)
            # 归一化
            norm = np.linalg.norm(centroid)
            if norm > 0:
                centroid = centroid / norm
            return centroid
        except Exception as e:
            logger.debug(f"图像质心计算失败: {e}")
            return None

    def search_milvus_text(
        self,
        query_embedding: np.ndarray,
        collection_name: str = None,
        top_k: int = 50,
        filter_expr: str = "",
    ) -> list[dict]:
        """
        Milvus Dense 语义检索

        Args:
            query_embedding: 查询向量
            collection_name: Collection 名称（默认 rag_text_768）
            top_k: 召回数量
            filter_expr: 权限+版本过滤表达式

        Returns:
            [{"doc_id": str, "content": str, "score": float, "metadata": dict}]
        """
        from pymilvus import Collection

        collection_name = collection_name or config["embedding"]["text"]["collection"]
        collection = Collection(collection_name)

        search_params = {
            "metric_type": config["milvus"]["collections"]["rag_text_768"]["metric_type"],
            "params": {"nprobe": 16},
        }

        results = collection.search(
            data=query_embedding.tolist(),
            anns_field="embedding",
            param=search_params,
            limit=top_k,
            expr=filter_expr if filter_expr else None,
            output_fields=["doc_id", "content", "doc_type", "embedding_type"],
        )

        hits = []
        for hit in results[0]:
            hits.append({
                "doc_id": str(hit.entity.get("doc_id")),
                "content": hit.entity.get("content", ""),
                "score": hit.score,
                "metadata": {
                    "doc_type": hit.entity.get("doc_type", ""),
                    "embedding_type": hit.entity.get("embedding_type", ""),
                },
            })
        return hits

    def search_milvus_image(
        self,
        query_embedding: np.ndarray,
        top_k: int = 20,
        filter_expr: str = "",
    ) -> list[dict]:
        """
        Milvus CLIP 图像向量检索

        Args:
            query_embedding: CLIP 文本向量（512d）
            top_k: 召回数量
            filter_expr: 权限+版本过滤表达式

        Returns:
            [{"doc_id": str, "content": str, "image_uri": str, "score": float}]
        """
        from pymilvus import Collection

        collection_name = config["embedding"]["image_clip"]["collection"]
        collection = Collection(collection_name)

        search_params = {
            "metric_type": config["milvus"]["collections"]["rag_image_512"]["metric_type"],
            "params": {"nprobe": 16},
        }

        results = collection.search(
            data=query_embedding.tolist(),
            anns_field="embedding",
            param=search_params,
            limit=top_k,
            expr=filter_expr if filter_expr else None,
            output_fields=["doc_id", "content", "image_uri"],
        )

        hits = []
        for hit in results[0]:
            hits.append({
                "doc_id": str(hit.entity.get("doc_id")),
                "content": hit.entity.get("content", ""),
                "image_uri": hit.entity.get("image_uri", ""),
                "score": hit.score,
            })
        return hits
