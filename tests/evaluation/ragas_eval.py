"""
RAGAS 评估器

独立离线评估模块，用于基于 RAGAS 框架评估 RAG 系统质量。
支持 CLI 接口和编程式调用。

设计要点：
- 一次 run 只调用一次 evaluator（避免重复计费与结果漂移）。
- ``--pipeline`` 模式使用真实 pipeline 生成的 answer 与真实 retrieved contexts。
- 缺少 RAGAS 依赖 / evaluator API key 时明确失败，绝不伪造 0 分报告。
- 失败样本单独记录，聚合分母只使用成功样本。

如果 RAGAS 未安装，evaluate() 会优雅降级返回全零分数 + ``_warning``。
注意：零分是“未运行”的降级标记，不是质量结果；CLI 不会把它保存为成功报告。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_ALL_METRIC_NAMES = [
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
]


def aggregate_ragas_scores(raw: Any, metrics: list[str], expected_rows: int) -> dict[str, float]:
    """Compute means from actual score observations, never missing-as-zero."""
    rows = getattr(raw, "scores", None)
    if not isinstance(rows, list):
        to_pandas = getattr(raw, "to_pandas", None)
        if callable(to_pandas):
            try:
                rows = to_pandas().to_dict(orient="records")
            except Exception:
                return {}

    def valid(value: Any) -> bool:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)

    result: dict[str, float] = {}
    if isinstance(rows, list):
        if expected_rows <= 0 or len(rows) != expected_rows or any(not isinstance(row, dict) for row in rows):
            return {}
        for metric in metrics:
            values = [float(row[metric]) for row in rows if valid(row.get(metric))]
            if values:
                result[metric] = math.fsum(values) / len(values)
        return result

    # Legacy integrations/test doubles may provide scalar aggregate attributes.
    for metric in metrics:
        value = getattr(raw, metric, None)
        if valid(value):
            result[metric] = float(value)
    return result


class EvaluatorUnavailableError(RuntimeError):
    """Raised when the RAGAS dependency itself cannot be imported."""


class EvaluatorConfigError(RuntimeError):
    """Raised when the evaluator backend is not usable (e.g. missing API key)."""


class AllSamplesFailedError(RuntimeError):
    """Raised when every requested pipeline sample failed."""

    def __init__(self, failures: list[dict]):
        super().__init__(f"all {len(failures)} requested pipeline sample(s) failed")
        self.failures = failures


@dataclass
class PipelineSample:
    """One evaluated sample, with the real pipeline outputs recorded."""

    sample_id: str
    question: str
    generated_answer: str
    retrieved_contexts: list[str]
    reference_answer: str
    reference_contexts: list[str]
    route: str = ""
    model: str = ""


def load_golden_set(path: str, *, strict: bool = False) -> list[dict]:
    """加载 JSON Lines 黄金测试集。

    Args:
        path: JSONL 文件路径，每行一个 JSON 对象。

    Returns:
        包含 question / answer / contexts / ground_truth 的字典列表。
        空文件或无条目时返回空列表。
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"数据集文件未找到: {path}")

    dataset: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
                if strict and not isinstance(entry, dict):
                    raise ValueError(f"invalid_dataset_row:{line_num}")
                dataset.append(entry)
            except json.JSONDecodeError as exc:
                if strict:
                    raise ValueError(f"invalid_dataset_json:{line_num}") from None
                preview = stripped[:80] + "..." if len(stripped) > 80 else stripped
                logger.warning("跳过无效 JSON 行 (line %d): %s — 内容: %s", line_num, exc, preview)
    if strict:
        validate_golden_set(dataset)
    return dataset


def sample_id_for(entry: dict, index: int) -> str:
    """Stable sample id for a golden-set entry."""
    explicit = entry.get("sample_id") or entry.get("id")
    if explicit:
        return str(explicit)
    digest = hashlib.sha256(str(entry.get("question", "")).encode("utf-8")).hexdigest()[:8]
    return f"{index:04d}-{digest}"


