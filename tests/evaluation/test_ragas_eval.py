"""
RAGAS 评估器单元测试
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from tests.evaluation.ragas_eval import RAGASEvaluator, format_for_ragas, load_golden_set

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_entries() -> list[dict]:
    return [
        {
            "question": "化妆品的保质期是多久？",
            "answer": "一般未开封 3 年，开封后 6-12 个月。",
            "contexts": ["化妆品保质期通常标注在包装上。"],
            "ground_truth": "一般未开封化妆品保质期为 3 年，开封后建议 6-12 个月内使用完毕。",
        },
        {
            "question": "什么是敏感肌？",
            "answer": "皮肤对外界刺激反应过度的状态。",
            "contexts": ["敏感肌不是皮肤类型，而是皮肤状态。"],
            "ground_truth": "敏感肌是皮肤对外界刺激反应过度的状态，并非固定的皮肤类型。",
        },
    ]


@pytest.fixture
def jsonl_file(sample_entries: list[dict]) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        for entry in sample_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    yield f.name
    os.unlink(f.name)


@pytest.fixture
def empty_jsonl_file() -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        pass  # 空文件
    yield f.name
    os.unlink(f.name)


@pytest.fixture
def ragas_unavailable(monkeypatch):
    """Make the optional RAGAS path deterministic even on full-dependency CI."""
    monkeypatch.setitem(sys.modules, "ragas", None)
    monkeypatch.setitem(sys.modules, "datasets", None)


# ---------------------------------------------------------------------------
# load_golden_set
# ---------------------------------------------------------------------------


class TestLoadGoldenSet:
    def test_load_valid_file(self, jsonl_file: str, sample_entries: list[dict]) -> None:
        """加载有效 JSONL 文件应返回解析后的字典列表。"""
        dataset = load_golden_set(jsonl_file)
        assert len(dataset) == 2
        assert dataset[0]["question"] == sample_entries[0]["question"]
        assert dataset[1]["question"] == sample_entries[1]["question"]

    def test_load_file_not_found(self) -> None:
        """文件不存在应引发 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            load_golden_set("/tmp/non_existent_file.jsonl")

    def test_load_empty_file(self, empty_jsonl_file: str) -> None:
        """空文件应返回空列表。"""
        dataset = load_golden_set(empty_jsonl_file)
        assert dataset == []

    def test_load_whitespace_only_entries(self) -> None:
        """纯空行文件应返回空列表。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write("   \n\n")
        try:
            dataset = load_golden_set(f.name)
            assert dataset == []
        finally:
            os.unlink(f.name)


# ---------------------------------------------------------------------------
# format_for_ragas
# ---------------------------------------------------------------------------


class TestFormatForRagas:
    def test_basic_fields(self, sample_entries: list[dict]) -> None:
        """格式化后的字典应包含 question / answer / contexts / ground_truth。"""
        result = format_for_ragas(sample_entries)
        assert "question" in result
        assert "answer" in result
        assert "contexts" in result
        assert "ground_truth" in result
        assert len(result["question"]) == 2
        assert result["question"][0] == sample_entries[0]["question"]

    def test_custom_answers_override(self, sample_entries: list[dict]) -> None:
        """传入 answers 应覆盖数据集中默认 answer。"""
        custom = ["自定义答案1", "自定义答案2"]
        result = format_for_ragas(sample_entries, answers=custom)
        assert result["answer"] == custom
        assert result["answer"][0] != sample_entries[0]["answer"]

    def test_contexts_as_list_of_strings(self, sample_entries: list[dict]) -> None:
        """contexts 应为每个条目对应一个列表的列表。"""
        result = format_for_ragas(sample_entries)
        assert len(result["contexts"]) == 2
        assert isinstance(result["contexts"][0], list)
        assert isinstance(result["contexts"][0][0], str)

    def test_empty_dataset(self) -> None:
        """空数据集应返回各字段为空列表。"""
        result = format_for_ragas([])
        assert result == {"question": [], "answer": [], "contexts": [], "ground_truth": []}


# ---------------------------------------------------------------------------
# RAGASEvaluator
# ---------------------------------------------------------------------------


class TestRAGASEvaluatorInit:
    def test_init_loads_dataset(self, jsonl_file: str) -> None:
        """初始化时应加载数据集。"""
        evaluator = RAGASEvaluator(jsonl_file)
        assert len(evaluator.dataset) == 2

    def test_init_file_not_found(self) -> None:
        """传入不存在的文件路径应引发 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            RAGASEvaluator("/tmp/not_exist.jsonl")


