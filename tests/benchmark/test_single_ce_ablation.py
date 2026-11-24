"""Single-CE architecture comparisons are strict benchmark-only paths."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from benchmarks import backends
from benchmarks.executors import BenchmarkUnavailable, SingleCrossEncoderReranker, build_executor
from benchmarks.strict_retrieval_suite import main


@pytest.mark.parametrize("config_name,model_key", backends.SINGLE_CE_CONFIGS.items())
def test_single_ce_wiring(config_name, model_key):
    assert set(backends.rerank_model_paths(config_name)) == {"biencoder", model_key}
    assert model_key in backends.REQUIRED_BACKENDS[config_name]
    assert "crossencoder" not in backends.REQUIRED_BACKENDS[config_name]
    assert build_executor(config_name).name == config_name


@pytest.mark.parametrize("model_key", ["cross_encoder_a", "cross_encoder_b"])
def test_single_ce_uses_one_strict_batch_and_rejects_incomplete_scores(model_key):
    reranker = SingleCrossEncoderReranker.__new__(SingleCrossEncoderReranker)
    reranker.model_key = model_key
    reranker.model = Mock()
    reranker.aggregator = Mock()
    reranker._truncate = lambda text: text
    candidates = [SimpleNamespace(content="a"), SimpleNamespace(content="b")]
    ctx = SimpleNamespace()
    reranker.aggregator.batch_predict.return_value = [0.1, 0.9]
    assert reranker.rerank("q", candidates, 1, ctx=ctx, strict=True) == [candidates[1]]
    assert ctx.ce_stage_provenance == model_key
    reranker.aggregator.batch_predict.assert_called_once_with(
        reranker.model, [("q", "a"), ("q", "b")], strict=True
    )
    reranker.aggregator.batch_predict.return_value = [0.9]
    with pytest.raises(BenchmarkUnavailable, match="invalid single CrossEncoder"):
        reranker.rerank("q", candidates, 1, ctx=SimpleNamespace(), strict=True)


def test_seven_arm_suite_keeps_publication_gates(monkeypatch):
    captured = []

    def blocked(args):
        captured.extend(args)
        return 2

    monkeypatch.setattr("benchmarks.retrieval_benchmark.main", blocked)
    assert main(["--dataset", "reviewed.jsonl", "--output-dir", "run", "--include-single-ce"]) == 2
    configs = captured[captured.index("--configs") + 1].split(",")
    assert len(configs) == 7
    assert set(backends.SINGLE_CE_CONFIGS).issubset(configs)
    assert "--require-publishable" in captured
    assert "--require-complete-suite" in captured