def validate_golden_set(dataset: list[dict]) -> None:
    """Fail closed before any model call; never silently change the population."""
    if not dataset:
        raise ValueError("empty_dataset")
    seen: set[str] = set()
    for index, entry in enumerate(dataset):
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("question"), str)
            or not entry["question"].strip()
            or not isinstance(entry.get("ground_truth"), str)
            or not entry["ground_truth"].strip()
            or not isinstance(entry.get("answer"), str)
            or not isinstance(entry.get("contexts"), list)
            or any(not isinstance(context, str) for context in entry["contexts"])
        ):
            raise ValueError(f"invalid_dataset_fields:{index}")
        for key in ("sample_id", "id"):
            if key in entry and entry[key] is not None:
                value = entry[key]
                if type(value) not in (str, int) or not str(value).strip():
                    raise ValueError(f"invalid_sample_identity:{index}")
        sid = sample_id_for(entry, index)
        if sid in seen:
            raise ValueError(f"duplicate_sample_identity:{index}")
        seen.add(sid)


def format_for_ragas(
    dataset: list[dict],
    answers: list[str] | None = None,
    contexts: list[list[str]] | None = None,
) -> dict[str, list]:
    """将内部数据集格式化为 RAGAS 评估所需的格式。

    Args:
        dataset: 包含 question / answer / contexts / ground_truth 的字典列表。
        answers: 可选答案列表，覆盖 dataset 的 answer（例如 pipeline 生成答案）。
        contexts: 可选检索上下文列表，覆盖 dataset 的 contexts（例如 pipeline 实际召回）。

    Returns:
        包含 "question", "answer", "contexts", "ground_truth" 键的字典。
    """
    return {
        "question": [item["question"] for item in dataset],
        "answer": answers if answers is not None else [item["answer"] for item in dataset],
        "contexts": contexts if contexts is not None else [item["contexts"] for item in dataset],
        "ground_truth": [item["ground_truth"] for item in dataset],
    }


def evaluator_backend() -> dict[str, str]:
    """Resolve the evaluator backend from config with optional env overrides."""
    try:
        from common.config import get_config

        backend = get_config().ragas.llm_backend
        default = {
            "provider": getattr(backend, "type", "openai"),
            "model": getattr(backend, "model", "gpt-4o-mini"),
            "api_base": getattr(backend, "api_base", ""),
            "api_key_env": getattr(backend, "api_key_env", "OPENAI_API_KEY") or "OPENAI_API_KEY",
        }
    except Exception:
        default = {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_base": "",
            "api_key_env": "OPENAI_API_KEY",
        }
    return {
        "provider": os.environ.get("RAGAS_EVALUATOR_PROVIDER") or default["provider"],
        "model": os.environ.get("RAGAS_EVALUATOR_MODEL") or default["model"],
        "api_base": os.environ.get("RAGAS_EVALUATOR_BASE_URL") or default["api_base"],
        "api_key_env": default["api_key_env"],
    }


def require_evaluator_credentials() -> dict[str, str]:
    """Fail fast when the configured evaluator backend has no usable key."""
    backend = evaluator_backend()
    if backend["provider"] in ("openai", "azure_openai") and not os.environ.get(backend["api_key_env"]):
        raise EvaluatorConfigError(
            f"evaluator provider {backend['provider']!r} requires environment variable "
            f"{backend['api_key_env']!r} to be set"
        )
    return backend


