import sys
from unittest.mock import MagicMock

import pytest

# ── torch mock ──────────────────────────────────────────────────────────────
# torch 不在该环境下安装，因此需要 mock 以通过 import。使用 pytest fixture
# save/restore 确保测试执行期间 sys.modules["torch"] 一致且测试间不泄漏状态。
# ---------------------------------------------------------------------------
mock_torch = MagicMock()
mock_torch.cuda.OutOfMemoryError = type("OutOfMemoryError", (RuntimeError,), {})


@pytest.fixture(autouse=True)
def _mock_torch():
    """Mock torch module for all tests in this file (save/restore)."""
    _orig = sys.modules.get("torch")
    sys.modules["torch"] = mock_torch
    yield
    if _orig is not None:
        sys.modules["torch"] = _orig
    else:
        sys.modules.pop("torch", None)


from retrieval.rerank_batch_aggregator import RerankBatchAggregator


def test_cpu_fallback_when_gpu_unavailable():
    """GPU 不可用时应降级到 CPU 推理。"""
    mock_config = {
        "gpu1": {
            "rerank_batch_aggregator": {
                "time_window_ms": 15,
                "max_batch_size": 64,
            }
        }
    }
    aggregator = RerankBatchAggregator(config=mock_config)
    mock_model = MagicMock()
    # First call (GPU path) raises, second call (CPU fallback) succeeds
    mock_model.predict.side_effect = [
        RuntimeError("CUDA out of memory"),
        [0.5, 0.6],
    ]
    pairs = [("query1", "doc1"), ("query2", "doc2")]
    results = aggregator.batch_predict_with_fallback(mock_model, pairs, use_cpu_fallback=True)
    assert len(results) == 2
    assert results == [0.5, 0.6]


def test_gpu_path_no_fallback():
    """GPU 正常时不应触发 fallback。"""
    mock_config = {
        "gpu1": {
            "rerank_batch_aggregator": {
                "time_window_ms": 15,
                "max_batch_size": 64,
            }
        }
    }
    aggregator = RerankBatchAggregator(config=mock_config)
    mock_model = MagicMock()
    mock_model.predict.return_value = [0.9, 0.7]
    pairs = [("query1", "doc1"), ("query2", "doc2")]
    results = aggregator.batch_predict_with_fallback(mock_model, pairs, use_cpu_fallback=False)
    assert results == [0.9, 0.7]


def test_fallback_disabled_raises():
    """use_cpu_fallback=False 时应抛出异常。"""
    mock_config = {
        "gpu1": {
            "rerank_batch_aggregator": {
                "time_window_ms": 15,
                "max_batch_size": 64,
            }
        }
    }
    aggregator = RerankBatchAggregator(config=mock_config)
    mock_model = MagicMock()
    mock_model.predict.side_effect = RuntimeError("CUDA out of memory")
    pairs = [("query1", "doc1")]
    with pytest.raises(RuntimeError):
        aggregator.batch_predict_with_fallback(mock_model, pairs, use_cpu_fallback=False)