class TestRAGASEvaluatorEvaluate:
    def test_evaluate_returns_faithfulness_key(self, jsonl_file: str, ragas_unavailable) -> None:
        """即使 RAGAS 未安装，evaluate() 也应返回包含 faithfulness 的字典。"""
        evaluator = RAGASEvaluator(jsonl_file)
        results = evaluator.evaluate()
        assert isinstance(results, dict)
        assert "faithfulness" in results
        assert results["faithfulness"] == 0.0

    def test_evaluate_returns_warning_when_ragas_missing(self, jsonl_file: str, ragas_unavailable) -> None:
        """RAGAS 未安装时 evaluate() 应包含 _warning 键。"""
        evaluator = RAGASEvaluator(jsonl_file)
        results = evaluator.evaluate()
        assert "_warning" in results
        assert "未安装" in results["_warning"]

    def test_evaluate_empty_dataset(self, empty_jsonl_file: str) -> None:
        """空数据集应返回空字典，不崩溃。"""
        evaluator = RAGASEvaluator(empty_jsonl_file)
        results = evaluator.evaluate()
        assert results == {}

    def test_evaluate_invalid_metrics_filtered(self, jsonl_file: str) -> None:
        """传入无效指标应返回空字典。"""
        evaluator = RAGASEvaluator(jsonl_file)
        results = evaluator.evaluate(metrics=["nonexistent_metric"])
        assert results == {}

    def test_evaluate_subset_of_metrics(self, jsonl_file: str, ragas_unavailable) -> None:
        """传入有效指标子集应返回对应指标（RAGAS 未安装时返回 0.0）。"""
        evaluator = RAGASEvaluator(jsonl_file)
        results = evaluator.evaluate(metrics=["faithfulness", "answer_relevancy"])
        assert "faithfulness" in results
        assert "answer_relevancy" in results
        assert results["faithfulness"] == 0.0
        assert results["answer_relevancy"] == 0.0
        # 未请求的指标不应返回
        assert "context_precision" not in results

    def test_evaluate_unknown_metrics_returns_empty(self, jsonl_file: str) -> None:
        """仅传入未知指标应返回空字典。"""
        evaluator = RAGASEvaluator(jsonl_file)
        results = evaluator.evaluate(metrics=["foo", "bar"])
        assert results == {}