class RAGASEvaluator:
    """RAGAS 评估器。

    用法:
        evaluator = RAGASEvaluator("sample_golden_set.jsonl")
        results = evaluator.evaluate()
    """

    def __init__(
        self,
        dataset_path: str | None = None,
        metrics: list[str] | None = None,
        *,
        strict_dataset: bool = False,
    ) -> None:
        try:
            from common.config import get_config

            cfg = get_config().ragas
            self._dataset_path = dataset_path or cfg.dataset_path
            self._default_metrics = metrics or list(cfg.default_metrics)
        except Exception:
            self._dataset_path = dataset_path or "tests/evaluation/golden_set.jsonl"
            self._default_metrics = metrics or list(_ALL_METRIC_NAMES)
        self._dataset = load_golden_set(self._dataset_path, strict=strict_dataset)
        self._last_raw_result: Any = None

        # Evaluation run state (single evaluation per run).
        self._evaluate_calls = 0
        self._last_scores: dict[str, float] = {}
        self._last_available = False
        self._last_warning: str | None = None
        self._last_answers: list[str] = []
        self._last_contexts: list[list[str]] = []
        self._last_sample_ids: list[str] = []
        self._last_samples: list[PipelineSample] = []
        self._last_failures: list[dict] = []
        self._last_counts: dict[str, int] = {"requested": 0, "successful": 0, "failed": 0, "skipped": 0}

    @property
    def dataset(self) -> list[dict]:
        """获取加载的数据集。"""
        return self._dataset

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------

    def select_entries(
        self,
        limit: int | None = None,
        sample_ids: list[str] | None = None,
    ) -> list[dict]:
        """Select dataset entries by explicit sample ids or a deterministic limit."""
        if sample_ids:
            wanted = [str(s) for s in sample_ids]
            index = {sample_id_for(entry, i): entry for i, entry in enumerate(self._dataset)}
            missing = [s for s in wanted if s not in index]
            if missing:
                raise ValueError(f"unknown sample ids: {missing}")
            return [index[s] for s in wanted]
        if limit is not None:
            if limit <= 0:
                raise ValueError("limit must be a positive integer")
            return self._dataset[:limit]
        return list(self._dataset)

    def _entry_sample_id(self, entry: dict, fallback_index: int) -> str:
        # select_entries returns original dataset objects; preserve their source index.
        for source_index, source_entry in enumerate(self._dataset):
            if source_entry is entry:
                return sample_id_for(entry, source_index)
        return sample_id_for(entry, fallback_index)

    @staticmethod
    def _selected_metric_names(metrics: list[str] | None) -> list[str]:
        if metrics is not None:
            return [m for m in metrics if m in _ALL_METRIC_NAMES]
        return list(_ALL_METRIC_NAMES)

    # ------------------------------------------------------------------
    # Core RAGAS invocation (exactly once per run)
    # ------------------------------------------------------------------

    def _run_ragas(
        self,
        questions: list[str],
        answers: list[str],
        contexts: list[list[str]],
        ground_truths: list[str],
        selected_metric_names: list[str],
    ) -> dict[str, float]:
        """Call RAGAS once and record availability state."""
        if os.environ.get("RAGAS_SECURITY_APPROVED") != "1":
            msg = "RAGAS BLOCKED: evaluator dependency security admission is missing; no evaluator was imported or called."
            logger.warning(msg)
            self._last_available = False
            self._last_warning = msg
            result: dict[str, float] = {name: 0.0 for name in selected_metric_names}
            result["_warning"] = msg  # type: ignore[assignment]
            return result

        try:
            from datasets import Dataset  # type: ignore[import-untyped]
            from ragas import evaluate as ragas_evaluate  # type: ignore[import-untyped]
            from ragas.metrics import (  # type: ignore[import-untyped]
                answer_relevancy,
                context_precision,
                context_recall,
                faithfulness,
            )
        except ImportError as exc:
            msg = f"RAGAS 或 datasets 未安装: {exc}。返回降级零值（非质量结果）。"
            logger.warning(msg)
            self._last_available = False
            self._last_warning = msg
            result: dict[str, float] = {name: 0.0 for name in selected_metric_names}
            result["_warning"] = msg  # type: ignore[assignment]
            return result

        all_metrics = {
            "faithfulness": faithfulness,
            "answer_relevancy": answer_relevancy,
            "context_precision": context_precision,
            "context_recall": context_recall,
        }
        selected_metrics = [all_metrics[m] for m in selected_metric_names]

        dataset = Dataset.from_dict(
            {
                "question": questions,
                "answer": answers,
                "contexts": contexts,
                "ground_truth": ground_truths,
            }
        )

        self._evaluate_calls += 1
        raw = ragas_evaluate(dataset=dataset, metrics=selected_metrics)
        self._last_raw_result = raw

        # Prefer the real EvaluationResult row API over legacy scalar doubles.
        scores = aggregate_ragas_scores(raw, selected_metric_names, len(questions))
        missing = [metric for metric in selected_metric_names if metric not in scores]

        if missing:
            msg = "RAGAS 未返回可用的指标: " + ", ".join(missing)
            logger.warning(msg)
        else:
            msg = None
        self._last_warning = msg
        # Availability answers "did the evaluator run?", not "did every metric
        # come back?". A partial run is a real run; an empty one is not.
        self._last_available = bool(scores)
        return scores

    # ------------------------------------------------------------------
    # Public evaluation entrypoints
    # ------------------------------------------------------------------

    def evaluate(
        self,
        answers: list[str] | None = None,
        metrics: list[str] | None = None,
        contexts: list[list[str]] | None = None,
        entries: list[dict] | None = None,
    ) -> dict[str, float]:
        """运行 RAGAS 评估（单次调用）。

        当未提供 ``answers`` 时使用数据集 reference answer；该模式是 evaluator
        smoke（参考答案），不代表真实 pipeline 质量。真实 pipeline 质量请用
        ``run_pipeline_samples`` / ``--pipeline``。

        缺依赖时返回零值 + ``_warning`` 降级标记（``_last_available`` 为 False）。
        """
        active = entries if entries is not None else self._dataset
        if not active:
            logger.warning("数据集为空，跳过评估")
            self._last_counts = {"requested": 0, "successful": 0, "failed": 0, "skipped": 0}
            return {}

        if answers is not None and len(answers) != len(active):
            raise ValueError(f"answers 长度 ({len(answers)}) 与数据集 ({len(active)}) 不匹配")
        if contexts is not None and len(contexts) != len(active):
            raise ValueError(f"contexts 长度 ({len(contexts)}) 与数据集 ({len(active)}) 不匹配")

        selected_metric_names = self._selected_metric_names(metrics)
        if not selected_metric_names:
            logger.warning("没有有效的指标可供评估。")
            return {}

        effective_answers = answers if answers is not None else [item["answer"] for item in active]
        effective_contexts = contexts if contexts is not None else [item["contexts"] for item in active]
        self._last_answers = list(effective_answers)
        self._last_contexts = [list(c) for c in effective_contexts]
        self._last_sample_ids = [self._entry_sample_id(entry, i) for i, entry in enumerate(active)]
        self._last_samples = [
            PipelineSample(
                sample_id=self._last_sample_ids[i],
                question=active[i]["question"],
                generated_answer=effective_answers[i],
                retrieved_contexts=list(effective_contexts[i]),
                reference_answer=active[i]["answer"],
                reference_contexts=list(active[i]["contexts"]),
            )
            for i in range(len(active))
        ]
        self._last_failures = []
        self._last_counts = {
            "requested": len(active),
            "successful": len(active),
            "failed": 0,
            "skipped": 0,
        }

        scores = self._run_ragas(
            [item["question"] for item in active],
            effective_answers,
            effective_contexts,
            [item["ground_truth"] for item in active],
            selected_metric_names,
        )
        self._last_scores = {k: v for k, v in scores.items() if not k.startswith("_")}  # type: ignore[assignment]
        return scores

    def evaluate_with_custom_answer_fn(
        self,
        answer_fn: Callable[[str, list[str]], str],
        metrics: list[str] | None = None,
    ) -> dict[str, float]:
        """使用自定义答案函数生成回答后运行 RAGAS 评估。"""
        answers = [answer_fn(item["question"], item["contexts"]) for item in self._dataset]
        return self.evaluate(answers=answers, metrics=metrics)

    @staticmethod
    def _extract_answer(ctx, response) -> str:
        if isinstance(response, str) and response:
            return response
        final = getattr(ctx, "final_response", "")
        if isinstance(final, str):
            return final
        return ""

    @staticmethod
    def _extract_contexts(ctx) -> list[str]:
        def collect(items) -> list[str]:
            out: list[str] = []
            for item in items or []:
                content = getattr(item, "content", None)
                if isinstance(content, str) and content:
                    out.append(content)
            return out

        for attr in ("rerank_results", "union_recall_set", "recall_results"):
            items = getattr(ctx, attr, None)
            if items:
                contexts = collect(items)
                if contexts:
                    return contexts
        evidence = getattr(ctx, "evidence_result", None)
        if evidence is not None:
            return collect(getattr(evidence, "top_docs", None))
        return []

    def run_pipeline_samples(
        self,
        pipeline,
        metrics: list[str] | None = None,
        limit: int | None = None,
        sample_ids: list[str] | None = None,
    ) -> dict[str, float]:
        """Run real pipeline outputs through RAGAS.

        Each selected question goes through the actual project pipeline; the
        generated answer and retrieved contexts are what RAGAS evaluates. Failed
        samples are recorded and excluded from the aggregate denominator.
        """
        from core.pipeline_context import RequestContext

        entries = self.select_entries(limit=limit, sample_ids=sample_ids)
        selected_metric_names = self._selected_metric_names(metrics)
        if not selected_metric_names:
            raise ValueError("no valid metrics selected")

        samples: list[PipelineSample] = []
        failures: list[dict] = []
        questions: list[str] = []
        answers: list[str] = []
        contexts: list[list[str]] = []
        ground_truths: list[str] = []

        for index, entry in enumerate(entries):
            sid = self._entry_sample_id(entry, index)
            try:
                ctx = RequestContext(
                    user_input=entry["question"],
                    user_id="eval-user",
                    session_id=f"ragas-{sid}",
                    user_role_mask=0,  # public
                    user_dept_mask=0,
                )
                response = pipeline.process(ctx)
                answer = self._extract_answer(ctx, response)
                retrieved = self._extract_contexts(ctx)
                if not answer.strip():
                    raise RuntimeError("pipeline returned an empty answer")
                samples.append(
                    PipelineSample(
                        sample_id=sid,
                        question=entry["question"],
                        generated_answer=answer,
                        retrieved_contexts=retrieved,
                        reference_answer=entry["answer"],
                        reference_contexts=list(entry["contexts"]),
                        model=getattr(getattr(ctx, "generation_result", None), "model_used", "") or "",
                    )
                )
                questions.append(entry["question"])
                answers.append(answer)
                contexts.append(retrieved)
                ground_truths.append(entry["ground_truth"])
            except Exception as exc:  # noqa: BLE001 - record per-sample failure
                failures.append(
                    {
                        "sample_id": sid,
                        "stage": "pipeline",
                        "exception": type(exc).__name__,
                        "message": str(exc).replace("\n", " ")[:200],
                    }
                )
                logger.warning("pipeline sample %s failed: %s: %s", sid, type(exc).__name__, exc)

        self._last_samples = samples
        self._last_failures = failures
        self._last_sample_ids = [s.sample_id for s in samples]
        self._last_counts = {
            "requested": len(entries),
            "successful": len(samples),
            "failed": len(failures),
            "skipped": len(entries) - len(samples) - len(failures),
        }

        if not samples:
            self._last_available = False
            self._last_warning = "all requested pipeline samples failed"
            self._last_scores = {}
            raise AllSamplesFailedError(failures)

        self._last_answers = answers
        self._last_contexts = contexts
        scores = self._run_ragas(questions, answers, contexts, ground_truths, selected_metric_names)
        self._last_scores = {k: v for k, v in scores.items() if not k.startswith("_")}  # type: ignore[assignment]
        return scores

    def evaluate_with_pipeline(self, pipeline, metrics: list[str] | None = None) -> dict[str, float]:
        """Backward-compatible wrapper around :meth:`run_pipeline_samples`."""
        return self.run_pipeline_samples(pipeline, metrics=metrics)

    # ------------------------------------------------------------------
    # Introspection for the reporter
    # ------------------------------------------------------------------

    @property
    def last_available(self) -> bool:
        return self._last_available

    @property
    def last_warning(self) -> str | None:
        return self._last_warning

    @property
    def last_scores(self) -> dict[str, float]:
        return self._last_scores

    @property
    def last_samples(self) -> list[PipelineSample]:
        return self._last_samples

    @property
    def last_failures(self) -> list[dict]:
        return self._last_failures

    @property
    def last_counts(self) -> dict[str, int]:
        return self._last_counts

    @property
    def last_sample_ids(self) -> list[str]:
        return self._last_sample_ids

    @property
    def evaluate_calls(self) -> int:
        return self._evaluate_calls


