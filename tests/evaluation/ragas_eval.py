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
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            dataset.append(json.loads(stripped))
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
        "answer": answers or [item["answer"] for item in dataset],
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

    def __init__(self, dataset_path: str) -> None:
        """初始化评估器。

        Args:
            dataset_path: JSONL 黄金测试集路径。
        """
        self._dataset_path = dataset_path
        self._dataset = load_golden_set(dataset_path)

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
        scores = {metric: getattr(raw, metric, 0.0) for metric in _ALL_METRICS}
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


def main() -> None:
    """CLI 入口：python -m tests.evaluation.ragas_eval --dataset <path>"""
    import argparse

    parser = argparse.ArgumentParser(description="RAGAS 评估器")
    parser.add_argument(
        "--dataset",
        type=str,
        required=True,
        help="JSONL 黄金测试集路径",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    evaluator = RAGASEvaluator(args.dataset)
    logger.info("加载了 %d 条测试数据", len(evaluator.dataset))
    results = evaluator.evaluate()
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
