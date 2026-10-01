"""
并行多路召回管理器（readme 7.1 节）

设计：并行 4 路召回 + 冗余覆盖，消除延迟翻倍问题
1. Dense 语义路：BGE (Qdrant text_768)
2. 关键词精确路：BM25 (ES)
3. 视觉语义路：CLIP (Qdrant image_512，受判别路由控制)
4. 改写泛化路：Query Rewrite 变体 Query 的 Dense 检索

权限过滤在 Python 层执行（Qdrant pre-filter 处理状态+版本过滤）。
RBAC 位掩码过滤在召回后通过 is_allowed() 二次校验。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

import numpy as np

from common.audit import log_audit_event
from common.config import get_config_dict
from retrieval_service.rerank.rrf_fusion import rrf_fusion

if TYPE_CHECKING:
    from core.pipeline_context import SessionState

config = get_config_dict()

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

    def _apply_rbac_filter(self, results: list, user_role_mask: int, user_dept_mask: int) -> list:
        """
        RBAC 后置过滤

        Qdrant pre-filter 无法处理位掩码运算，因此在 Python 层
        用 is_allowed() 对召回结果进行角色/部门权限过滤。
        """
        from auth.bitmask_rbac import is_allowed
        filtered = []
        for r in results:
            metadata = getattr(r, "metadata", {}) or {}
            doc_role = metadata.get("role_mask", 0)
            doc_dept = metadata.get("dept_mask", 0)
            if is_allowed(doc_role, user_role_mask, doc_dept, user_dept_mask):
                filtered.append(r)
        return filtered

    def execute(
        self,
        query: str,
        query_embedding: np.ndarray,
        user_role_mask: int,
        user_dept_mask: int,
        use_clip: bool = True,
        clip_top_k: int = 20,
        top_k_per_path: dict = None,
        rrf_weights: dict[str, float] | None = None,
    ) -> tuple[list, float]:
        """
        并行执行多路召回

        Args:
            query: 改写后的查询文本
            query_embedding: BGE 查询向量 (768d)
            user_role_mask: 用户角色位掩码
            user_dept_mask: 用户部门位掩码
            use_clip: 是否启用 CLIP 视觉路
            clip_top_k: CLIP 同步召回 top_k
            top_k_per_path: 各路 top_k 配置
            rrf_weights: 当前查询使用的RRF路径权重

        Returns:
            (list[RecallResult], retrieval_agreement_score)
        """
        from auth.bitmask_rbac import build_qdrant_filter

        top_k_per_path = top_k_per_path or config["retrieval"]["parallel_paths"]

        active_epoch = config.get("knowledge_version_epoch", "default")
        qdrant_filter = build_qdrant_filter(user_role_mask, user_dept_mask, active_epoch)

        log_audit_event(
            event_type="recall_filter",
            request_id="",
            user_role_mask=user_role_mask,
            filter_expr=str(qdrant_filter),
        )

        all_results = []
        path_results: dict[str, list] = {}  # 各路径独立结果（用于计算 agreement）
        futures = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # ① Dense 语义路 (BGE → Qdrant)
            if top_k_per_path.get("dense_bge", {}).get("enabled", True):
                futures[executor.submit(
                    self._recall_dense, query_embedding, qdrant_filter,
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
                    self._recall_clip, query, qdrant_filter, clip_top_k
                )] = "clip_visual"

            # ④ 改写泛化路（Query Rewrite 变体）
            if top_k_per_path.get("rewrite_variants", {}).get("enabled", True):
                futures[executor.submit(
                    self._recall_rewrite_variants, query, query_embedding, qdrant_filter,
                    top_k_per_path["rewrite_variants"].get("top_k", 30)
                )] = "rewrite_variant"

            # 收集结果（各路独立存储，用于 RRF 融合）
            for future in as_completed(futures):
                path_name = futures[future]
                try:
                    results = future.result()
                    results = self._apply_rbac_filter(
                        results,
                        user_role_mask=user_role_mask,
                        user_dept_mask=user_dept_mask,
                    )
                    path_results[path_name] = results
                    all_results.extend(results)
                    logger.info(f"召回路 [{path_name}] 返回 {len(results)} 条结果")
                except Exception as e:
                    logger.error(f"召回路 [{path_name}] 失败: {e}")

        # RRF 融合 — 替代简单拼接
        try:
            rrf_config = config["retrieval"]["rrf"]
            fused_results = rrf_fusion(
                path_results,
                k=rrf_config.get("k", 60),
                weights=rrf_weights or rrf_config.get("weights", None),
            )
            all_results = fused_results
            logger.info(
                f"RRF 融合完成: {sum(len(v) for v in path_results.values())} "
                f"输入 → {len(all_results)} 条融合结果"
            )
        except Exception as e:
            logger.warning(f"RRF 融合失败，回退到简单合并: {e}")

        # 计算 Retrieval Agreement Score（PRD §7.2）
        agreement_score = self._compute_agreement_score(path_results)

        # ES Fallback — Qdrant 路径异常或召回不足时触发
        all_doc_ids = {r.doc_id for r in all_results}
        qdrant_paths = {"dense_bge", "clip_visual", "rewrite_variant"}
        qdrant_failed = any(
            path_name in qdrant_paths and path_name not in path_results
            for path_name in qdrant_paths
            if any(futures[f] == path_name for f in futures)
        ) or (len(all_doc_ids) < 10 and any(
            path_name in qdrant_paths for path_name in path_results
            if not path_results[path_name]
        ))
        if qdrant_failed:
            logger.warning("Qdrant 路径异常: 触发 ES Fallback（Qdrant 可能不可用）")
        if len(all_doc_ids) < 50:
            logger.info(f"召回有效文档数 {len(all_doc_ids)} < 50，触发 ES Fallback")
            try:
                fallback_results = self._recall_es_fallback(
                    query, user_role_mask, user_dept_mask, top_k_per_path
                )
                fallback_results = self._apply_rbac_filter(
                    fallback_results,
                    user_role_mask=user_role_mask,
                    user_dept_mask=user_dept_mask,
                )
                if fallback_results:
                    all_results.extend(fallback_results)
                    logger.info(f"ES Fallback 补充召回 {len(fallback_results)} 条")
            except Exception as e:
                logger.warning(f"ES Fallback 失败: {e}")

        return all_results, agreement_score

    def _recall_dense(self, query_embedding, qdrant_filter, top_k) -> list:
        """Dense 语义召回"""
        from core.pipeline_context import RecallResult
        hits = self.dense_retriever.search(query_embedding, qdrant_filter, top_k)
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

    def _recall_clip(self, query, qdrant_filter, top_k) -> list:
        """
        CLIP 视觉语义召回（含 PRD §4.5 120ms 超时保护）
        """
        from concurrent.futures import ThreadPoolExecutor
        from concurrent.futures import TimeoutError as FuturesTimeout

        from core.pipeline_context import RecallResult

        clip_timeout_s = config.get("clip_sync", {}).get("timeout_ms", 120) / 1000.0

        def _do_clip():
            from models.embedding_service import EmbeddingService
            embedding_svc = EmbeddingService()
            clip_embedding = embedding_svc.encode_text_clip(query)
            return self.clip_retriever.search(clip_embedding, qdrant_filter, top_k)

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_do_clip)
                hits = future.result(timeout=clip_timeout_s)
            return [RecallResult(
                doc_id=h["doc_id"], content=h.get("content", ""),
                score=h["score"], source="clip_visual",
                metadata={
                    **(h.get("metadata") or {}),
                    "image_uri": h.get("image_uri", ""),
                },
            ) for h in hits]
        except (FuturesTimeout, TimeoutError) as e:
            logger.warning(f"CLIP 检索超时 ({clip_timeout_s*1000:.0f}ms)，丢弃 CLIP 分支: {e}")
            return []
        except Exception as e:
            logger.error(f"CLIP 视觉语义召回失败: {e}")
            return []

    def _recall_rewrite_variants(self, query, query_embedding, qdrant_filter, top_k) -> list:
        """改写泛化路召回"""
        from core.pipeline_context import RecallResult
        from rewrite.query_rewriter import QueryRewriter
        rewriter = QueryRewriter()
        variants = rewriter.generate_variants(query)

        results = []
        for variant in variants[:3]:
            variant_embedding = self.dense_retriever.encode(variant)
            hits = self.dense_retriever.search(variant_embedding, qdrant_filter, top_k // 3)
            results.extend([RecallResult(
                doc_id=h["doc_id"], content=h["content"],
                score=h["score"], source="rewrite_variant",
                metadata={
                    **(h.get("metadata") or {}),
                    "variant_query": variant,
                },
            ) for h in hits])
        return results

    def _recall_es_fallback(self, query, user_role_mask, user_dept_mask, top_k_per_path) -> list:
        """
        ES Fallback 召回（PRD §7.1）

        当 Qdrant 不可用或召回有效文档数 < 50 时，自动切换到 ES 全文检索。
        """
        from core.pipeline_context import RecallResult
        fallback_top_k = top_k_per_path.get("bm25_es", {}).get("top_k", 50)
        hits = self.bm25_retriever.fallback_search(
            query,
            user_role_mask=user_role_mask,
            user_dept_mask=user_dept_mask,
            top_k=fallback_top_k,
        )
        return [RecallResult(
            doc_id=h["doc_id"], content=h["content"],
            score=h["score"], source="bm25_fallback", metadata=h.get("metadata", {}),
        ) for h in hits]

    def _compute_agreement_score(self, path_results: dict[str, list]) -> float:
        """
        检索一致性评分（PRD §7.2）

        衡量多路召回结果的语义簇一致性：
        1. 主评分：MiniBatchKMeans 聚类 + 簇内熵值（使用 score + hash 代理向量）
        2. 辅评分：各路径 Top-K 文档集合的 Jaccard 相似度均值
        低一致性时 Evidence Gate 应提高阈值（保守策略）。
        """
        all_doc_ids = []
        doc_embeddings = []
        source_topks: dict[str, set[str]] = {}

        for path_name, results in path_results.items():
            doc_ids = set()
            for r in results[:10]:
                doc_ids.add(r.doc_id)
                if r.doc_id not in all_doc_ids:
                    all_doc_ids.append(r.doc_id)
                    # 使用 score + hash 作为代理向量
                    doc_embeddings.append([r.score, hash(r.doc_id) % 1000 / 1000.0])
            source_topks[path_name] = doc_ids

        # ① MiniBatchKMeans 聚类一致性（PRD §7.2 核心要求）
        clustering_score = 0.5  # 默认中性
        if len(doc_embeddings) >= 4:
            try:
                import numpy as np
                from sklearn.cluster import MiniBatchKMeans

                X = np.array(doc_embeddings, dtype=np.float32)
                n_clusters = min(3, len(X) // 2)
                if n_clusters >= 2:
                    km = MiniBatchKMeans(n_clusters=n_clusters, random_state=42, n_init=3)
                    labels = km.fit_predict(X)

                    from collections import Counter
                    total = len(labels)
                    label_counts = Counter(labels)
                    entropy = 0.0
                    for count in label_counts.values():
                        p = count / total
                        if p > 0:
                            entropy -= p * __import__("math").log2(p)
                    max_entropy = __import__("math").log2(n_clusters) if n_clusters > 1 else 1.0
                    clustering_score = 1.0 - (entropy / max_entropy) if max_entropy > 0 else 0.5
                    logger.debug(f"KMeans 聚类一致性: entropy={entropy:.3f}, score={clustering_score:.3f}")
            except Exception as e:
                logger.debug(f"KMeans 聚类评分降级为 Jaccard: {e}")

        # ② Jaccard 相似度均值（辅助评分）
        sources = list(source_topks.values())
        jaccard_score = 0.0
        if len(sources) >= 2:
            agreements = []
            for i in range(len(sources)):
                for j in range(i + 1, len(sources)):
                    intersection = len(sources[i] & sources[j])
                    union = len(sources[i] | sources[j])
                    if union > 0:
                        agreements.append(intersection / union)
            jaccard_score = sum(agreements) / len(agreements) if agreements else 0.0

        # 综合评分：聚类一致性 70% + Jaccard 30%
        return clustering_score * 0.7 + jaccard_score * 0.3

    def recall_async_clip(
        self,
        query: str,
        qdrant_filter,
        session: SessionState = None,
        top_k: int = None,
        user_role_mask: int = 0,
        user_dept_mask: int = 0,
    ) -> list:
        """
        CLIP 异步补召回（PRD §6 异步机制）

        后台 TopK=100 异步执行，结果存入 SessionState.async_clip_results。
        多轮对话时预热复用异步结果。

        Args:
            query: 查询文本
            qdrant_filter: Qdrant Filter 对象
            session: 会话状态（用于存储/复用异步结果）
            top_k: 召回数量（默认从配置读取）
            user_role_mask: 用户角色位掩码
            user_dept_mask: 用户部门位掩码
        """
        from core.pipeline_context import RecallResult

        clip_cfg = config.get("clip_async", {})
        if not clip_cfg.get("enabled", False):
            return []

        # 多轮预热：检查 session 中是否有可复用的异步结果
        if session and clip_cfg.get("preheat_on_multiturn", False):
            cached = session.async_clip_results
            if cached:
                cached = self._apply_rbac_filter(
                    cached,
                    user_role_mask=user_role_mask,
                    user_dept_mask=user_dept_mask,
                )
                logger.info(f"CLIP 异步预热命中: {len(cached)} 条")
                return cached

        top_k = top_k or clip_cfg.get("top_k", 100)

        try:
            from models.embedding_service import EmbeddingService
            embedding_svc = EmbeddingService()
            clip_embedding = embedding_svc.encode_text_clip(query)
            hits = self.clip_retriever.search(clip_embedding, qdrant_filter, top_k)

            results = [RecallResult(
                doc_id=h["doc_id"], content=h.get("content", ""),
                score=h["score"], source="clip_async",
                metadata={
                    **(h.get("metadata") or {}),
                    "image_uri": h.get("image_uri", ""),
                },
            ) for h in hits]
            results = self._apply_rbac_filter(
                results,
                user_role_mask=user_role_mask,
                user_dept_mask=user_dept_mask,
            )

            # 存入 session 供后续轮次复用
            if session:
                session.store_async_clip_result(results)

            return results

        except Exception as e:
            logger.warning(f"CLIP 异步补召回失败: {e}")
            return []