class TestRAGASEvaluatorCustomAnswerFn:
    def test_evaluate_with_custom_answer_fn(self, jsonl_file: str, ragas_unavailable) -> None:
        """evaluate_with_custom_answer_fn 应正常工作。"""
        evaluator = RAGASEvaluator(jsonl_file)

        def dummy_answer_fn(question: str, contexts: list[str]) -> str:
            return f"总结: {contexts[0]}" if contexts else "无上下文"

        results = evaluator.evaluate_with_custom_answer_fn(dummy_answer_fn)
        assert isinstance(results, dict)
        assert "faithfulness" in results
        assert results["faithfulness"] == 0.0

    def test_custom_answer_fn_dataset_property(self, jsonl_file: str, ragas_unavailable) -> None:
        """自定义回答函数后数据集属性应保持不变。"""
        evaluator = RAGASEvaluator(jsonl_file)
        original_question = evaluator.dataset[0]["question"]

        def answer_fn(question: str, contexts: list[str]) -> str:
            return f"回答: {question}"

        evaluator.evaluate_with_custom_answer_fn(answer_fn)
        assert evaluator.dataset[0]["question"] == original_question

    def test_evaluate_ragas_installed_path(self, jsonl_file: str, monkeypatch) -> None:
        """模拟 RAGAS 已安装时的 evaluate() 执行路径"""
        import types

        # 创建 mock ragas 模块
        mock_ragas = types.ModuleType("ragas")

        class MockMetric:
            pass

        mock_ragas.evaluate = lambda dataset, metrics: type(
            "Result",
            (),
            {
                "faithfulness": 0.85,
                "answer_relevancy": 0.92,
                "context_precision": 0.78,
                "context_recall": 0.88,
            },
        )()
        sys.modules["ragas"] = mock_ragas

        # 创建 mock ragas.metrics 模块
        mock_metrics = types.ModuleType("ragas.metrics")
        for name in ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]:
            setattr(mock_metrics, name, MockMetric())
        sys.modules["ragas.metrics"] = mock_metrics

        # 创建 mock datasets 模块
        mock_datasets = types.ModuleType("datasets")

        class MockDataset:
            @classmethod
            def from_dict(cls, data):
                return data

        mock_datasets.Dataset = MockDataset
        sys.modules["datasets"] = mock_datasets

        try:
            from tests.evaluation.ragas_eval import RAGASEvaluator

            evaluator = RAGASEvaluator(jsonl_file)
            results = evaluator.evaluate()
            # 由于 mock 的 ragas.evaluate 返回了固定值
            assert "faithfulness" in results
            assert results["faithfulness"] == 0.85
        finally:
            for mod in ["ragas", "ragas.metrics", "datasets"]:
                if mod in sys.modules:
                    del sys.modules[mod]

    def test_main_cli(self, jsonl_file: str, monkeypatch, capsys, ragas_unavailable) -> None:
        """测试 CLI 入口 main() 函数"""
        from unittest.mock import patch

        from tests.evaluation.ragas_eval import main

        # 使用 patch 模拟 sys.argv
        with patch("sys.argv", ["ragas_eval.py", "--dataset", jsonl_file]):
            main()

        captured = capsys.readouterr()
        assert "faithfulness" in captured.out

    def test_evaluate_with_custom_answer_fn_ragas_installed(self, jsonl_file: str, monkeypatch) -> None:
        """RAGAS 已安装时使用自定义 answer_fn 评估"""
        import types

        # 创建 mock ragas 模块
        mock_ragas = types.ModuleType("ragas")

        class MockMetric:
            pass

        mock_ragas.evaluate = lambda dataset, metrics: type(
            "Result",
            (),
            {
                "faithfulness": 0.90,
                "answer_relevancy": 0.95,
                "context_precision": 0.80,
                "context_recall": 0.85,
            },
        )()
        sys.modules["ragas"] = mock_ragas

        mock_metrics = types.ModuleType("ragas.metrics")
        for name in ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]:
            setattr(mock_metrics, name, MockMetric())
        sys.modules["ragas.metrics"] = mock_metrics

        mock_datasets = types.ModuleType("datasets")

        class MockDataset:
            @classmethod
            def from_dict(cls, data):
                return data

        mock_datasets.Dataset = MockDataset
        sys.modules["datasets"] = mock_datasets

        try:
            from tests.evaluation.ragas_eval import RAGASEvaluator

            evaluator = RAGASEvaluator(jsonl_file)

            def answer_fn(question: str, contexts: list[str]) -> str:
                return f"回答: {question}"

            results = evaluator.evaluate_with_custom_answer_fn(answer_fn)
            assert "faithfulness" in results
            assert results["faithfulness"] == 0.90
        finally:
            for mod in ["ragas", "ragas.metrics", "datasets"]:
                if mod in sys.modules:
                    del sys.modules[mod]


# ---------------------------------------------------------------------------
# Integration: Sample golden set
# ---------------------------------------------------------------------------


class TestSampleGoldenSet:
    def test_sample_golden_set_loads(self) -> None:
        """实际样例文件应能成功加载。"""
        sample_path = Path(__file__).parent / "sample_golden_set.jsonl"
        assert sample_path.exists(), f"样例文件未找到: {sample_path}"
        dataset = load_golden_set(str(sample_path))
        assert len(dataset) == 5

    def test_sample_golden_set_all_keys_present(self) -> None:
        """每个条目应包含所有必需的键。"""
        sample_path = Path(__file__).parent / "sample_golden_set.jsonl"
        dataset = load_golden_set(str(sample_path))
        required_keys = {"question", "answer", "contexts", "ground_truth"}
        for entry in dataset:
            assert required_keys.issubset(entry.keys()), f"条目缺少键: {entry.get('question', '?')}"

    def test_sample_golden_set_evaluate(self, ragas_unavailable) -> None:
        """使用样例数据集调用 evaluate() 不应崩溃。"""
        sample_path = Path(__file__).parent / "sample_golden_set.jsonl"
        evaluator = RAGASEvaluator(str(sample_path))
        results = evaluator.evaluate()
        assert isinstance(results, dict)
        assert "faithfulness" in results
