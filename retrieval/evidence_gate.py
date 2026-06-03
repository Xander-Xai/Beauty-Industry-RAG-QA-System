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

import json
import logging
from typing import Optional

with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

logger = logging.getLogger(__name__)


class EvidenceEnsembleGate:
    """
    Evidence Ensemble Gate - 多维度证据投票

    权重 w1-w4 通过离线日志学习得到，
    优化目标为最大化人工标注的"回答可用性"与"点击率"的相关性，
    每周更新一次。
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
    ) -> "EvidenceGateResult":
        """
        评估证据综合置信度

        Args:
            query: 查询文本
            rerank_results: CrossEncoder 输出的 RerankResult 列表
            retrieval_agreement_score: 检索一致性评分（readme 7.2）

        Returns:
            EvidenceGateResult 含决策结果
        """
        from core.pipeline_context import EvidenceGateResult, RerankResult

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

        # ④ Doc_Consistency_Score（NLI 交叉校验 Top-3 文档间逻辑一致性）
        doc_consistency_score = self._compute_doc_consistency(query, rerank_results[:3])

        # 综合 Evidence Score
        evidence_score = (
            self.weights["w1"] * ce_top1_score +
            self.weights["w2"] * ce_top3_mean_score +
            self.weights["w3"] * retrieval_agreement_score +
            self.weights["w4"] * doc_consistency_score
        )

        # 决策
        if evidence_score >= self.thresholds["high_confidence"]:
            decision = "pass"
        elif evidence_score >= self.thresholds["low_confidence"]:
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
        文档间逻辑矛盾检测（NLI 交叉校验，GPU Batch）

        对 Top-3 候选文档进行批量蕴含关系判断：
        - 对每对文档做 NLI（前提=doc_i内容, 假设=doc_j内容）
        - 一致文档对占比越高，一致性分数越高
        """
        if len(top_docs) < 2:
            return 1.0

        try:
            # 通过 AnswerGate 的 NLI 模型进行批量推理
            # 检查每对 Top-3 文档间是否互相蕴含（而非矛盾）
            doc_texts = [doc.content[:512] for doc in top_docs]

            contradiction_count = 0
            total_pairs = 0

            for i in range(len(doc_texts)):
                for j in range(i + 1, len(doc_texts)):
                    # NLI: premise=doc_i, hypothesis=doc_j
                    contradiction, entailment = self.answer_gate._nli_inference(
                        doc_texts[i], doc_texts[j]
                    )
                    if contradiction > 0.5:
                        contradiction_count += 1
                    total_pairs += 1

            if total_pairs == 0:
                return 1.0

            # 一致性 = 1 - 矛盾文档对比例
            consistency = 1.0 - (contradiction_count / total_pairs)
            return consistency

        except Exception as e:
            logger.warning(f"NLI 文档一致性检测失败，使用默认值: {e}")
            return 0.8
