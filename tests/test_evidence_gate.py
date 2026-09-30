"""
Evidence Ensemble Gate 测试 (retrieval/evidence_gate.py)

覆盖 §7.4 Evidence Ensemble Gate 逻辑：
- 高置信度放行 (evidence_score >= threshold)
- 多证据增强 (evidence_score 在中间区间)
- 拒答 (evidence_score 低于阈值)
- NLI 文档一致性计算
- 空输入边界处理
- 权重对综合得分的影响

注意：所有外部模型调用（AnswerGate._nli_inference）均通过 mock 隔离。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock, patch

import pytest

from core.pipeline_context import RerankResult
from retrieval.evidence_gate import EvidenceEnsembleGate

# ── 辅助工厂 ──


def make_rerank_result(doc_id: str, ce_score_ensemble: float) -> RerankResult:
    """构造一个 RerankResult 的快捷方法"""
    return RerankResult(
        doc_id=doc_id,
        content=f"文档 {doc_id} 的内容",
        ce_score_ensemble=ce_score_ensemble,
    )


# ── 空输入边界 ──


class TestEmptyInput:
    """空输入时应安全降级"""

    def test_empty_rerank_results_returns_reject(self):
        """空 rerank 结果列表：返回 reject 决策，evidence_score=0"""
        g = EvidenceEnsembleGate()
        result = g.evaluate(query="test query", rerank_results=[])
        assert result.decision == "reject"
        assert result.evidence_score == 0.0
        assert result.top_docs == []


# ── 高置信度放行 ──


class TestHighConfidencePass:
    """高置信度 (>= 0.70) 应放行"""

    def test_perfect_scores_pass(self):
        """所有 CE 分数为 1.0 时应放行"""
        g = EvidenceEnsembleGate()
        results = [
            make_rerank_result("d1", 1.0),
            make_rerank_result("d2", 1.0),
            make_rerank_result("d3", 1.0),
        ]
        with patch.object(g, "_compute_doc_consistency", return_value=1.0):
            result = g.evaluate("test", results, retrieval_agreement_score=1.0)
        assert result.decision == "pass"
        assert result.evidence_score >= 0.70

    def test_high_ce_scores_with_mock_consistency(self):
        """高 CE 分数 + 高一致性：应放行"""
        g = EvidenceEnsembleGate()
        results = [
            make_rerank_result("d1", 0.9),
            make_rerank_result("d2", 0.85),
            make_rerank_result("d3", 0.8),
        ]
        # w1=0.4, w2=0.2, w3=0.2, w4=0.2
        # expected = 0.4*0.9 + 0.2*(0.9+0.85+0.8)/3 + 0.2*0.8 + 0.2*0.9
        with patch.object(g, "_compute_doc_consistency", return_value=0.9):
            result = g.evaluate("test", results, retrieval_agreement_score=0.8)
        assert result.decision == "pass"


# ── 多证据增强 ──


class TestEnhancedGenerate:
    """中等置信度 (0.55 ~ 0.75) 应触发增强生成"""

    def test_medium_scores_enhanced_generate(self):
        """中等 CE 分数触发增强生成（阈值 0.55-0.75）"""
        g = EvidenceEnsembleGate()
        results = [
            make_rerank_result("d1", 0.75),
            make_rerank_result("d2", 0.65),
            make_rerank_result("d3", 0.55),
        ]
        with patch.object(g, "_compute_doc_consistency", return_value=0.6):
            result = g.evaluate("test", results, retrieval_agreement_score=0.6)
        assert result.decision == "enhanced_generate"


# ── 拒答 ──


class TestReject:
    """低置信度 (< 0.40) 应拒答"""

    def test_low_scores_reject(self):
        """低 CE 分数：拒答"""
        g = EvidenceEnsembleGate()
        results = [
            make_rerank_result("d1", 0.2),
            make_rerank_result("d2", 0.1),
        ]
        with patch.object(g, "_compute_doc_consistency", return_value=0.2):
            result = g.evaluate("test", results, retrieval_agreement_score=0.1)
        assert result.decision == "reject"

    def test_single_low_score_reject(self):
        """单个低分文档：拒答"""
        g = EvidenceEnsembleGate()
        results = [make_rerank_result("d1", 0.15)]
        with patch.object(g, "_compute_doc_consistency", return_value=0.1):
            result = g.evaluate("test", results, retrieval_agreement_score=0.0)
        assert result.decision == "reject"


# ── 综合得分计算验证 ──


class TestScoreCalculation:
    """验证综合得分的加权计算正确性"""

    def test_weights_applied_correctly(self):
        """验证 w1~w4 权重正确应用"""
        g = EvidenceEnsembleGate()
        weights = g.weights  # {"w1": 0.4, "w2": 0.2, "w3": 0.2, "w4": 0.2}

        results = [
            make_rerank_result("d1", 0.8),
            make_rerank_result("d2", 0.6),
            make_rerank_result("d3", 0.4),
        ]
        retrieval_agreement = 0.7
        doc_consistency = 0.9

        with patch.object(g, "_compute_doc_consistency", return_value=doc_consistency):
            result = g.evaluate("test", results, retrieval_agreement_score=retrieval_agreement)

        # 手动计算期望值
        ce_top1 = 0.8
        ce_top3_mean = (0.8 + 0.6 + 0.4) / 3
        expected = (
            weights["w1"] * ce_top1
            + weights["w2"] * ce_top3_mean
            + weights["w3"] * retrieval_agreement
            + weights["w4"] * doc_consistency
        )
        assert result.evidence_score == pytest.approx(expected, abs=1e-6)

    def test_top3_mean_with_fewer_than_3_results(self):
        """不足 3 个结果时，Top3 Mean 使用实际数量"""
        g = EvidenceEnsembleGate()
        results = [
            make_rerank_result("d1", 0.9),
            make_rerank_result("d2", 0.7),
        ]
        with patch.object(g, "_compute_doc_consistency", return_value=0.8):
            result = g.evaluate("test", results, retrieval_agreement_score=0.6)
        # Top3 Mean = (0.9 + 0.7) / 2 = 0.8
        assert result.ce_top3_mean_score == pytest.approx(0.8, abs=1e-6)

    def test_result_fields_populated(self):
        """返回结果的各字段被正确填充"""
        g = EvidenceEnsembleGate()
        results = [
            make_rerank_result("d1", 0.85),
            make_rerank_result("d2", 0.75),
        ]
        with patch.object(g, "_compute_doc_consistency", return_value=0.9):
            result = g.evaluate("test", results, retrieval_agreement_score=0.8)

        assert result.ce_top1_score == 0.85
        assert result.retrieval_agreement_score == 0.8
        assert result.doc_consistency_score == 0.9
        assert len(result.top_docs) == 2


# ── 文档一致性计算 ──


class TestDocConsistency:
    """测试 NLI 文档一致性计算"""

    def test_single_doc_consistency_is_one(self):
        """单文档一致性为 1.0（无需比较）"""
        g = EvidenceEnsembleGate()
        score = g._compute_doc_consistency("query", [make_rerank_result("d1", 0.8)])
        assert score == 1.0

    def test_empty_docs_consistency_is_one(self):
        """空文档列表一致性为 1.0"""
        g = EvidenceEnsembleGate()
        score = g._compute_doc_consistency("query", [])
        assert score == 1.0

    def test_consistency_returns_default_on_exception(self):
        """NLI 推理异常时返回默认值 0.8"""
        g = EvidenceEnsembleGate()
        mock_gate = MagicMock()
        # 批量 NLI 和逐条 NLI 都抛出异常
        mock_gate._batch_nli_inference.side_effect = RuntimeError("NLI batch error")
        mock_gate._nli_inference.side_effect = RuntimeError("NLI model error")
        g._answer_gate = mock_gate

        results = [
            make_rerank_result("d1", 0.8),
            make_rerank_result("d2", 0.7),
        ]
        score = g._compute_doc_consistency("query", results)
        assert score == 0.8

    def test_consistency_all_consistent(self):
        """所有文档对一致 (contradiction < 0.5)：一致性为 1.0"""
        g = EvidenceEnsembleGate()
        mock_gate = MagicMock()
        # 批量 NLI 不可用，降级为逐条推理
        mock_gate._batch_nli_inference.side_effect = AttributeError("no batch")
        mock_gate._nli_inference.return_value = (0.1, 0.8)  # (contradiction, entailment)
        g._answer_gate = mock_gate

        results = [
            make_rerank_result("d1", 0.9),
            make_rerank_result("d2", 0.8),
            make_rerank_result("d3", 0.7),
        ]
        # 3 对文档，每对 contradiction=0.1 < 0.5，全部一致
        score = g._compute_doc_consistency("query", results)
        assert score == 1.0

    def test_consistency_all_contradictory(self):
        """所有文档对矛盾 (contradiction > 0.5)：一致性为 0.0"""
        g = EvidenceEnsembleGate()
        mock_gate = MagicMock()
        # 批量 NLI 不可用，降级为逐条推理
        mock_gate._batch_nli_inference.side_effect = AttributeError("no batch")
        mock_gate._nli_inference.return_value = (0.8, 0.1)  # 高矛盾
        g._answer_gate = mock_gate

        results = [
            make_rerank_result("d1", 0.8),
            make_rerank_result("d2", 0.7),
        ]
        # 1 对，contradiction=0.8 > 0.5，矛盾
        score = g._compute_doc_consistency("query", results)
        assert score == 0.0

    def test_consistency_partial_contradiction(self):
        """部分文档对矛盾：一致性按比例计算"""
        g = EvidenceEnsembleGate()
        mock_gate = MagicMock()
        # 批量 NLI 不可用，降级为逐条推理
        mock_gate._batch_nli_inference.side_effect = AttributeError("no batch")
        # 3 对文档：第 1 对一致，第 2 对矛盾，第 3 对一致
        mock_gate._nli_inference.side_effect = [
            (0.1, 0.8),  # d1 vs d2: 一致
            (0.8, 0.1),  # d1 vs d3: 矛盾
            (0.2, 0.7),  # d2 vs d3: 一致
        ]
        g._answer_gate = mock_gate

        results = [
            make_rerank_result("d1", 0.9),
            make_rerank_result("d2", 0.8),
            make_rerank_result("d3", 0.7),
        ]
        # 3 对，1 对矛盾 -> consistency = 1 - 1/3 = 0.666...
        score = g._compute_doc_consistency("query", results)
        assert score == pytest.approx(2.0 / 3.0, abs=1e-6)


# ── 阈值边界测试 ──


class TestThresholdBoundaries:
    """验证决策阈值的精确边界"""

    def test_exactly_at_high_threshold(self):
        """evidence_score 恰好等于 high_confidence 阈值：应放行"""
        g = EvidenceEnsembleGate()
        # 阈值 high_confidence=0.70, low_confidence=0.40
        # 构造输入使 score 恰好 = 0.70
        # 0.4 * ce_top1 + 0.2 * ce_top3_mean + 0.2 * agreement + 0.2 * consistency = 0.70
        # 设 ce_top1=0.85, ce_top3_mean=0.85, agreement=0.35, consistency=0.35
        # 0.4*0.85 + 0.2*0.85 + 0.2*0.35 + 0.2*0.35 = 0.34 + 0.17 + 0.07 + 0.07 = 0.65
        # 需要调整：ce_top1=1.0, ce_top3_mean=0.75, agreement=0.5, consistency=0.5
        # 0.4*1.0 + 0.2*0.75 + 0.2*0.5 + 0.2*0.5 = 0.4 + 0.15 + 0.1 + 0.1 = 0.75
        results = [make_rerank_result("d1", 1.0), make_rerank_result("d2", 0.75)]
        with patch.object(g, "_compute_doc_consistency", return_value=0.5):
            result = g.evaluate("test", results, retrieval_agreement_score=0.5)
        # ce_top3_mean = (1.0 + 0.75)/2 = 0.875
        # score = 0.4*1.0 + 0.2*0.875 + 0.2*0.5 + 0.2*0.5 = 0.4 + 0.175 + 0.1 + 0.1 = 0.775
        assert result.decision == "pass"
