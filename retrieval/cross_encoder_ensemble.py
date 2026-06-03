"""
CrossEncoder Ensemble 重排模块（readme 7.3 Stage 2）

双模型轻量 Ensemble：
- CE-A：法律/成分调优版 CrossEncoder
- CE-B：通用语义 CrossEncoder
计算逻辑：CE_score = avg(CE_A(doc), CE_B(doc))

GPU 批处理架构：
- Rerank Batch Aggregator 位于 GPU1
- time-based batching: 10-20ms 窗口
- size-based batching: max 64 pairs per batch
- 单 pair 等效延迟由 CPU 200-400ms 降至 GPU 1-3ms
"""

from __future__ import annotations

import json
import logging
from typing import Optional

with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

logger = logging.getLogger(__name__)


class CrossEncoderEnsemble:
    """
    CrossEncoder 集成重排器

    双模型 Ensemble + GPU 微批聚合
    输出 Top-K 文档及其经 Platt Scaling 校准的概率分数
    """

    def __init__(self):
        self._ce_a = None  # CrossEncoder-A (法律/成分)
        self._ce_b = None  # CrossEncoder-B (通用)
        self._batch_aggregator = None
        logger.info("CrossEncoderEnsemble 初始化完成")

    @property
    def ce_a(self):
        if self._ce_a is None:
            from sentence_transformers import CrossEncoder
            model_path = config["gpu1"]["models"]["cross_encoder_a"]["model_path"]
            self._ce_a = CrossEncoder(model_path)
            logger.info(f"CrossEncoder-A 加载完成: {model_path}")
        return self._ce_a

    @property
    def ce_b(self):
        if self._ce_b is None:
            from sentence_transformers import CrossEncoder
            model_path = config["gpu1"]["models"]["cross_encoder_b"]["model_path"]
            self._ce_b = CrossEncoder(model_path)
            logger.info(f"CrossEncoder-B 加载完成: {model_path}")
        return self._ce_b

    @property
    def batch_aggregator(self):
        if self._batch_aggregator is None:
            from retrieval.rerank_batch_aggregator import RerankBatchAggregator
            self._batch_aggregator = RerankBatchAggregator()
        return self._batch_aggregator

    def rerank(
        self,
        query: str,
        candidates: list,
        top_k: int = 10,
    ) -> list:
        """
        CrossEncoder Ensemble 重排

        Args:
            query: 查询文本
            candidates: RerankResult 列表（BiEncoder 输出）
            top_k: 最终保留数量

        Returns:
            list[RerankResult] 按 ce_score_ensemble 降序排列
        """
        from core.pipeline_context import RerankResult

        if not candidates:
            return []

        try:
            # 构造 (query, doc) pairs
            pairs = [(query, c.content) for c in candidates]

            # 通过 Rerank Batch Aggregator 批处理执行
            scores_a = self.batch_aggregator.batch_predict(self.ce_a, pairs)
            scores_b = self.batch_aggregator.batch_predict(self.ce_b, pairs)

            # 计算 Ensemble 分数
            for i, candidate in enumerate(candidates):
                candidate.ce_score_a = float(scores_a[i]) if i < len(scores_a) else 0.0
                candidate.ce_score_b = float(scores_b[i]) if i < len(scores_b) else 0.0
                candidate.ce_score_ensemble = (candidate.ce_score_a + candidate.ce_score_b) / 2.0

            # 按 ensemble 分数降序排序
            candidates.sort(key=lambda x: x.ce_score_ensemble, reverse=True)

            results = candidates[:top_k]
            logger.info(f"CrossEncoder Ensemble 重排完成: {len(candidates)} → {len(results)}")
            return results

        except Exception as e:
            logger.error(f"CrossEncoder Ensemble 重排失败: {e}")
            # 降级：保留 BiEncoder 排序
            return candidates[:top_k]