def complete_pipeline_report_errors(report: Any, metrics: list[str] | None) -> list[str]:
    """Reject incomplete pipeline evidence rather than treating survivor means as acceptance."""
    expected = list(_ALL_METRIC_NAMES) if metrics is None else metrics
    errors = []
    if report.mode != "pipeline" or report.evaluator_status != "available":
        errors.append("not_available_pipeline")
    requested = report.requested_samples
    if (
        requested <= 0
        or report.successful_samples != requested
        or report.failed_samples
        or report.skipped_samples
        or report.failures
    ):
        errors.append("incomplete_pipeline_samples")
    if (
        len(report.sample_ids) != requested
        or len(set(report.sample_ids)) != requested
        or len(report.per_sample_scores) != requested
        or [row.get("sample_id") for row in report.per_sample_scores] != report.sample_ids
    ):
        errors.append("incomplete_sample_identity")

    def measured(value: Any) -> bool:
        return type(value) in (int, float) and math.isfinite(value)

    if not expected or len(set(expected)) != len(expected) or any(m not in _ALL_METRIC_NAMES for m in expected):
        errors.append("invalid_metric_selection")
    elif any(
        not measured(report.overall_scores.get(metric))
        or any(not measured(row.get(metric)) for row in report.per_sample_scores)
        for metric in expected
    ):
        errors.append("incomplete_metric_observations")
    return errors


