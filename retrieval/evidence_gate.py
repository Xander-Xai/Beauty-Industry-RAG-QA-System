"""
Evidence Ensemble Gate（readme 7.4 节）

从单点判决升级为多维度证据投票机制：

综合置信度计算公式：
Evidence Score =
  w1 * CE_Top1_Score +              # CrossEncoder 最强匹配
  w2 * CE_Top3_Mean_Score +         # 冗余文档平均得分
  w3 * Retrieval_Agreement_Score +  # 检索一致性评分
  w4 * Doc_Consistency_Score        # NLI 交叉校验

决策规则：
- ≥ 0.75 → 放行（高置信，直接进入 LLM 生成）
- 0.55-0.75 → 多证据增强生成（强制注入 Top3 文档摘要）
- < 0.55 → 拒答（返回引导性拒答，HTTP 200）
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from common.config import get_config_dict

if TYPE_CHECKING:
    from common.models import EvidenceGateResult

config = get_config_dict()

logger = logging.getLogger(__name__)


class EvidenceEnsembleGate:
    """
    Evidence Ensemble Gate - 多维度证据投票

    权重 w1-w4 当前从配置读取，也支持 A/B 覆盖。仓库包含离线反馈
    调参能力，但没有有效生产反馈数据时不能声称权重已经自动学习。
    """

    def __init__(self):
        self.weights = config["retrieval"]["evidence_gate"]["weights"]
        self.thresholds = config["retrieval"]["evidence_gate"]["thresholds"]
        self._answer_gate = None
        logger.info("EvidenceEnsembleGate 初始化完成")

    @property
    def answer_gate(self):
        """复用 AnswerGate 的 NLI 模型实例"""
        if self._answer_gate is None:
            from retrieval.answer_gate import AnswerGate
            self._answer_gate = AnswerGate()
        return self._answer_gate

    def evaluate(
        self,
        query: str,
        rerank_results: list,
        retrieval_agreement_score: float = 0.0,
        weights_override: dict = None,
        thresholds_override: dict = None,
        conservative_mode: bool = False,
    ) -> EvidenceGateResult:
        """
        评估证据综合置信度

        Args:
            query: 查询文本
            rerank_results: CrossEncoder 输出的 RerankResult 列表
            retrieval_agreement_score: 检索一致性评分（readme 7.2）
            weights_override: A/B 实验覆盖权重（PRD §12.2）
            thresholds_override: A/B 实验覆盖阈值（PRD §12.2）
            conservative_mode: 保守模式（PRD §4.4 Rewrite 降级时阈值提升至 ≥0.8）

        Returns:
            EvidenceGateResult 含决策结果
        """
        from core.pipeline_context import EvidenceGateResult

        if not rerank_results:
            return EvidenceGateResult(
                evidence_score=0.0,
                ce_top1_score=0.0,
                ce_top3_mean_score=0.0,
                retrieval_agreement_score=0.0,
                doc_consistency_score=0.0,
                decision="reject",
                top_docs=[],
            )

        # ① CE_Top1_Score
        ce_top1_score = rerank_results[0].ce_score_ensemble

        # ② CE_Top3_Mean_Score
        top3_scores = [r.ce_score_ensemble for r in rerank_results[:3]]
        ce_top3_mean_score = sum(top3_scores) / len(top3_scores) if top3_scores else 0.0

        # ③ Retrieval_Agreement_Score（来自 readme 7.2，外部计算传入）

        # ④ Doc_Consistency_Score（NLI 批量推理 Top-3 文档间逻辑一致性，GPU Batch）
        doc_consistency_score = self._compute_doc_consistency(query, rerank_results[:3])

        # 应用 A/B 实验覆盖权重（PRD §12.2）
        w = {**self.weights}
        if weights_override:
            w.update(weights_override)

        t = {**self.thresholds}
        if thresholds_override:
            t.update(thresholds_override)

        # PRD §4.4 保守模式：Rewrite 降级时强制提升 Evidence Gate 阈值至 ≥0.8
        if conservative_mode:
            t["high_confidence"] = max(t.get("high_confidence", 0.75), 0.8)
            t["low_confidence"] = max(t.get("low_confidence", 0.55), 0.65)
            logger.info(f"Evidence Gate 保守模式: high={t['high_confidence']}, low={t['low_confidence']}")

        # PRD §7.2: 检索一致性过低时动态提高阈值（保守策略）
        if retrieval_agreement_score < 0.3:
            t["high_confidence"] = max(t.get("high_confidence", 0.75), 0.8)
            t["low_confidence"] = max(t.get("low_confidence", 0.55), 0.65)
            logger.info(
                f"Evidence Gate 低一致性阈值调整: agreement={retrieval_agreement_score:.3f}, "
                f"high={t['high_confidence']}, low={t['low_confidence']}"
            )

        # 综合 Evidence Score
        evidence_score = (
            w.get("w1", 0) * ce_top1_score +
            w.get("w2", 0) * ce_top3_mean_score +
            w.get("w3", 0) * retrieval_agreement_score +
            w.get("w4", 0) * doc_consistency_score
        )

        # 决策
        if evidence_score >= t.get("high_confidence", 0.75):
            decision = "pass"
        elif evidence_score >= t.get("low_confidence", 0.55):
            decision = "enhanced_generate"
        else:
            decision = "reject"

        result = EvidenceGateResult(
            evidence_score=evidence_score,
            ce_top1_score=ce_top1_score,
            ce_top3_mean_score=ce_top3_mean_score,
            retrieval_agreement_score=retrieval_agreement_score,
            doc_consistency_score=doc_consistency_score,
            decision=decision,
            top_docs=rerank_results[:3],
        )

        logger.info(
            f"Evidence Gate: score={evidence_score:.3f}, decision={decision} | "
            f"CE_Top1={ce_top1_score:.3f}, CE_Top3_Mean={ce_top3_mean_score:.3f}, "
            f"Agreement={retrieval_agreement_score:.3f}, Consistency={doc_consistency_score:.3f}"
        )
        return result

    def _compute_doc_consistency(self, query: str, top_docs: list) -> float:
        """
        文档间逻辑矛盾检测（NLI 交叉校验，GPU Batch — PRD §7.4）

        对 Top-3 候选文档进行批量蕴含关系判断：
        - 收集所有文档对 (premise, hypothesis)
        - 调用 AnswerGate 的批量 NLI 推理接口一次性推理
        - 一致文档对占比越高，一致性分数越高
        """
        if len(top_docs) < 2:
            return 1.0

        try:
            doc_texts = [doc.content[:512] for doc in top_docs]

            # 收集所有文档对
            pairs = []
            for i in range(len(doc_texts)):
                for j in range(i + 1, len(doc_texts)):
                    pairs.append((doc_texts[i], doc_texts[j]))

            if not pairs:
                return 1.0

            # 使用批量 NLI 推理（GPU Batch，PRD §7.4 要求 ≤25ms）
            try:
                batch_results = self.answer_gate._batch_nli_inference(
                    [(p[0], p[1]) for p in pairs],
                )
                contradiction_count = sum(
                    1 for (contradiction, _entailment) in batch_results
                    if contradiction > 0.5
                )
            except (AttributeError, Exception):
                # 降级为逐条推理
                contradiction_count = 0
                for premise, hypothesis in pairs:
                    contradiction, _entailment = self.answer_gate._nli_inference(premise, hypothesis)
                    if contradiction > 0.5:
                        contradiction_count += 1

            total_pairs = len(pairs)
            # 一致性 = 1 - 矛盾文档对比例
            consistency = 1.0 - (contradiction_count / total_pairs)
            return consistency

        except Exception as e:
            logger.warning(f"NLI 文档一致性检测失败，使用默认值: {e}")
            return 0.8
