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


def load_golden_set(path: str) -> list[dict]:
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
                dataset.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                preview = stripped[:80] + "..." if len(stripped) > 80 else stripped
                logger.warning("跳过无效 JSON 行 (line %d): %s — 内容: %s", line_num, exc, preview)
    return dataset


def sample_id_for(entry: dict, index: int) -> str:
    """Stable sample id for a golden-set entry."""
    explicit = entry.get("sample_id") or entry.get("id")
    if explicit:
        return str(explicit)
    digest = hashlib.sha256(str(entry.get("question", "")).encode("utf-8")).hexdigest()[:8]
    return f"{index:04d}-{digest}"


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
    ) -> None:
        try:
            from common.config import get_config

            cfg = get_config().ragas
            self._dataset_path = dataset_path or cfg.dataset_path
            self._default_metrics = metrics or list(cfg.default_metrics)
        except Exception:
            self._dataset_path = dataset_path or "tests/evaluation/golden_set.jsonl"
            self._default_metrics = metrics or list(_ALL_METRIC_NAMES)
        self._dataset = load_golden_set(self._dataset_path)
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
        self._last_available = True
        self._last_warning = None
        return {metric: getattr(raw, metric, 0.0) for metric in selected_metric_names}

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
        self._last_sample_ids = [sample_id_for(entry, i) for i, entry in enumerate(active)]
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
            sid = sample_id_for(entry, index)
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
    return parser.parse_args(argv)


def main() -> int:
    """CLI 入口。返回进程退出码。"""
    args = _parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.require_ragas:
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

    report_dir = args.report_dir or "./data/eval/reports"
    report_path = reporter.save_report(report, report_dir)

    print("\n" + "=" * 60)
    print(reporter.format_report_markdown(report))
    print("=" * 60)
    print(f"\n📄 报告已保存: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