def _parse_args(argv: list[str] | None = None):
    import argparse

    parser = argparse.ArgumentParser(description="RAGAS 评估器")
    parser.add_argument("--dataset", type=str, help="JSONL 黄金测试集路径（默认从 config.json 读取）")
    parser.add_argument("--tag", type=str, default="cli", help="评估报告标签（默认: cli）")
    parser.add_argument("--report-dir", type=str, default=None, help="评估报告输出目录（默认从 config.json 读取）")
    parser.add_argument("--pipeline", action="store_true", help="启用真实管线端到端评估模式")
    parser.add_argument("--metrics", type=str, nargs="*", default=None, help="要计算的指标列表")
    parser.add_argument("--limit", type=int, default=None, help="只评估前 N 个样本")
    parser.add_argument("--sample-ids", type=str, nargs="*", default=None, help="只评估指定 sample id")
    parser.add_argument(
        "--require-ragas",
        action="store_true",
        help="要求真实 RAGAS 依赖与 evaluator 凭据；缺失时以非 0 退出且不生成报告",
    )
    parser.add_argument(
        "--require-complete-pipeline",
        action="store_true",
        help="拒绝不完整管线样本或指标报告；必须与 --pipeline 和 --require-ragas 一起使用",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="仅校验严格评测数据和样本选择；不加载 RAG 管线、模型或 evaluator 凭据",
    )
    args = parser.parse_args(argv)
    if args.preflight_only and not args.require_complete_pipeline:
        parser.error("--preflight-only requires --require-complete-pipeline")
    if args.require_complete_pipeline:
        if not (args.pipeline and args.require_ragas):
            parser.error("--require-complete-pipeline requires --pipeline and --require-ragas")
        if args.metrics is not None and (
            not args.metrics
            or len(set(args.metrics)) != len(args.metrics)
            or any(metric not in _ALL_METRIC_NAMES for metric in args.metrics)
        ):
            parser.error("Strict pipeline mode requires distinct supported metrics")
        if args.sample_ids is not None and (not args.sample_ids or len(set(args.sample_ids)) != len(args.sample_ids)):
            parser.error("Strict pipeline mode requires distinct sample IDs")
        if args.limit is not None and (args.limit <= 0 or args.sample_ids is not None):
            parser.error("Strict pipeline mode requires one valid sample selection")
    return args


