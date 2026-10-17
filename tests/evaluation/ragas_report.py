"""
RAGAS 评估报告生成器

生成结构化的评估报告，支持整体评分、逐条明细、类别拆分、基线对比。
报告可保存为 JSON 文件，并格式化为人类可读的 Markdown。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime

from benchmarks.provenance import UNCLASSIFIED_DATA_CLASS, dataset_data_provenance
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
    overall_scores: dict[str, float]  # 指标名称 → 整体分数（未返回的指标不出现，不写 0）
    per_sample_scores: list[dict]  # 每条数据的评分 [{question, faithfulness, ..., business_type, difficulty}]
    # {类别: {指标: 均值}}；值为 None 表示该类别没有该指标的观测，不是 0 分
    category_breakdown: dict[str, dict[str, float | None]]
    dataset_size: int
    metadata: dict  # 配置快照、LLM 后端信息等
    # Provenance / accounting (defaults keep older report files loadable).
    run_id: str = ""
    git_commit: str = ""
    dataset: str = ""
    dataset_sha256: str = ""
    mode: str = "reference"  # "pipeline" | "reference"
    evaluator_status: str = "unknown"  # "available" | "unavailable"
    unavailable_reason: str | None = None
    requested_samples: int = 0
    successful_samples: int = 0
    failed_samples: int = 0
    skipped_samples: int = 0
    sample_ids: list[str] = field(default_factory=list)
    evaluator_provider: str = ""
    evaluator_model: str = ""
    generation_model: str = ""
    metrics: list[str] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    environment: dict = field(default_factory=dict)
    # Self-description. `per_sample_scores` quotes the dataset's question,
    # contexts and reference answer. The defaults are *unknown*, not synthetic:
    # a report written before these fields existed has no provenance, and
    # defaulting it to "synthetic, no real claims" would turn an absence into an
    # affirmative statement about data this process never inspected. A report
    # built now gets the resolved class of the dataset it actually scored.
    data_class: str = UNCLASSIFIED_DATA_CLASS
    carries_real_regulatory_claims: bool | None = None
    notice: str = ""


@dataclass
class ComparisonResult:
    """基线对比结果。"""

    baseline_tag: str
    comparison_tag: str
    overall_deltas: dict[str, float]  # 指标 → 差值（comparison - baseline）
    category_deltas: dict[str, dict[str, float]]  # {类别: {指标: 差值}}
    verdict: str  # "improved" | "regressed" | "mixed" | "not_comparable"
    significant_changes: list[str]  # 显著变化的描述
    #: Why a comparison is unavailable or partial. Empty when it is comparable.
    #: A comparison that cannot be made must say so here rather than ship zeros.
    notes: list[str] = field(default_factory=list)


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
        """运行一次评估并生成报告（向后兼容入口）。

        This performs exactly one evaluator call, then builds the report from the
        stored run state; it never evaluates twice.
        """
        self._evaluator.evaluate(answers=answers, metrics=metrics)
        return self.build_report(tag=tag, metrics=metrics)

    def build_report(
        self,
        tag: str,
        metrics: list[str] | None = None,
        pipeline_mode: bool = False,
    ) -> EvaluationReport:
        """Build a report from the evaluator's completed run state.

        Does NOT call the evaluator. Aggregates only over the successful samples
        that were actually evaluated.
        """
        evaluator = self._evaluator
        available = evaluator.last_available
        warning = evaluator.last_warning
        overall_scores = dict(evaluator.last_scores) if available else {}

        per_sample = self._extract_per_sample_scores() or []
        samples = evaluator.last_samples

        # Attach generated/pipeline provenance and category fields per sample.
        category_lookup = self._category_lookup()
        for i, entry in enumerate(per_sample):
            if i < len(samples):
                sample = samples[i]
                entry.setdefault("sample_id", sample.sample_id)
                entry["generated_answer"] = sample.generated_answer
                entry["retrieved_contexts"] = sample.retrieved_contexts
                entry["reference_answer"] = sample.reference_answer
                if sample.model:
                    entry["generation_model"] = sample.model
            sid = entry.get("sample_id")
            meta = category_lookup.get(sid, {})
            entry["business_type"] = meta.get("business_type", "unspecified")
            entry["difficulty"] = meta.get("difficulty", "unspecified")

        category_breakdown = self._compute_category_breakdown(per_sample)

        backend = self._evaluator_backend()
        generation_models = sorted({s.model for s in samples if s.model})
        counts = evaluator.last_counts

        metadata = self._config_metadata()
        report = EvaluationReport(
            tag=tag,
            timestamp=datetime.now().isoformat(),
            overall_scores=overall_scores,
            per_sample_scores=per_sample,
            category_breakdown=category_breakdown,
            dataset_size=counts.get("successful", len(per_sample)),
            metadata=metadata,
            **dataset_data_provenance(self._dataset_path()),
            run_id=f"{tag}-{datetime.now().strftime('%Y%m%dT%H%M%S')}",
            git_commit=self._git_commit(),
            dataset=self._dataset_path(),
            dataset_sha256=self._dataset_sha256(),
            mode="pipeline" if pipeline_mode else "reference",
            evaluator_status="available" if available else "unavailable",
            unavailable_reason=None if available else warning,
            requested_samples=counts.get("requested", 0),
            successful_samples=counts.get("successful", 0),
            failed_samples=counts.get("failed", 0),
            skipped_samples=counts.get("skipped", 0),
            sample_ids=list(evaluator.last_sample_ids),
            evaluator_provider=backend.get("provider", ""),
            evaluator_model=backend.get("model", ""),
            generation_model=",".join(generation_models),
            metrics=list(evaluator.last_scores),
            failures=list(evaluator.last_failures),
            environment=self._environment(),
        )
        return report

    # ------------------------------------------------------------------
    # Provenance helpers
    # ------------------------------------------------------------------

    def _dataset_path(self) -> str:
        return getattr(self._evaluator, "_dataset_path", "") or ""

    def _dataset_sha256(self) -> str:
        path = self._dataset_path()
        try:
            with open(path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()[:16]
        except OSError:
            return ""

    def _category_lookup(self) -> dict[str, dict]:
        """Map sample id -> category fields from the evaluator dataset."""
        from tests.evaluation.ragas_eval import sample_id_for

        lookup: dict[str, dict] = {}
        for i, entry in enumerate(self._evaluator.dataset):
            lookup[sample_id_for(entry, i)] = {
                "business_type": entry.get("business_type", "unspecified"),
                "difficulty": entry.get("difficulty", "unspecified"),
            }
        return lookup

    @staticmethod
    def _git_commit() -> str:
        try:
            out = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return out.stdout.strip()
        except Exception:
            return ""

    @staticmethod
    def _environment() -> dict:
        env: dict = {"python": sys.version.split()[0]}
        try:
            import ragas  # type: ignore[import-untyped]

            env["ragas"] = getattr(ragas, "__version__", "unknown")
        except Exception:
            env["ragas"] = "unavailable"
        return env

    @staticmethod
    def _evaluator_backend() -> dict:
        try:
            from tests.evaluation.ragas_eval import evaluator_backend

            return evaluator_backend()
        except Exception:
            return {}

    @staticmethod
    def _config_metadata() -> dict:
        try:
            from common.config import get_config

            cfg = get_config()
            return {
                "deployment_mode": cfg.deployment_mode,
                "dataset_path": cfg.ragas.dataset_path,
                "llm_backend": asdict(cfg.ragas.llm_backend) if hasattr(cfg.ragas, "llm_backend") else {},
                "default_metrics": list(cfg.ragas.default_metrics),
            }
        except Exception:
            return {}

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
    ) -> dict[str, dict[str, float | None]]:
        """按分类字段（business_type, difficulty）计算各指标均值。"""
        breakdown: dict[str, dict[str, float | None]] = {}
        if not per_sample:
            return breakdown

        # 获取所有评分指标键（排除分类字段）
        metric_keys = [k for k in per_sample[0] if k not in ("question", "business_type", "difficulty")]

        def _means(rows: list[dict]) -> dict[str, float | None]:
            out: dict[str, float | None] = {}
            for mk in metric_keys:
                vals = [r[mk] for r in rows if isinstance(r.get(mk), (int, float))]
                # No observation in this category means the category has no score
                # for that metric. Writing 0.0 there reported "measured, and it
                # scored zero" for a cell that was never measured.
                out[mk] = sum(vals) / len(vals) if vals else None
            return out

        # 按 business_type 拆分
        bt_groups: dict[str, list[dict]] = {}
        for row in per_sample:
            bt = row.get("business_type", "unspecified")
            bt_groups.setdefault(bt, []).append(row)

        for bt, rows in bt_groups.items():
            breakdown[f"business_type:{bt}"] = _means(rows)

        # 按 difficulty 拆分
        diff_groups: dict[str, list[dict]] = {}
        for row in per_sample:
            diff = row.get("difficulty", "unspecified")
            diff_groups.setdefault(diff, []).append(row)

        for diff, rows in diff_groups.items():
            breakdown[f"difficulty:{diff}"] = _means(rows)

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
        """对比两个报告，计算增量并生成判断。

        Two reports may only be differenced when **both** actually ran. A report
        whose evaluator was unavailable carries no scores, and substituting
        ``0.0`` for a missing score turns "this side never executed" into a
        measured improvement on every metric — the old behaviour reported
        ``✅ 总体提升`` for a run whose baseline never ran. A missing side is
        reported as ``not_comparable`` with the reason attached, and no delta is
        emitted for a metric only one side measured.
        """
        notes: list[str] = []
        unusable: list[str] = []
        unknown: list[str] = []
        for report in (baseline, comparison):
            if report.evaluator_status == "unavailable" or not report.overall_scores:
                unusable.append(
                    f"{report.tag}（{report.evaluator_status}"
                    f"{'：' + report.unavailable_reason if report.unavailable_reason else '：无实测分数'}）"
                )
            elif report.evaluator_status != "available":
                # A report written before `evaluator_status` existed defaults to
                # `unknown`. It carries scores, so it may be compared, but its
                # provenance is unverified and the comparison says so rather
                # than presenting the delta as fully attested.
                unknown.append(f"{report.tag}（evaluator_status: unknown，来源记录无此字段）")
        if unusable:
            return ComparisonResult(
                baseline_tag=baseline.tag,
                comparison_tag=comparison.tag,
                overall_deltas={},
                category_deltas={},
                verdict="not_comparable",
                significant_changes=[],
                notes=[
                    "对比不可用：以下报告没有实测分数，相减会把「未执行」当成 0 分从而伪造出提升。"
                    f"{'; '.join(unusable)}"
                ],
            )
        notes.extend(f"来源记录的评测器状态未标注，对比结果未经 provenance 复核：{tag}" for tag in unknown)

        overall_deltas: dict[str, float] = {}
        all_metrics = set(baseline.overall_scores) | set(comparison.overall_scores)

        for metric in sorted(all_metrics):
            if metric not in baseline.overall_scores or metric not in comparison.overall_scores:
                missing_in = [
                    tag
                    for tag, report in (("基线", baseline), ("对比", comparison))
                    if metric not in report.overall_scores
                ]
                notes.append(
                    f"{METRIC_LABELS.get(metric, metric)}：{'/'.join(missing_in)} 未给出该指标，"
                    "已跳过而不是按 0 计入差值"
                )
                continue
            overall_deltas[metric] = round(comparison.overall_scores[metric] - baseline.overall_scores[metric], 4)

        # 类别维度增量
        category_deltas: dict[str, dict[str, float]] = {}
        all_categories = set(baseline.category_breakdown) | set(comparison.category_breakdown)
        for cat in sorted(all_categories):
            cat_deltas: dict[str, float] = {}
            b_cat = baseline.category_breakdown.get(cat) or {}
            c_cat = comparison.category_breakdown.get(cat) or {}
            for metric in sorted(set(b_cat) | set(c_cat)):
                if b_cat.get(metric) is None or c_cat.get(metric) is None:
                    continue
                cat_deltas[metric] = round(c_cat[metric] - b_cat[metric], 4)
            if cat_deltas:
                category_deltas[cat] = cat_deltas

        # 判断 verdict
        improvements = sum(1 for v in overall_deltas.values() if v > 0.01)
        regressions = sum(1 for v in overall_deltas.values() if v < -0.01)
        if not overall_deltas:
            # Both sides ran but share no comparable metric, so no delta exists.
            # "mixed" would claim a mixed result from zero measurements.
            verdict = "not_comparable"
            notes.append("两侧报告没有共同的可比指标，没有产生任何差值")
        elif regressions == 0 and improvements > 0:
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
            notes=notes,
        )

    # ------------------------------------------------------------------
    # 格式化输出
    # ------------------------------------------------------------------

    def format_report_markdown(self, report: EvaluationReport) -> str:
        """将报告格式化为人类可读的 Markdown。"""
        lines: list[str] = []
        lines.append(f"# RAGAS 评估报告: {report.tag}")
        lines.append("")
        if report.notice:
            # Above the header block, so it is the first thing read. This report
            # quotes fixture questions and reference answers that name real
            # regulation instruments next to invented limits.
            lines.append(f"> ⚠️ {report.notice}")
            lines.append("")
        if report.data_class and report.data_class != UNCLASSIFIED_DATA_CLASS:
            lines.append(f"- **数据类别**: {report.data_class}")
        lines.append(f"- **时间戳**: {report.timestamp}")
        lines.append(f"- **模式**: {report.mode}（pipeline=真实管线输出；reference=参考答案 smoke）")
        lines.append(f"- **git commit**: {report.git_commit or '—'}")
        lines.append(f"- **数据集**: {report.dataset} (sha256:{report.dataset_sha256 or '—'})")
        lines.append(f"- **评测器**: {report.evaluator_provider}/{report.evaluator_model}")
        lines.append(f"- **生成模型**: {report.generation_model or '—'}")
        lines.append(
            f"- **样本**: requested={report.requested_samples} "
            f"successful={report.successful_samples} "
            f"failed={report.failed_samples} skipped={report.skipped_samples}"
        )
        if report.evaluator_status != "available":
            lines.append(f"- **状态**: UNAVAILABLE — {report.unavailable_reason or 'unknown'}")

        # 整体评分
        lines.append("")
        lines.append("## 整体评分")
        lines.append("")
        lines.append("| 指标 | 分数 |")
        lines.append("|------|------|")
        for metric in ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]:
            label = METRIC_LABELS.get(metric, metric)
            # `None` = this category produced no observation for this metric.
            # `—` is the same token the overall block already uses, so a
            # category table can no longer show a fabricated 0.0000.
            score = report.overall_scores.get(metric)
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                lines.append(f"| {label} | {float(score):.4f} |")
            else:
                lines.append(f"| {label} | — |")

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
                    if isinstance(score, (int, float)) and not isinstance(score, bool):
                        lines.append(f"| {label} | {float(score):.4f} |")
                    else:
                        lines.append(f"| {label} | — |")

        if report.failures:
            lines.append("")
            lines.append("## 失败样本")
            lines.append("")
            lines.append("| sample_id | stage | exception | message |")
            lines.append("|-----------|-------|-----------|---------|")
            for failure in report.failures:
                lines.append(
                    f"| {failure.get('sample_id', '?')} | {failure.get('stage', '?')} "
                    f"| {failure.get('exception', '?')} | {failure.get('message', '')} |"
                )

        return "\n".join(lines)

    def format_comparison_markdown(self, comparison: ComparisonResult) -> str:
        """将对比结果格式化为 Markdown。"""
        lines: list[str] = []
        verdict_label = {
            "improved": "✅ 总体提升",
            "regressed": "❌ 总体下降",
            "mixed": "⚠️ 混合变化",
            "not_comparable": "⛔ 不可对比（未产生实测差值）",
        }.get(comparison.verdict, comparison.verdict)

        lines.append(f"# RAGAS 评估对比: {comparison.baseline_tag} → {comparison.comparison_tag}")
        lines.append("")
        lines.append(f"**判定**: {verdict_label}")
        lines.append("")

        if comparison.notes:
            lines.append("> ⚠️ 本次对比不完整，以下说明不可用的部分：")
            for note in comparison.notes:
                lines.append(f"> - {note}")
            lines.append("")

        # 整体增量
        lines.append("## 整体指标变化")
        lines.append("")
        lines.append("| 指标 | 增量 |")
        lines.append("|------|------|")
        for metric in ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]:
            label = METRIC_LABELS.get(metric, metric)
            # An absent delta is an unmeasured delta. The old `get(metric, 0.0)`
            # plus a `→` arrow printed `+0.0000 →` for a metric nobody compared,
            # which reads as "measured, no change".
            delta = comparison.overall_deltas.get(metric)
            if isinstance(delta, (int, float)) and not isinstance(delta, bool):
                arrow = "↑" if delta > 0.01 else ("↓" if delta < -0.01 else "→")
                lines.append(f"| {label} | {float(delta):+.4f} {arrow} |")
            else:
                lines.append(f"| {label} | — |")

        # 显著变化
        if comparison.significant_changes:
            lines.append("")
            lines.append("### 显著变化")
            for change in comparison.significant_changes:
                lines.append(f"- {change}")

        return "\n".join(lines)
