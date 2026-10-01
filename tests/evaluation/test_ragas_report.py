"""
RAGAS 评估报告生成器单元测试
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from tests.evaluation.ragas_eval import RAGASEvaluator
from tests.evaluation.ragas_report import (
    METRIC_LABELS,
    ComparisonResult,
    EvaluationReport,
    RAGASReporter,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_evaluator() -> RAGASEvaluator:
    """创建一个带有样本数据的评估器（RAGAS 未安装路径）。"""
    import tempfile

    entries = [
        {
            "question": "化妆品的保质期是多久？",
            "answer": "一般未开封 3 年。",
            "contexts": ["化妆品保质期通常标注在包装上。"],
            "ground_truth": "未开封化妆品保质期为 3 年。",
            "business_type": "regulation",
            "difficulty": "easy",
        },
        {
            "question": "什么是敏感肌？",
            "answer": "皮肤对外界刺激反应过度的状态。",
            "contexts": ["敏感肌不是皮肤类型，而是皮肤状态。"],
            "ground_truth": "敏感肌是皮肤对外界刺激反应过度的状态。",
            "business_type": "ingredient",
            "difficulty": "medium",
        },
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        tmppath = f.name
    try:
        yield RAGASEvaluator(tmppath)
    finally:
        os.unlink(tmppath)


@pytest.fixture
def sample_report() -> EvaluationReport:
    return EvaluationReport(
        tag="baseline",
        timestamp="2026-06-26T12:00:00",
        overall_scores={
            "faithfulness": 0.85,
            "answer_relevancy": 0.92,
            "context_precision": 0.78,
            "context_recall": 0.88,
        },
        per_sample_scores=[
            {
                "question": "Q1",
                "faithfulness": 0.90,
                "answer_relevancy": 0.95,
                "context_precision": 0.80,
                "context_recall": 0.90,
                "business_type": "regulation",
                "difficulty": "easy",
            },
            {
                "question": "Q2",
                "faithfulness": 0.80,
                "answer_relevancy": 0.89,
                "context_precision": 0.76,
                "context_recall": 0.86,
                "business_type": "ingredient",
                "difficulty": "medium",
            },
        ],
        category_breakdown={
            "business_type:regulation": {
                "faithfulness": 0.90,
                "answer_relevancy": 0.95,
                "context_precision": 0.80,
                "context_recall": 0.90,
            },
            "business_type:ingredient": {
                "faithfulness": 0.80,
                "answer_relevancy": 0.89,
                "context_precision": 0.76,
                "context_recall": 0.86,
            },
            "difficulty:easy": {
                "faithfulness": 0.90,
                "answer_relevancy": 0.95,
                "context_precision": 0.80,
                "context_recall": 0.90,
            },
            "difficulty:medium": {
                "faithfulness": 0.80,
                "answer_relevancy": 0.89,
                "context_precision": 0.76,
                "context_recall": 0.86,
            },
        },
        dataset_size=2,
        metadata={"deployment_mode": "testing"},
    )


# ---------------------------------------------------------------------------
# EvaluationReport dataclass
# ---------------------------------------------------------------------------


class TestEvaluationReport:
    def test_create_report(self) -> None:
        """应能创建 EvaluationReport 并正确访问字段。"""
        report = EvaluationReport(
            tag="test",
            timestamp="2026-01-01T00:00:00",
            overall_scores={"faithfulness": 0.85},
            per_sample_scores=[],
            category_breakdown={},
            dataset_size=1,
            metadata={},
        )
        assert report.tag == "test"
        assert report.overall_scores["faithfulness"] == 0.85
        assert report.dataset_size == 1

    def test_report_defaults(self) -> None:
        """字段默认值应正确。"""
        report = EvaluationReport(
            tag="x",
            timestamp="t",
            overall_scores={},
            per_sample_scores=[],
            category_breakdown={},
            dataset_size=0,
            metadata={},
        )
        assert report.tag == "x"


# ---------------------------------------------------------------------------
# ComparisonResult
# ---------------------------------------------------------------------------


class TestComparisonResult:
    def test_improved_verdict(self) -> None:
        """所有指标提升时应判定为 improved。"""
        result = ComparisonResult(
            baseline_tag="baseline",
            comparison_tag="v2",
            overall_deltas={"faithfulness": 0.05, "answer_relevancy": 0.03},
            category_deltas={},
            verdict="improved",
            significant_changes=["faithfulness: +0.05 ↑"],
        )
        assert result.verdict == "improved"
        assert len(result.significant_changes) == 1

    def test_regressed_verdict(self) -> None:
        """所有指标下降时应判定为 regressed。"""
        result = ComparisonResult(
            baseline_tag="baseline",
            comparison_tag="v2",
            overall_deltas={"faithfulness": -0.05, "answer_relevancy": -0.03},
            category_deltas={},
            verdict="regressed",
            significant_changes=["faithfulness: -0.05 ↓"],
        )
        assert result.verdict == "regressed"

    def test_mixed_verdict(self) -> None:
        """有升有降时应判定为 mixed。"""
        result = ComparisonResult(
            baseline_tag="baseline",
            comparison_tag="v2",
            overall_deltas={"faithfulness": 0.05, "answer_relevancy": -0.03},
            category_deltas={},
            verdict="mixed",
            significant_changes=[],
        )
        assert result.verdict == "mixed"


# ---------------------------------------------------------------------------
# RAGASReporter
# ---------------------------------------------------------------------------


class TestRAGASReporterInit:
    def test_init_with_evaluator(self, sample_evaluator: RAGASEvaluator) -> None:
        """使用有效评估器初始化不应报错。"""
        reporter = RAGASReporter(sample_evaluator)
        assert reporter is not None


class TestRunAndReport:
    def test_run_and_report_unavailable_marks_status(self, sample_evaluator: RAGASEvaluator, monkeypatch) -> None:
        """RAGAS 不可用时报 UNAVAILABLE，且不把零分当作质量分。"""
        import sys

        monkeypatch.setitem(sys.modules, "ragas", None)
        monkeypatch.setitem(sys.modules, "datasets", None)
        reporter = RAGASReporter(sample_evaluator)
        report = reporter.run_and_report(tag="test-basic")
        assert isinstance(report, EvaluationReport)
        assert report.tag == "test-basic"
        assert report.evaluator_status == "unavailable"
        assert report.overall_scores == {}
        assert report.unavailable_reason

    def test_run_and_report_with_answers(self, sample_evaluator: RAGASEvaluator) -> None:
        """传入自定义答案应正确传递到评估器。"""
        reporter = RAGASReporter(sample_evaluator)
        report = reporter.run_and_report(
            tag="test-answers",
            answers=["自定义回答1", "自定义回答2"],
        )
        assert report.dataset_size == 2

    def test_run_and_report_metrics_subset(self, sample_evaluator: RAGASEvaluator, fake_ragas) -> None:
        """指定指标子集时应只返回选中指标。"""
        reporter = RAGASReporter(sample_evaluator)
        report = reporter.run_and_report(
            tag="test-metrics",
            metrics=["faithfulness", "answer_relevancy"],
        )
        assert "faithfulness" in report.overall_scores
        assert "answer_relevancy" in report.overall_scores
        assert "context_precision" not in report.overall_scores
        assert "context_recall" not in report.overall_scores

    def test_single_evaluation_per_run(self, sample_evaluator: RAGASEvaluator, fake_ragas) -> None:
        """run_and_report 只调用一次 evaluator。"""
        reporter = RAGASReporter(sample_evaluator)
        reporter.run_and_report(tag="once")
        assert fake_ragas["calls"] == 1
        assert sample_evaluator.evaluate_calls == 1

    def test_build_report_does_not_reevaluate(self, sample_evaluator: RAGASEvaluator, fake_ragas) -> None:
        """build_report 复用已完成的评估，不重复调用 evaluator。"""
        sample_evaluator.evaluate()
        assert fake_ragas["calls"] == 1
        reporter = RAGASReporter(sample_evaluator)
        reporter.build_report(tag="reuse")
        assert fake_ragas["calls"] == 1
        assert sample_evaluator.evaluate_calls == 1

    def test_report_provenance_fields(self, sample_evaluator: RAGASEvaluator, fake_ragas) -> None:
        """报告必须带有可追踪的来源信息与样本计数。"""
        reporter = RAGASReporter(sample_evaluator)
        report = reporter.run_and_report(tag="prov")
        assert report.dataset.endswith(".jsonl")
        assert len(report.dataset_sha256) == 16
        assert report.requested_samples == 2
        assert report.successful_samples == 2
        assert report.failed_samples == 0
        assert report.metrics
        assert report.sample_ids
        assert report.evaluator_provider
        assert report.evaluator_model
        assert report.evaluator_status == "available"
        assert report.environment.get("python")


class TestCategoryBreakdown:
    def test_category_breakdown_empty(self) -> None:
        """无逐条数据时类别拆分为空。"""
        reporter = RAGASReporter.__new__(RAGASReporter)
        breakdown = reporter._compute_category_breakdown([])
        assert breakdown == {}

    def test_category_breakdown_business_type(self, sample_evaluator: RAGASEvaluator) -> None:
        """类别拆分应按 business_type 正确分组。"""
        reporter = RAGASReporter(sample_evaluator)
        report = reporter.run_and_report(tag="test-cat")
        if report.per_sample_scores:
            break_by_bt = {k: v for k, v in report.category_breakdown.items() if k.startswith("business_type:")}
            assert len(break_by_bt) > 0
            for scores in break_by_bt.values():
                assert isinstance(scores, dict)
                for v in scores.values():
                    assert isinstance(v, (int, float))

    def test_category_breakdown_difficulty(self, sample_evaluator: RAGASEvaluator) -> None:
        """类别拆分应按 difficulty 正确分组。"""
        reporter = RAGASReporter(sample_evaluator)
        report = reporter.run_and_report(tag="test-diff")
        if report.per_sample_scores:
            break_by_diff = {k: v for k, v in report.category_breakdown.items() if k.startswith("difficulty:")}
            assert len(break_by_diff) > 0


class TestSaveLoadReport:
    def test_save_report(self, sample_report: EvaluationReport) -> None:
        """保存报告应生成 JSON 文件。"""
        reporter = RAGASReporter.__new__(RAGASReporter)
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = reporter.save_report(sample_report, tmpdir)
            assert os.path.exists(filepath)
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
            assert data["tag"] == "baseline"
            assert data["dataset_size"] == 2

    def test_save_and_load_roundtrip(self, sample_report: EvaluationReport) -> None:
        """保存后再加载应得到相同内容。"""
        reporter = RAGASReporter.__new__(RAGASReporter)
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = reporter.save_report(sample_report, tmpdir)
            loaded = reporter.load_report(filepath)
            assert loaded.tag == sample_report.tag
            assert loaded.dataset_size == sample_report.dataset_size
            for metric in sample_report.overall_scores:
                assert loaded.overall_scores[metric] == sample_report.overall_scores[metric]

    def test_load_nonexistent_file(self) -> None:
        """加载不存在的文件应报错。"""
        reporter = RAGASReporter.__new__(RAGASReporter)
        with pytest.raises(FileNotFoundError):
            reporter.load_report("/tmp/non_existent_report.json")


class TestCompareReports:
    def test_compare_improved(self, sample_report: EvaluationReport) -> None:
        """对比时基线低于新报告应判定 improved。"""
        improved = EvaluationReport(
            tag="v2",
            timestamp="2026-06-27T12:00:00",
            overall_scores={
                "faithfulness": 0.90,
                "answer_relevancy": 0.95,
                "context_precision": 0.85,
                "context_recall": 0.92,
            },
            per_sample_scores=[],
            category_breakdown={},
            dataset_size=2,
            metadata={},
        )
        reporter = RAGASReporter.__new__(RAGASReporter)
        result = reporter.compare_reports(sample_report, improved)
        assert result.verdict == "improved"
        assert result.overall_deltas["faithfulness"] == pytest.approx(0.05)

    def test_compare_regressed(self, sample_report: EvaluationReport) -> None:
        """对比时基线高于新报告应判定 regressed。"""
        worse = EvaluationReport(
            tag="v2",
            timestamp="2026-06-27T12:00:00",
            overall_scores={
                "faithfulness": 0.70,
                "answer_relevancy": 0.80,
                "context_precision": 0.65,
                "context_recall": 0.75,
            },
            per_sample_scores=[],
            category_breakdown={},
            dataset_size=2,
            metadata={},
        )
        reporter = RAGASReporter.__new__(RAGASReporter)
        result = reporter.compare_reports(sample_report, worse)
        assert result.verdict == "regressed"
        assert result.overall_deltas["faithfulness"] == pytest.approx(-0.15)

    def test_compare_mixed(self, sample_report: EvaluationReport) -> None:
        """有升有降应判定 mixed。"""
        mixed = EvaluationReport(
            tag="v2",
            timestamp="2026-06-27T12:00:00",
            overall_scores={
                "faithfulness": 0.90,
                "answer_relevancy": 0.88,
                "context_precision": 0.78,
                "context_recall": 0.88,
            },
            per_sample_scores=[],
            category_breakdown={},
            dataset_size=2,
            metadata={},
        )
        reporter = RAGASReporter.__new__(RAGASReporter)
        result = reporter.compare_reports(sample_report, mixed)
        assert result.verdict == "mixed"


class TestFormatMarkdown:
    def test_format_report_markdown(self, sample_report: EvaluationReport) -> None:
        """Markdown 格式化应包含报告标题和指标表。"""
        reporter = RAGASReporter.__new__(RAGASReporter)
        md = reporter.format_report_markdown(sample_report)
        assert "# RAGAS 评估报告" in md
        assert sample_report.tag in md
        assert "faithfulness" in md or "忠实度" in md
        assert "0.8500" in md or "0.85" in md

    def test_format_comparison_markdown(self) -> None:
        """对比 Markdown 应包含基线标签和增量信息。"""
        comparison = ComparisonResult(
            baseline_tag="baseline",
            comparison_tag="v2",
            overall_deltas={"faithfulness": 0.05},
            category_deltas={},
            verdict="improved",
            significant_changes=["faithfulness: +0.05 ↑"],
        )
        reporter = RAGASReporter.__new__(RAGASReporter)
        md = reporter.format_comparison_markdown(comparison)
        assert "baseline → v2" in md
        assert "+0.05" in md

    def test_metric_labels_present(self) -> None:
        """指标显示名称应包含中文说明。"""
        assert "忠实度" in METRIC_LABELS["faithfulness"]
        assert len(METRIC_LABELS) == 4
