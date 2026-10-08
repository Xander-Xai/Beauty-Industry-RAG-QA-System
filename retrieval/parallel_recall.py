"""
并行多路召回管理器（readme 7.1 节）

设计：动态 2–4 路召回 + 冗余覆盖，消除延迟翻倍问题。四条可选通道分别是：
1. Dense 语义路：BGE (Qdrant text_768) —— 所有问题
2. 关键词精确路：BM25 (ES) —— 所有问题
3. 视觉语义路：CLIP (Qdrant image_512，受判别路由控制) —— 仅复杂且视觉相关
4. 改写泛化路：Query Rewrite 变体 Query 的 Dense 检索 —— 仅复杂问题

简单问题只跑 1+2 两路；复杂问题加第 4 路；复杂且视觉相关再加第 3 路。
在线主链路（core/pipeline.py）因此是动态 2–4 路。实际启用哪些通道由调用方本次传入的
top_k_per_path 决定：省略的通道即本次禁用，传 None 表示使用 config.json 的完整拓扑。
本管理器本身不保证路数下界——直接调用 execute() 可以传任意子集或空映射。

ES Fallback 是一次降级补召回，不是新的一路：它在 RRF 之后直接追加到 all_results，
不进入 path_results，因此既不参与融合也不计入路数。其触发条件**只有**融合后有效文档数
< FALLBACK_MIN_DOC_IDS 这一个；Qdrant 路径异常只记一条说明「哪一路降级了」的 warning，
不会独立触发 fallback，warning 里也不声称 fallback 已触发。

融合只有这一个入口：下面的 `rrf_fusion` 调用。查询感知权重由 `rrf_weights` 传入并在此
一次性消费；调用链下游（`core/pipeline.py` 的 `_union_dedup`）不再重算分数、不再重排。

路径名即 `path_results` 的 key，也是 `config.json` → `retrieval.parallel_paths` 的键、
`_build_rrf_weights()` 产出权重的 key。三处必须逐字一致，否则该路的权重会被静默忽略、
该路的失败诊断分支会永不可达。

权限过滤在 Python 层执行（Qdrant pre-filter 处理状态+版本过滤）。
RBAC 位掩码过滤在召回后通过 is_allowed() 二次校验，并在融合与 fallback 之后再兜底一次。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from common.audit import log_audit_event
from common.config import get_config_dict
from core.pipeline_context import SessionState
from retrieval_service.rerank.rrf_fusion import rrf_fusion

config = get_config_dict()

logger = logging.getLogger(__name__)

#: Unique documents below this count after fusion trigger the ES fallback.
#: The only trigger for that supplementary recall; a failing Qdrant path is
#: reported but does not start it.
FALLBACK_MIN_DOC_IDS = 50


class ParallelRecallManager:
    """
    并行多路召回管理器

    并行执行本次请求 top_k_per_path 中启用（或在完整拓扑下默认启用）的通道，
    每路独立配置 top_k，输出 Union Recall Set，后续去冗归一化后进入 Rerank 层。
    在线主链路（core/pipeline.py）启用 2–4 路；本管理器不强制这个区间。
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
        from common.auth import is_document_authorized

        filtered = []
        for r in results:
            metadata = getattr(r, "metadata", None)
            if is_document_authorized(metadata, user_role_mask, user_dept_mask):
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
            rrf_weights: 本次查询的各路 RRF 权重（query-aware）。为 None 时回退到
                config.json 的静态 retrieval.rrf.weights。显式传入空 dict 表示
                「不做加权」，由 rrf_fusion 按等权重（1.0）处理。

        Returns:
            (list[RecallResult], retrieval_agreement_score)
        """
        from auth.bitmask_rbac import build_qdrant_filter, build_qdrant_image_filter

        # None means "use the canonical full topology".  A caller-supplied
        # mapping is instead the topology for *this request*: omitted paths are
        # intentionally disabled (the online pipeline uses this to route simple
        # queries through BGE + BM25 only).
        if top_k_per_path is None:
            top_k_per_path = config["retrieval"]["parallel_paths"]

        active_epoch = config.get("knowledge_version_epoch", "default")
        qdrant_filter = build_qdrant_filter(user_role_mask, user_dept_mask, active_epoch)
        image_qdrant_filter = build_qdrant_image_filter(user_role_mask, user_dept_mask, active_epoch)

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
            dense_cfg = top_k_per_path.get("dense_bge")
            if dense_cfg is not None and dense_cfg.get("enabled", True):
                futures[
                    executor.submit(self._recall_dense, query_embedding, qdrant_filter, dense_cfg.get("top_k", 50))
                ] = "dense_bge"

            # ② BM25 关键词精确路 (ES)
            bm25_cfg = top_k_per_path.get("bm25_es")
            if bm25_cfg is not None and bm25_cfg.get("enabled", True):
                futures[
                    executor.submit(
                        self._recall_bm25,
                        query,
                        user_role_mask,
                        user_dept_mask,
                        bm25_cfg.get("top_k", 50),
                    )
                ] = "bm25_es"

            # ③ CLIP 视觉语义路
            clip_cfg = top_k_per_path.get("clip_visual")
            if use_clip and clip_cfg is not None and clip_cfg.get("enabled", True):
                futures[executor.submit(self._recall_clip, query, image_qdrant_filter, clip_top_k)] = "clip_visual"

            # ④ 改写泛化路（Query Rewrite 变体）
            # 路径名与 config.json → retrieval.parallel_paths 的键一致，
            # 也与 _build_rrf_weights 产出的权重 key 一致。
            rewrite_cfg = top_k_per_path.get("rewrite_variants")
            if rewrite_cfg is not None and rewrite_cfg.get("enabled", True):
                futures[
                    executor.submit(
                        self._recall_rewrite_variants,
                        query,
                        query_embedding,
                        qdrant_filter,
                        rewrite_cfg.get("top_k", 30),
                    )
                ] = "rewrite_variants"

            # 收集结果（各路独立存储，用于 RRF 融合）
            for future in as_completed(futures):
                path_name = futures[future]
                try:
                    results = future.result()
                    results = self._apply_rbac_filter(results, user_role_mask, user_dept_mask)
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
                # `is not None` 而不是 `or`：rrf_fusion 的契约是「None 或空 dict 时
                # 等权重」，因此空 dict 是一个有意义的显式取值，不能被当成「未传入」
                # 而被静态 config 权重悄悄顶替。未传参的调用方仍走 config 回退。
                weights=(rrf_weights if rrf_weights is not None else rrf_config.get("weights", None)),
            )
            all_results = fused_results
            logger.info(
                f"RRF 融合完成: {sum(len(v) for v in path_results.values())} 输入 → {len(all_results)} 条融合结果"
            )
        except Exception as e:
            logger.warning(f"RRF 融合失败，回退到简单合并: {e}")

        # 计算 Retrieval Agreement Score（PRD §7.2）
        agreement_score = self._compute_agreement_score(path_results)

        # ES Fallback — degraded supplementary recall, not a fifth path. It is
        # appended to all_results after RRF and never enters path_results, so it
        # neither participates in fusion nor counts as a recall path. Only the
        # `len(all_doc_ids) < FALLBACK_MIN_DOC_IDS` condition below actually runs
        # it; a failing Qdrant path is reported separately and never triggers it.
        all_doc_ids = {r.doc_id for r in all_results}
        qdrant_paths = {"dense_bge", "clip_visual", "rewrite_variants"}
        failed_qdrant_paths = {
            path_name
            for path_name in qdrant_paths
            if any(futures[f] == path_name for f in futures) and path_name not in path_results
        }
        if len(all_doc_ids) < 10:
            failed_qdrant_paths |= {
                path_name for path_name in qdrant_paths if path_name in path_results and not path_results[path_name]
            }
        if failed_qdrant_paths:
            # Reports which Qdrant-backed path degraded, and nothing about the
            # fallback: the fallback runs only under the `len(all_doc_ids) < 50`
            # condition below, and it may not run at all. Claiming here that it
            # was triggered would send an operator looking for a fallback log
            # line that was never written.
            logger.warning(
                f"Qdrant 路径异常，召回覆盖降级: {sorted(failed_qdrant_paths)}"
                f"（ES Fallback 是否运行，由融合后有效文档数 <{FALLBACK_MIN_DOC_IDS} 单独决定）"
            )
        if len(all_doc_ids) < FALLBACK_MIN_DOC_IDS:
            logger.info(f"召回有效文档数 {len(all_doc_ids)} < {FALLBACK_MIN_DOC_IDS}，触发 ES Fallback")
            try:
                fallback_results = self._recall_es_fallback(query, user_role_mask, user_dept_mask, top_k_per_path)
                if fallback_results:
                    all_results.extend(fallback_results)
                    logger.info(f"ES Fallback 补充召回 {len(fallback_results)} 条")
            except Exception as e:
                logger.warning(f"ES Fallback 失败: {e}")

        # Keep the authorization boundary after fallback and fusion as a final
        # guard against a retrieval channel returning an unfiltered candidate.
        return self._apply_rbac_filter(all_results, user_role_mask, user_dept_mask), agreement_score

    def _recall_dense(self, query_embedding, qdrant_filter, top_k) -> list:
        """Dense 语义召回"""
        from core.pipeline_context import RecallResult

        hits = self.dense_retriever.search(query_embedding, qdrant_filter, top_k)
        return [
            RecallResult(
                doc_id=h["doc_id"],
                content=h["content"],
                score=h["score"],
                source="dense_bge",
                metadata=h.get("metadata", {}),
            )
            for h in hits
        ]

    def _recall_bm25(self, query, user_role_mask, user_dept_mask, top_k) -> list:
        """BM25 关键词召回"""
        from core.pipeline_context import RecallResult

        hits = self.bm25_retriever.search(query, user_role_mask, user_dept_mask, top_k)
        return [
            RecallResult(
                doc_id=h["doc_id"],
                content=h["content"],
                score=h["score"],
                source="bm25_es",
                metadata=h.get("metadata", {}),
            )
            for h in hits
        ]

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
            return [
                RecallResult(
                    doc_id=h["doc_id"],
                    content=h.get("content", ""),
                    score=h["score"],
                    source="clip_visual",
                    metadata={
                        "image_uri": h.get("image_uri", ""),
                        **(h.get("metadata") or {}),
                    },
                )
                for h in hits
            ]
        except (FuturesTimeout, TimeoutError) as e:
            logger.warning(f"CLIP 检索超时 ({clip_timeout_s * 1000:.0f}ms)，丢弃 CLIP 分支: {e}")
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
            results.extend(
                [
                    RecallResult(
                        doc_id=h["doc_id"],
                        content=h["content"],
                        score=h["score"],
                        source="rewrite_variants",
                        metadata={"variant_query": variant, **(h.get("metadata") or {})},
                    )
                    for h in hits
                ]
            )
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
        return [
            RecallResult(
                doc_id=h["doc_id"],
                content=h["content"],
                score=h["score"],
                source="bm25_fallback",
                metadata=h.get("metadata", {}),
            )
            for h in hits
        ]

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
        """
        from core.pipeline_context import RecallResult

        clip_cfg = config.get("clip_async", {})
        if not clip_cfg.get("enabled", False):
            return []

        # 多轮预热：检查 session 中是否有可复用的异步结果
        if session and clip_cfg.get("preheat_on_multiturn", False):
            cached = session.async_clip_results
            if cached:
                logger.info(f"CLIP 异步预热命中: {len(cached)} 条")
                return self._apply_rbac_filter(cached, user_role_mask, user_dept_mask)

        top_k = top_k or clip_cfg.get("top_k", 100)

        try:
            from auth.bitmask_rbac import build_qdrant_image_filter
            from models.embedding_service import EmbeddingService

            embedding_svc = EmbeddingService()
            clip_embedding = embedding_svc.encode_text_clip(query)
            if qdrant_filter is None:
                active_epoch = config.get("knowledge_version_epoch", "default")
                qdrant_filter = build_qdrant_image_filter(user_role_mask, user_dept_mask, active_epoch)
            hits = self.clip_retriever.search(clip_embedding, qdrant_filter, top_k)

            results = [
                RecallResult(
                    doc_id=h["doc_id"],
                    content=h.get("content", ""),
                    score=h["score"],
                    source="clip_async",
                    metadata={
                        "image_uri": h.get("image_uri", ""),
                        **(h.get("metadata") or {}),
                    },
                )
                for h in hits
            ]

            # 存入 session 供后续轮次复用
            if session:
                session.store_async_clip_result(results)

            return self._apply_rbac_filter(results, user_role_mask, user_dept_mask)

        except Exception as e:
            logger.warning(f"CLIP 异步补召回失败: {e}")
            return []
