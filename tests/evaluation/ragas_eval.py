"""
RAGAS 评估器

独立离线评估模块，用于基于 RAGAS 框架评估 RAG 系统质量。
支持 CLI 接口和编程式调用。

如果 RAGAS 未安装，evaluate() 会优雅降级返回全零分数。
"""

import json
import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)


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
    with open(path, "r", encoding="utf-8") as f:
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


def format_for_ragas(
    dataset: list[dict],
    answers: list[str] | None = None,
) -> dict[str, list]:
    """将内部数据集格式化为 RAGAS 评估所需的格式。

    Args:
        dataset: 包含 question / answer / contexts / ground_truth 的字典列表。
        answers: 可选的答案列表，用于覆盖 dataset 中的 answer 字段
                 （例如传入 RAG 系统生成的回答）。

    Returns:
        包含 "question", "answer", "contexts", "ground_truth" 键的字典，
         每个键对应一个列表。
    """
    result: dict[str, list] = {
        "question": [item["question"] for item in dataset],
        "answer": answers if answers is not None else [item["answer"] for item in dataset],
        "contexts": [item["contexts"] for item in dataset],
        "ground_truth": [item["ground_truth"] for item in dataset],
    }
    return result


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
        """初始化评估器。

        Args:
            dataset_path: JSONL 黄金测试集路径。为 None 时从 config 读取。
            metrics: 默认指标列表。为 None 时从 config 读取。
        """
        try:
            from common.config import get_config
            cfg = get_config().ragas
            self._dataset_path = dataset_path or cfg.dataset_path
            self._default_metrics = metrics or list(cfg.default_metrics)
        except Exception:
            self._dataset_path = dataset_path or "tests/evaluation/golden_set.jsonl"
            self._default_metrics = metrics or [
                "faithfulness", "answer_relevancy", "context_precision", "context_recall",
            ]
        self._dataset = load_golden_set(self._dataset_path)
        self._last_raw_result: Any = None  # 存储 RAGAS Result 对象（用于逐条评分提取）

    @property
    def dataset(self) -> list[dict]:
        """获取加载的数据集。"""
        return self._dataset

    def evaluate(
        self,
        answers: list[str] | None = None,
        metrics: list[str] | None = None,
    ) -> dict[str, float]:
        """运行 RAGAS 评估。

        如果 RAGAS / datasets 未安装，会优雅地返回全零分数，
        并在结果中包含一个 _warning 键说明原因。

        Args:
            answers: 可选的 RAG 系统生成答案列表。为 None 时使用
                     数据集中 ground_truth 作为回答（用于评估数据集本身）。
            metrics: 要计算的指标列表。为 None 时使用全部指标：
                     ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]。
                     未知指标会被静默过滤掉。

        Returns:
            指标名称到分数的字典。例如:
                {"faithfulness": 0.85, "answer_relevancy": 0.92, ...}
        """
        if not self._dataset:
            logger.warning("数据集为空，跳过评估")
            return {}

        if answers is not None and len(answers) != len(self._dataset):
            raise ValueError(
                f"answers 长度 ({len(answers)}) 与数据集 ({len(self._dataset)}) 不匹配"
            )

        _ALL_METRIC_NAMES = [
            "faithfulness",
            "answer_relevancy",
            "context_precision",
            "context_recall",
        ]

        # 提前过滤指标，使降级路径也遵循 metrics 参数
        if metrics is not None:
            selected_metric_names = [m for m in metrics if m in _ALL_METRIC_NAMES]
        else:
            selected_metric_names = list(_ALL_METRIC_NAMES)

        if not selected_metric_names:
            logger.warning("没有有效的指标可供评估。")
            return {}

        try:
            from ragas import evaluate as ragas_evaluate  # type: ignore[import-untyped]
            from ragas.metrics import (                   # type: ignore[import-untyped]
                answer_relevancy,
                context_precision,
                context_recall,
                faithfulness,
            )
            from datasets import Dataset                 # type: ignore[import-untyped]
        except ImportError as exc:
            msg = f"RAGAS 或 datasets 未安装: {exc}。返回默认零值。"
            logger.warning(msg)
            result: dict[str, float] = {
                name: 0.0 for name in selected_metric_names
            }
            result["_warning"] = msg  # type: ignore[assignment]
            return result

        _ALL_METRICS = {
            "faithfulness": faithfulness,
            "answer_relevancy": answer_relevancy,
            "context_precision": context_precision,
            "context_recall": context_recall,
        }

        selected_metrics = [_ALL_METRICS[m] for m in selected_metric_names]

        formatted = format_for_ragas(self._dataset, answers=answers)
        dataset = Dataset.from_dict(formatted)

        raw = ragas_evaluate(dataset=dataset, metrics=selected_metrics)
        self._last_raw_result = raw
        scores = {metric: getattr(raw, metric, 0.0) for metric in selected_metric_names}
        return scores

    def evaluate_with_custom_answer_fn(
        self,
        answer_fn: Callable[[str, list[str]], str],
        metrics: list[str] | None = None,
    ) -> dict[str, float]:
        """使用自定义答案函数生成回答后运行 RAGAS 评估。

        Args:
            answer_fn: 接收 (question, contexts) 并返回答案字符串的函数。
            metrics: 要计算的指标列表。为 None 时使用全部指标。

        Returns:
            指标名称到分数的字典。
        """
        answers = [
            answer_fn(item["question"], item["contexts"])
            for item in self._dataset
        ]
        return self.evaluate(answers=answers, metrics=metrics)

    def evaluate_with_pipeline(
        self,
        pipeline,
        metrics: list[str] | None = None,
    ) -> dict[str, float]:
        """使用 RAG 管线的实际输出进行端到端评估。

        遍历数据集中的每个问题，通过管线生成答案，然后用管线答案评估。
        注意：管线需提前初始化并连接好 vLLM / Qdrant / ES / Redis 等基础设施。

        Args:
            pipeline: 实现了 process(context) → {"answer": str} 的管线对象。
            metrics: 要计算的指标列表。

        Returns:
            指标名称到分数的字典。
        """
        pipeline_answers: list[str] = []

        for item in self._dataset:
            try:
                from core.pipeline_context import RequestContext

                session_id = f"eval_{hash(item['question'])}"
                ctx = RequestContext(
                    user_input=item["question"],
                    user_id="eval-user",
                    session_id=session_id,
                    user_role_mask=0,  # public
                    user_dept_mask=0,
                )
                # 管线 process() 返回包含 answer 的响应
                response = pipeline.process(ctx)
                if isinstance(response, dict):
                    answer = response.get("answer", "")
                else:
                    answer = getattr(response, "answer", "")
                pipeline_answers.append(answer or "")
            except Exception as exc:
                logger.warning("管线处理问题 '%s' 失败: %s", item.get("question", "?"), exc)
                pipeline_answers.append("")

        if not pipeline_answers:
            logger.warning("管线未生成任何答案，返回空结果")
            return {}

        return self.evaluate(answers=pipeline_answers, metrics=metrics)


