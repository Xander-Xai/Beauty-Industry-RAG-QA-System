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


def clip_cosine_similarity(left, right) -> float:
    """Cosine similarity between two CLIP-space vectors, with an explicit check.

    Both arguments must already live in the same CLIP joint space. This helper refuses
    to produce a number when the two are not comparable, rather than padding, truncating
    or coercing one to match the other — a similarity between mismatched spaces is not a
    small number, it is not a number.

    Raises:
        ValueError: differing lengths, a non-finite norm, or a zero norm.
    """
    left_arr = np.asarray(left, dtype=np.float32).flatten()
    right_arr = np.asarray(right, dtype=np.float32).flatten()
    if left_arr.shape != right_arr.shape:
        raise ValueError(f"vector length mismatch: {left_arr.shape[0]} != {right_arr.shape[0]}")
    left_norm = float(np.linalg.norm(left_arr))
    right_norm = float(np.linalg.norm(right_arr))
    if not np.isfinite(left_norm) or not np.isfinite(right_norm):
        raise ValueError("non-finite vector norm")
    if left_norm == 0.0 or right_norm == 0.0:
        raise ValueError("zero-norm vector has no defined cosine similarity")
    return float(np.dot(left_arr, right_arr) / (left_norm * right_norm))


class EmbeddingService:
    """
    Embedding 编码服务

    职责：
    - BGE 文本编码（768d，用于 Dense 语义检索）
    - CLIP 文本编码（512d，用于视觉语义检索）
    - Qdrant 向量检索封装
    - 统一接口封装，支持模型热切换
    """

    def __init__(self, model_path: str | None = None, clip_model_path: str | None = None):
        self._bge_model = None
        self._bge_tokenizer = None
        self._bge_model_path = model_path or config["embedding"]["text"]["model_path"]
        self._clip_model = None
        self._clip_processor = None
        self._clip_model_path = clip_model_path or config["embedding"]["image_clip"]["model_path"]
        self._qdrant_client = None
        logger.info("EmbeddingService 初始化完成（模型懒加载）")

    @property
    def bge_tokenizer(self):
        if self._bge_tokenizer is None:
            from transformers import AutoTokenizer

            self._bge_tokenizer = AutoTokenizer.from_pretrained(self._bge_model_path)
        return self._bge_tokenizer

    @property
    def bge_model(self):
        if self._bge_model is None:
            from transformers import AutoModel

            self._bge_model = AutoModel.from_pretrained(self._bge_model_path)
            self._bge_model.eval()
        return self._bge_model

    @property
    def clip_processor(self):
        if self._clip_processor is None:
            from transformers import CLIPProcessor

            self._clip_processor = CLIPProcessor.from_pretrained(self._clip_model_path)
        return self._clip_processor

    @property
    def clip_model(self):
        if self._clip_model is None:
            from transformers import CLIPModel

            self._clip_model = CLIPModel.from_pretrained(self._clip_model_path)
            self._clip_model.eval()
        return self._clip_model

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
        embedding = self._mean_pool(outputs.last_hidden_state, inputs["attention_mask"]).numpy()
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
        embeddings = self._mean_pool(outputs.last_hidden_state, inputs["attention_mask"]).numpy()
        return embeddings

    @staticmethod
    def _mean_pool(last_hidden_state, attention_mask):
        """Mean-pool real tokens only so single and padded batch inputs agree."""
        mask = attention_mask.unsqueeze(-1).to(dtype=last_hidden_state.dtype)
        token_sums = (last_hidden_state * mask).sum(dim=1)
        token_counts = mask.sum(dim=1).clamp(min=1)
        return token_sums / token_counts

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
        """PRD §4.5: 获取图像库质心向量

        用于 CLIP 判别器的第三个分量: query_emb vs image_centroid_sim。
        从 Qdrant rag_image_512 采样向量计算质心。

        保留此方法作为兼容包装；需要区分「质心不存在」与「质心查询失败」的调用方
        应使用 :meth:`image_centroid_with_status`。
        """
        centroid, _status = self.image_centroid_with_status()
        return centroid

    def image_centroid_with_status(self) -> tuple[np.ndarray | None, str]:
        """图像库质心 + 结构化状态。

        Returns:
            ``(centroid, status)``。``status`` 为 ``"ok"``、``"no_image_vectors"``
            （collection 为空，属正常情况）或失败分类。质心始终按
            ``embedding.image_clip.dimension`` 校验维度，返回的向量与 CLIP 图像
            向量同处一个 512 维联合空间。
        """
        expected_dim = int(config["embedding"]["image_clip"].get("dimension", 512))
        collection_name = config["embedding"]["image_clip"]["collection"]
        try:
            records, _ = self.qdrant_client.scroll(
                collection_name=collection_name,
                limit=100,
                with_payload=False,
                with_vectors=True,
            )
        except Exception as exc:
            logger.warning("图像质心查询失败 (%s): %s", type(exc).__name__, collection_name)
            return None, "qdrant_error"

        if not records:
            return None, "no_image_vectors"

        try:
            embeddings = np.array([r.vector for r in records], dtype=np.float32)
        except (TypeError, ValueError):
            # A record whose vector is absent or ragged. Reported, never coerced.
            return None, "ragged_vectors"

        if embeddings.ndim != 2:
            return None, "ragged_vectors"
        if embeddings.shape[1] != expected_dim:
            # Never truncate or pad to make the shapes agree.
            logger.warning(
                "图像质心维度不符: collection=%s 实际=%d 期望=%d",
                collection_name,
                embeddings.shape[1],
                expected_dim,
            )
            return None, "dimension_mismatch"

        centroid = np.mean(embeddings, axis=0)
        norm = float(np.linalg.norm(centroid))
        if not np.isfinite(norm) or norm == 0.0:
            # A zero or non-finite centroid would make every similarity undefined.
            return None, "zero_norm"
        return centroid / norm, "ok"

    @property
    def image_clip_dimension(self) -> int:
        """The dimension the image collection and the CLIP encoders must agree on."""
        return int(config["embedding"]["image_clip"].get("dimension", 512))

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

        payload_fields = [
            "doc_id",
            "chunk_id",
            "chunk_index",
            "content",
            "source_path",
            "content_hash",
            "doc_type",
            "embedding_type",
            "role_mask",
            "dept_mask",
            "status",
            "doc_version_epoch",
        ]
        points = []
        seen_doc_ids = set()
        offset = 0
        query_vector = query_embedding.flatten().tolist()
        while len(points) < top_k:
            if hasattr(client, "query_points"):
                response = client.query_points(
                    collection_name=collection_name,
                    query=query_vector,
                    limit=top_k,
                    offset=offset,
                    query_filter=qdrant_filter,
                    with_payload=payload_fields,
                )
                page = response.points
            else:  # Compatibility for older clients and repository test doubles.
                page = client.search(
                    collection_name=collection_name,
                    query_vector=query_vector,
                    limit=top_k,
                    offset=offset,
                    query_filter=qdrant_filter,
                    with_payload=payload_fields,
                )

            for point in page:
                doc_id = (point.payload or {}).get("doc_id")
                if not doc_id or str(doc_id) in seen_doc_ids:
                    continue
                seen_doc_ids.add(str(doc_id))
                points.append(point)
                if len(points) == top_k:
                    break
            if len(page) < top_k or len(points) == top_k:
                break
            offset += len(page)

        hits = []
        for point in points:
            hits.append(
                {
                    "doc_id": str(point.payload.get("doc_id", "")),
                    "content": point.payload.get("content", ""),
                    "score": point.score,
                    "metadata": {
                        "doc_type": point.payload.get("doc_type", ""),
                        "embedding_type": point.payload.get("embedding_type", ""),
                        "chunk_id": point.payload.get("chunk_id"),
                        "chunk_index": point.payload.get("chunk_index"),
                        "source_path": point.payload.get("source_path"),
                        "content_hash": point.payload.get("content_hash"),
                        "status": point.payload.get("status"),
                        "doc_version_epoch": point.payload.get("doc_version_epoch"),
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
        query_vector = query_embedding.flatten().tolist()
        payload_fields = ["doc_id", "content", "ocr_full_text", "image_uri", "role_mask", "dept_mask"]

        if hasattr(client, "query_points"):
            response = client.query_points(
                collection_name=collection_name,
                query=query_vector,
                limit=top_k,
                query_filter=qdrant_filter,
                with_payload=payload_fields,
            )
            results = response.points
        else:  # Compatibility for older clients and repository test doubles.
            results = client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=top_k,
                query_filter=qdrant_filter,
                with_payload=payload_fields,
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
