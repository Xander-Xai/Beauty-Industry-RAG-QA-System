"""
RAGAS 评估报告生成器

生成结构化的评估报告，支持整体评分、逐条明细、类别拆分、基线对比。
报告可保存为 JSON 文件，并格式化为人类可读的 Markdown。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime

from tests.evaluation.ragas_eval import RAGASEvaluator

logger = logging.getLogger(__name__)

# 指标显示名称映射
METRIC_LABELS: dict[str, str] = {
    "faithfulness": "Faithfulness（忠实度）",
    "answer_relevancy": "Answer Relevancy（答案相关性）",
    "context_precision": "Context Precision（上下文精确度）",
    "context_recall": "Context Recall（上下文召回率）",
}


@dataclass
class EvaluationReport:
    """评估报告数据类。"""

    tag: str  # 报告标签，如 "baseline", "v2.1-prompt-tweak"
    timestamp: str  # ISO 格式时间戳
    overall_scores: dict[str, float]  # 指标名称 → 整体分数
    per_sample_scores: list[dict]  # 每条数据的评分 [{question, faithfulness, ..., business_type, difficulty}]
    category_breakdown: dict[str, dict[str, float]]  # {类别: {指标: 均值}}
    dataset_size: int
    metadata: dict  # 配置快照、LLM 后端信息等


@dataclass
class ComparisonResult:
    """基线对比结果。"""

    baseline_tag: str
    comparison_tag: str
    overall_deltas: dict[str, float]  # 指标 → 差值（comparison - baseline）
    category_deltas: dict[str, dict[str, float]]  # {类别: {指标: 差值}}
    verdict: str  # "improved" | "regressed" | "mixed"
    significant_changes: list[str]  # 显著变化的描述


class RAGASReporter:
    """RAGAS 评估报告生成器。

    用法:
        evaluator = RAGASEvaluator("golden_set.jsonl")
        reporter = RAGASReporter(evaluator)
        report = reporter.run_and_report(tag="baseline")
        reporter.save_report(report, "./data/eval/reports")
        print(reporter.format_report_markdown(report))
    """

    def __init__(self, evaluator: RAGASEvaluator) -> None:
        self._evaluator = evaluator

    # ------------------------------------------------------------------
    # 运行评估 + 生成报告
    # ------------------------------------------------------------------

    def run_and_report(
        self,
        tag: str,
        answers: list[str] | None = None,
        metrics: list[str] | None = None,
    ) -> EvaluationReport:
        """运行 RAGAS 评估并生成结构化报告。

        Args:
            tag: 报告标签，用于区分不同配置/版本。
            answers: 可选的 RAG 系统生成答案列表。
            metrics: 要计算的指标列表。

        Returns:
            包含整体评分、逐条明细、类别拆分的 EvaluationReport。
        """
        overall_scores = self._evaluator.evaluate(answers=answers, metrics=metrics)
        per_sample = self._extract_per_sample_scores()

        # 合并分类字段到 per_sample
        dataset = self._evaluator.dataset
        for i, entry in enumerate(per_sample if per_sample else []):
            if i < len(dataset):
                entry["business_type"] = dataset[i].get("business_type", "unspecified")
                entry["difficulty"] = dataset[i].get("difficulty", "unspecified")

        category_breakdown = self._compute_category_breakdown(per_sample or [])

        # 提取 metadata
        try:
            from common.config import get_config

            cfg = get_config()
            metadata = {
                "deployment_mode": cfg.deployment_mode,
                "dataset_path": cfg.ragas.dataset_path,
                "llm_backend": asdict(cfg.ragas.llm_backend) if hasattr(cfg.ragas, "llm_backend") else {},
                "default_metrics": list(cfg.ragas.default_metrics),
            }
        except Exception:
            metadata = {}

        return EvaluationReport(
            tag=tag,
            timestamp=datetime.now().isoformat(),
            overall_scores=overall_scores,
            per_sample_scores=per_sample or [],
            category_breakdown=category_breakdown,
            dataset_size=len(dataset),
            metadata=metadata,
        )

    def _extract_per_sample_scores(self) -> list[dict] | None:
        """从 RAGAS Result 中提取每条数据的评分。

        Returns:
            每条数据的评分列表，如果 RAGAS 未安装则返回 None。
        """
        raw = getattr(self._evaluator, "_last_raw_result", None)
        if raw is None:
            return None

        try:
            import pandas as pd

            if hasattr(raw, "to_pandas"):
                df: pd.DataFrame = raw.to_pandas()
                return df.to_dict(orient="records")
        except Exception as exc:
            logger.warning("提取逐条评分失败: %s", exc)

        try:
            if hasattr(raw, "scores"):
                scores = raw.scores
                if isinstance(scores, list):
                    return scores
        except Exception as exc:
            logger.debug("读取 RAGAS Result.scores 失败: %s", exc)

        return None

    def _compute_category_breakdown(
        self,
        per_sample: list[dict],
    ) -> dict[str, dict[str, float]]:
        """按分类字段（business_type, difficulty）计算各指标均值。"""
        breakdown: dict[str, dict[str, float]] = {}
        if not per_sample:
            return breakdown

        # 获取所有评分指标键（排除分类字段）
        metric_keys = [k for k in per_sample[0] if k not in ("question", "business_type", "difficulty")]

        # 按 business_type 拆分
        bt_groups: dict[str, list[dict]] = {}
        for row in per_sample:
            bt = row.get("business_type", "unspecified")
            bt_groups.setdefault(bt, []).append(row)

        for bt, rows in bt_groups.items():
            key = f"business_type:{bt}"
            breakdown[key] = {}
            for mk in metric_keys:
                vals = [r[mk] for r in rows if isinstance(r.get(mk), (int, float))]
                breakdown[key][mk] = sum(vals) / len(vals) if vals else 0.0

        # 按 difficulty 拆分
        diff_groups: dict[str, list[dict]] = {}
        for row in per_sample:
            diff = row.get("difficulty", "unspecified")
            diff_groups.setdefault(diff, []).append(row)

        for diff, rows in diff_groups.items():
            key = f"difficulty:{diff}"
            breakdown[key] = {}
            for mk in metric_keys:
                vals = [r[mk] for r in rows if isinstance(r.get(mk), (int, float))]
                breakdown[key][mk] = sum(vals) / len(vals) if vals else 0.0

        return breakdown

    # ------------------------------------------------------------------
    # 持久化：保存 / 加载报告
    # ------------------------------------------------------------------

    def save_report(self, report: EvaluationReport, output_dir: str) -> str:
        """将报告保存为 JSON 文件。

        Returns:
            文件的绝对路径。
        """
        os.makedirs(output_dir, exist_ok=True)
        safe_tag = report.tag.replace("/", "_").replace(" ", "_")
        filename = f"ragas_report_{safe_tag}_{report.timestamp[:10]}.json"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, ensure_ascii=False, indent=2)

        logger.info("评估报告已保存: %s", filepath)
        return os.path.abspath(filepath)

    def load_report(self, path: str) -> EvaluationReport:
        """从 JSON 文件加载报告。"""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return EvaluationReport(**data)

    # ------------------------------------------------------------------
    # 对比模式
    # ------------------------------------------------------------------

    def compare_reports(
        self,
        baseline: EvaluationReport,
        comparison: EvaluationReport,
    ) -> ComparisonResult:
        """对比两个报告，计算增量并生成判断。"""
        overall_deltas: dict[str, float] = {}
        all_metrics = set(baseline.overall_scores) | set(comparison.overall_scores)

        for metric in all_metrics:
            b = baseline.overall_scores.get(metric, 0.0)
            c = comparison.overall_scores.get(metric, 0.0)
            overall_deltas[metric] = round(c - b, 4)

        # 类别维度增量
        category_deltas: dict[str, dict[str, float]] = {}
        all_categories = set(baseline.category_breakdown) | set(comparison.category_breakdown)
        for cat in all_categories:
            cat_deltas: dict[str, float] = {}
            b_cat = baseline.category_breakdown.get(cat, {})
            c_cat = comparison.category_breakdown.get(cat, {})
            for metric in set(b_cat) | set(c_cat):
                bv = b_cat.get(metric, 0.0)
                cv = c_cat.get(metric, 0.0)
                cat_deltas[metric] = round(cv - bv, 4)
            if cat_deltas:
                category_deltas[cat] = cat_deltas

        # 判断 verdict
        improvements = sum(1 for v in overall_deltas.values() if v > 0.01)
        regressions = sum(1 for v in overall_deltas.values() if v < -0.01)
        if regressions == 0 and improvements > 0:
            verdict = "improved"
        elif improvements == 0 and regressions > 0:
            verdict = "regressed"
        else:
            verdict = "mixed"

        # 显著变化
        significant_changes: list[str] = []
        THRESHOLD = 0.03
        for metric, delta in overall_deltas.items():
            label = METRIC_LABELS.get(metric, metric)
            if delta > THRESHOLD:
                significant_changes.append(f"{label}: +{delta:.2f} ↑")
            elif delta < -THRESHOLD:
                significant_changes.append(f"{label}: {delta:.2f} ↓")

        return ComparisonResult(
            baseline_tag=baseline.tag,
            comparison_tag=comparison.tag,
            overall_deltas=overall_deltas,
            category_deltas=category_deltas,
            verdict=verdict,
            significant_changes=significant_changes,
        )

    # ------------------------------------------------------------------
    # 格式化输出
    # ------------------------------------------------------------------

    def format_report_markdown(self, report: EvaluationReport) -> str:
        """将报告格式化为人类可读的 Markdown。"""
        lines: list[str] = []
        lines.append(f"# RAGAS 评估报告: {report.tag}")
        lines.append("")
        lines.append(f"- **时间戳**: {report.timestamp}")
        lines.append(f"- **数据集**: {report.dataset_size} 条")

        # 整体评分
        lines.append("")
        lines.append("## 整体评分")
        lines.append("")
        lines.append("| 指标 | 分数 |")
        lines.append("|------|------|")
        for metric in ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]:
            label = METRIC_LABELS.get(metric, metric)
            score = report.overall_scores.get(metric, "—")
            if isinstance(score, float):
                lines.append(f"| {label} | {score:.4f} |")
            else:
                lines.append(f"| {label} | {score} |")

        # 类别拆分
        if report.category_breakdown:
            lines.append("")
            lines.append("## 类别拆分")
            for cat_key, metrics_dict in sorted(report.category_breakdown.items()):
                cat_label = cat_key.replace("business_type:", "业务类型: ").replace("difficulty:", "难度: ")
                lines.append("")
                lines.append(f"### {cat_label}")
                lines.append("")
                lines.append("| 指标 | 分数 |")
                lines.append("|------|------|")
                for metric, score in metrics_dict.items():
                    label = METRIC_LABELS.get(metric, metric)
                    lines.append(f"| {label} | {score:.4f} |")

        return "\n".join(lines)

    def format_comparison_markdown(self, comparison: ComparisonResult) -> str:
        """将对比结果格式化为 Markdown。"""
        lines: list[str] = []
        verdict_label = {
            "improved": "✅ 总体提升",
            "regressed": "❌ 总体下降",
            "mixed": "⚠️ 混合变化",
        }.get(comparison.verdict, comparison.verdict)

        lines.append(f"# RAGAS 评估对比: {comparison.baseline_tag} → {comparison.comparison_tag}")
        lines.append("")
        lines.append(f"**判定**: {verdict_label}")
        lines.append("")

        # 整体增量
        lines.append("## 整体指标变化")
        lines.append("")
        lines.append("| 指标 | 基线 | 对比 | 增量 |")
        lines.append("|------|------|------|------|")
        for metric in ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]:
            label = METRIC_LABELS.get(metric, metric)
            delta = comparison.overall_deltas.get(metric, 0.0)
            arrow = "↑" if delta > 0.01 else ("↓" if delta < -0.01 else "→")
            lines.append(f"| {label} | — | — | {delta:+.4f} {arrow} |")

        # 显著变化
        if comparison.significant_changes:
            lines.append("")
            lines.append("### 显著变化")
            for change in comparison.significant_changes:
                lines.append(f"- {change}")

        return "\n".join(lines)
