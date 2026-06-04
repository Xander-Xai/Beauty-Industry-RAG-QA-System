"""
Parallel Multi-Path Recall Manager (service version)

Migrated from retrieval/parallel_recall.py.
Logic is identical; imports updated to use common.models.

Design: 4 parallel recall paths with redundant coverage
1. Dense semantic  : BGE (Milvus text_768)
2. Keyword exact   : BM25 (ES)
3. Visual semantic  : CLIP (Milvus image_512, controlled by routing)
4. Rewrite generalise: Query Rewrite variants -> Dense retrieval

Permission and version filtering pushed down to Milvus/ES;
the Python layer does not perform runtime permission filtering.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

# ---------------------------------------------------------------------------
# Ensure project root on sys.path for config.json and shared modules
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Config loaded from project root (CWD is set by main.py)
with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

logger = logging.getLogger(__name__)


class ParallelRecallManager:
    """
    Parallel multi-path recall manager.

    Executes 4 recall paths concurrently, each with independently
    configured top_k.  Outputs a Union Recall Set that will be
    deduplicated and normalised before entering the Rerank layer.
    """

    def __init__(self):
        self._dense_retriever = None
        self._bm25_retriever = None
        self._clip_retriever = None
        self._rewrite_variants_retriever = None
        self.max_workers = 4
        logger.info("ParallelRecallManager initialised")

    @property
    def dense_retriever(self):
        if self._dense_retriever is None:
            from retrieval_service.dense_retriever import DenseRetriever
            self._dense_retriever = DenseRetriever()
        return self._dense_retriever

    @property
    def bm25_retriever(self):
        if self._bm25_retriever is None:
            from retrieval_service.bm25_retriever import BM25Retriever
            self._bm25_retriever = BM25Retriever()
        return self._bm25_retriever

    @property
    def clip_retriever(self):
        if self._clip_retriever is None:
            from retrieval_service.clip_retriever import CLIPRetriever
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
        Execute parallel multi-path recall.

        Args:
            query: rewritten query text
            query_embedding: BGE query vector (768d)
            user_role_mask: user role bitmask
            user_dept_mask: user department bitmask
            use_clip: whether to enable CLIP visual path
            top_k_per_path: per-path top_k configuration

        Returns:
            list[RecallResult] all recall results
        """
        from common.models import RecallResult
        from auth.bitmask_rbac import build_milvus_filter

        top_k_per_path = top_k_per_path or config["retrieval"]["parallel_paths"]

        # Build Milvus/ES permission filter expression
        active_epoch = config.get("knowledge_version_epoch", "default")
        filter_expr = build_milvus_filter(user_role_mask, user_dept_mask, active_epoch)

        all_results = []
        futures = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # (1) Dense semantic path (BGE -> Milvus)
            if top_k_per_path.get("dense_bge", {}).get("enabled", True):
                futures[executor.submit(
                    self._recall_dense, query_embedding, filter_expr,
                    top_k_per_path["dense_bge"].get("top_k", 50)
                )] = "dense_bge"

            # (2) BM25 keyword exact path (ES)
            if top_k_per_path.get("bm25_es", {}).get("enabled", True):
                futures[executor.submit(
                    self._recall_bm25, query, user_role_mask, user_dept_mask,
                    top_k_per_path["bm25_es"].get("top_k", 50)
                )] = "bm25_es"

            # (3) CLIP visual semantic path
            if use_clip and top_k_per_path.get("clip_visual", {}).get("enabled", True):
                futures[executor.submit(
                    self._recall_clip, query, filter_expr,
                    top_k_per_path["clip_visual"].get("top_k", 20)
                )] = "clip_visual"

            # (4) Rewrite generalise path (Query Rewrite variants)
            if top_k_per_path.get("rewrite_variants", {}).get("enabled", True):
                futures[executor.submit(
                    self._recall_rewrite_variants, query, query_embedding, filter_expr,
                    top_k_per_path["rewrite_variants"].get("top_k", 30)
                )] = "rewrite_variant"

            # Collect results
            for future in as_completed(futures):
                path_name = futures[future]
                try:
                    results = future.result()
                    all_results.extend(results)
                    logger.info(f"Recall path [{path_name}] returned {len(results)} results")
                except Exception as e:
                    logger.error(f"Recall path [{path_name}] failed: {e}")

        return all_results

    def _recall_dense(self, query_embedding, filter_expr, top_k) -> list:
        """Dense semantic recall."""
        from common.models import RecallResult
        hits = self.dense_retriever.search(query_embedding, filter_expr, top_k)
        return [RecallResult(
            doc_id=h["doc_id"], content=h["content"],
            score=h["score"], source="dense_bge", metadata=h.get("metadata", {}),
        ) for h in hits]

    def _recall_bm25(self, query, user_role_mask, user_dept_mask, top_k) -> list:
        """BM25 keyword recall."""
        from common.models import RecallResult
        hits = self.bm25_retriever.search(query, user_role_mask, user_dept_mask, top_k)
        return [RecallResult(
            doc_id=h["doc_id"], content=h["content"],
            score=h["score"], source="bm25_es", metadata=h.get("metadata", {}),
        ) for h in hits]

    def _recall_clip(self, query, filter_expr, top_k) -> list:
        """CLIP visual semantic recall."""
        from common.models import RecallResult
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
        """Rewrite generalise recall."""
        from common.models import RecallResult
        from rewrite.query_rewriter import QueryRewriter
        rewriter = QueryRewriter()
        variants = rewriter.generate_variants(query)

        results = []
        for variant in variants[:3]:  # at most 3 variants
            variant_embedding = self.dense_retriever.encode(variant)
            hits = self.dense_retriever.search(variant_embedding, filter_expr, top_k // 3)
            results.extend([RecallResult(
                doc_id=h["doc_id"], content=h["content"],
                score=h["score"], source="rewrite_variant",
                metadata={"variant_query": variant},
            ) for h in hits])
        return results