def main() -> int:
    """CLI 入口。返回进程退出码。"""
    args = _parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    evaluator = None
    if args.require_complete_pipeline:
        try:
            evaluator = RAGASEvaluator(dataset_path=args.dataset, strict_dataset=True)
            selected = evaluator.select_entries(limit=args.limit, sample_ids=args.sample_ids)
        except (OSError, ValueError) as exc:
            logger.warning("strict dataset preflight rejected: %s", type(exc).__name__)
            print(json.dumps({"status": "BLOCKED", "reasons": ["dataset_preflight_failed"]}), file=sys.stderr)
            return 7
        if args.preflight_only:
            print(
                json.dumps(
                    {
                        "status": "PREFLIGHT_PASS",
                        "dataset_samples": len(evaluator.dataset),
                        "selected_samples": len(selected),
                        "model_called": False,
                    }
                )
            )
            return 0

    if args.require_ragas:
        if os.environ.get("RAGAS_SECURITY_APPROVED") != "1":
            print(
                "RAGAS BLOCKED: dependency security admission is missing; run a clean pip-audit on the exact isolated environment before supplying evaluator credentials.",
                file=sys.stderr,
            )
            return 8
        try:
            import datasets  # type: ignore[import-untyped]  # noqa: F401
            import ragas  # type: ignore[import-untyped]  # noqa: F401
        except ImportError as exc:
            print(
                f"RAGAS UNAVAILABLE: {exc}\n真实 RAGAS 依赖未安装（或安全策略禁止安装）；不生成任何质量报告。",
                file=sys.stderr,
            )
            return 2
        try:
            require_evaluator_credentials()
        except EvaluatorConfigError as exc:
            print(f"RAGAS BLOCKED: {exc}\n缺少 evaluator 凭据；不生成任何质量报告。", file=sys.stderr)
            return 3

    if evaluator is None:
        evaluator = RAGASEvaluator(dataset_path=args.dataset)
    logger.info("加载了 %d 条测试数据", len(evaluator.dataset))

    if args.pipeline:
        logger.info("管线评估模式 — 初始化 RAG 管线...")
        try:
            from core.pipeline import OnlineRAGPipeline

            pipeline = OnlineRAGPipeline()
        except Exception as exc:  # noqa: BLE001 - surface init failure as exit code
            logger.error("管线初始化失败: %s。请确保基础设施已就绪。", exc)
            print(json.dumps({"error": f"管线初始化失败: {exc}"}, ensure_ascii=False, indent=2))
            return 4
        try:
            evaluator.run_pipeline_samples(pipeline, metrics=args.metrics, limit=args.limit, sample_ids=args.sample_ids)
        except AllSamplesFailedError as exc:
            logger.error("所有管线样本均失败，评估终止。")
            print(
                json.dumps(
                    {"error": "all pipeline samples failed", "failures": exc.failures}, ensure_ascii=False, indent=2
                )
            )
            return 5
    else:
        evaluator.evaluate(
            metrics=args.metrics, entries=evaluator.select_entries(limit=args.limit, sample_ids=args.sample_ids)
        )

    if not evaluator.last_available:
        print(
            f"RAGAS UNAVAILABLE: {evaluator.last_warning}\n未进行真实评估；不生成质量报告。",
            file=sys.stderr,
        )
        return 3

    from tests.evaluation.ragas_report import RAGASReporter

    reporter = RAGASReporter(evaluator)
    report = reporter.build_report(tag=args.tag, metrics=args.metrics, pipeline_mode=args.pipeline)

    if args.require_complete_pipeline:
        errors = complete_pipeline_report_errors(report, args.metrics)
        if errors:
            print(json.dumps({"status": "BLOCKED", "reasons": errors}), file=sys.stderr)
            return 6

    report_dir = args.report_dir or "./data/eval/reports"
    report_path = reporter.save_report(report, report_dir)

    print("\n" + "=" * 60)
    print(reporter.format_report_markdown(report))
    print("=" * 60)
    print(f"\n📄 报告已保存: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