def main() -> None:
    """CLI 入口：python -m tests.evaluation.ragas_eval --dataset <path>"""
    import argparse

    parser = argparse.ArgumentParser(description="RAGAS 评估器")
    parser.add_argument(
        "--dataset",
        type=str,
        help="JSONL 黄金测试集路径（默认从 config.json 读取）",
    )
    parser.add_argument(
        "--tag",
        type=str,
        default="cli",
        help="评估报告标签（默认: cli）",
    )
    parser.add_argument(
        "--report-dir",
        type=str,
        default=None,
        help="评估报告输出目录（默认从 config.json 读取）",
    )
    parser.add_argument(
        "--pipeline",
        action="store_true",
        help="启用管线端到端评估模式",
    )
    parser.add_argument(
        "--metrics",
        type=str,
        nargs="*",
        default=None,
        help="要计算的指标列表（默认行为从 config.json 读取）",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    evaluator = RAGASEvaluator(dataset_path=args.dataset)
    logger.info("加载了 %d 条测试数据", len(evaluator.dataset))

    if args.pipeline:
        logger.info("管线评估模式 — 初始化 RAG 管线...")
        try:
            from core.pipeline import OnlineRAGPipeline

            pipeline = OnlineRAGPipeline()
        except Exception as exc:
            logger.error("管线初始化失败: %s。请确保基础设施已就绪。", exc)
            print(json.dumps({"error": f"管线初始化失败: {exc}"}, ensure_ascii=False, indent=2))
            return
        results = evaluator.evaluate_with_pipeline(pipeline, metrics=args.metrics)
    else:
        results = evaluator.evaluate(metrics=args.metrics)

    # 尝试生成并保存报告
    report_path = None
    try:
        from tests.evaluation.ragas_report import RAGASReporter

        reporter = RAGASReporter(evaluator)
        report = reporter.run_and_report(tag=args.tag, metrics=args.metrics)

        if args.report_dir:
            report_path = reporter.save_report(report, args.report_dir)
        else:
            report_path = reporter.save_report(report, "./data/eval/reports")

        # 打印 Markdown 报告
        print("\n" + "=" * 60)
        print(reporter.format_report_markdown(report))
        print("=" * 60)
    except Exception as exc:
        logger.warning("报告生成失败（不影响评估结果）: %s", exc)
        # 降级：直接打印 JSON 结果
        print(json.dumps(results, ensure_ascii=False, indent=2))

    if report_path:
        print(f"\n📄 报告已保存: {report_path}")


if __name__ == "__main__":
    main()
