"""
并行多路召回管理器（readme 7.1 节）

设计：并行 4 路召回 + 冗余覆盖，消除延迟翻倍问题
1. Dense 语义路：BGE (Milvus text_768)
2. 关键词精确路：BM25 (ES)
3. 视觉语义路：CLIP (Milvus image_512，受判别路由控制)
4. 改写泛化路：Query Rewrite 变体 Query 的 Dense 检索

权限与版本过滤下推至 Milvus/ES，Python 层不再执行运行时权限过滤。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

logger = logging.getLogger(__name__)


class ParallelRecallManager:
    """
    并行多路召回管理器

    并行执行 4 路召回，每路独立配置 top_k，
    输出 Union Recall Set，后续去冗归一化后进入 Rerank 层。
    """

    def __init__(self):
        self._dense_retriever = None
        self._bm25_retriever = None
        self._clip_retriever = None
        self._rewrite_variants_retriever = None
        self.max_workers = 4
        logger.info("ParallelRecallManager 初始化完成")

    @property
    def dense_retriever(self):
        if self._dense_retriever is None:
            from retrieval.dense_retriever import DenseRetriever
            self._dense_retriever = DenseRetriever()
        return self._dense_retriever

    @property
    def bm25_retriever(self):
        if self._bm25_retriever is None:
            from retrieval.bm25_retriever import BM25Retriever
            self._bm25_retriever = BM25Retriever()
        return self._bm25_retriever

    @property
    def clip_retriever(self):
        if self._clip_retriever is None:
            from retrieval.clip_retriever import CLIPRetriever
            self._clip_retriever = CLIPRetriever()
        return self._clip_retriever

    def execute(
        self,
        query: str,
        query_embedding: np.ndarray,
        user_role_mask: int,
        user_dept_mask: int,
        use_clip: bool = True,
        top_k_per_path: dict = None,
    ) -> list:
        """
        并行执行多路召回

        Args:
            query: 改写后的查询文本
            query_embedding: BGE 查询向量 (768d)
            user_role_mask: 用户角色位掩码
            user_dept_mask: 用户部门位掩码
            use_clip: 是否启用 CLIP 视觉路
            top_k_per_path: 各路 top_k 配置

        Returns:
            list[RecallResult] 所有召回结果
        """
        from core.pipeline_context import RecallResult
        from auth.bitmask_rbac import build_milvus_filter

        top_k_per_path = top_k_per_path or config["retrieval"]["parallel_paths"]

        # 构建 Milvus/ES 权限过滤表达式
        active_epoch = config.get("knowledge_version_epoch", "default")
        filter_expr = build_milvus_filter(user_role_mask, user_dept_mask, active_epoch)

        all_results = []
        futures = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # ① Dense 语义路 (BGE → Milvus)
            if top_k_per_path.get("dense_bge", {}).get("enabled", True):
                futures[executor.submit(
                    self._recall_dense, query_embedding, filter_expr,
                    top_k_per_path["dense_bge"].get("top_k", 50)
                )] = "dense_bge"

            # ② BM25 关键词精确路 (ES)
            if top_k_per_path.get("bm25_es", {}).get("enabled", True):
                futures[executor.submit(
                    self._recall_bm25, query, user_role_mask, user_dept_mask,
                    top_k_per_path["bm25_es"].get("top_k", 50)
                )] = "bm25_es"

            # ③ CLIP 视觉语义路
            if use_clip and top_k_per_path.get("clip_visual", {}).get("enabled", True):
                futures[executor.submit(
                    self._recall_clip, query, filter_expr,
                    top_k_per_path["clip_visual"].get("top_k", 20)
                )] = "clip_visual"

            # ④ 改写泛化路（Query Rewrite 变体）
            if top_k_per_path.get("rewrite_variants", {}).get("enabled", True):
                futures[executor.submit(
                    self._recall_rewrite_variants, query, query_embedding, filter_expr,
                    top_k_per_path["rewrite_variants"].get("top_k", 30)
                )] = "rewrite_variant"

            # 收集结果
            for future in as_completed(futures):
                path_name = futures[future]
                try:
                    results = future.result()
                    all_results.extend(results)
                    logger.info(f"召回路 [{path_name}] 返回 {len(results)} 条结果")
                except Exception as e:
                    logger.error(f"召回路 [{path_name}] 失败: {e}")

        return all_results

    def _recall_dense(self, query_embedding, filter_expr, top_k) -> list:
        """Dense 语义召回"""
        from core.pipeline_context import RecallResult
        hits = self.dense_retriever.search(query_embedding, filter_expr, top_k)
        return [RecallResult(
            doc_id=h["doc_id"], content=h["content"],
            score=h["score"], source="dense_bge", metadata=h.get("metadata", {}),
        ) for h in hits]

    def _recall_bm25(self, query, user_role_mask, user_dept_mask, top_k) -> list:
        """BM25 关键词召回"""
        from core.pipeline_context import RecallResult
        hits = self.bm25_retriever.search(query, user_role_mask, user_dept_mask, top_k)
        return [RecallResult(
            doc_id=h["doc_id"], content=h["content"],
            score=h["score"], source="bm25_es", metadata=h.get("metadata", {}),
        ) for h in hits]

    def _recall_clip(self, query, filter_expr, top_k) -> list:
        """CLIP 视觉语义召回"""
        from core.pipeline_context import RecallResult
        from models.embedding_service import EmbeddingService
        embedding_svc = EmbeddingService()
        clip_embedding = embedding_svc.encode_text_clip(query)
        hits = self.clip_retriever.search(clip_embedding, filter_expr, top_k)
        return [RecallResult(
            doc_id=h["doc_id"], content=h.get("content", ""),
            score=h["score"], source="clip_visual",
            metadata={"image_uri": h.get("image_uri", "")},
        ) for h in hits]

    def _recall_rewrite_variants(self, query, query_embedding, filter_expr, top_k) -> list:
        """改写泛化路召回"""
        from core.pipeline_context import RecallResult
        from rewrite.query_rewriter import QueryRewriter
        rewriter = QueryRewriter()
        variants = rewriter.generate_variants(query)

        results = []
        for variant in variants[:3]:  # 最多 3 个变体
            variant_embedding = self.dense_retriever.encode(variant)
            hits = self.dense_retriever.search(variant_embedding, filter_expr, top_k // 3)
            results.extend([RecallResult(
                doc_id=h["doc_id"], content=h["content"],
                score=h["score"], source="rewrite_variant",
                metadata={"variant_query": variant},
            ) for h in hits])
        return results
