"""Exercise the actual production strict paths, without model weights."""
from unittest.mock import MagicMock

import numpy as np
import pytest

from common.models import RecallResult
from retrieval.bi_encoder import BiEncoderReranker
from retrieval.rerank_batch_aggregator import RerankBatchAggregator


def test_strict_biencoder_rejects_dedicated_model_failure():
    reranker = BiEncoderReranker()
    reranker._bi_encoder_model_path = "/fixture/model"
    reranker._dedicated_model = MagicMock()
    reranker._dedicated_model.encode.side_effect = RuntimeError("test inference failure")
    reranker._embedding_service = MagicMock()
    candidates = [RecallResult(doc_id="doc", content="fixture", score=1.0, source="fixture")]
    with pytest.raises(RuntimeError, match="test inference failure"):
        reranker.rerank("q", candidates, strict=True)
    reranker._embedding_service.encode_text.assert_not_called()


def test_strict_biencoder_rejects_degenerate_vectors():
    reranker = BiEncoderReranker()
    reranker._bi_encoder_model_path = None
    reranker._embedding_service = MagicMock()
    reranker._embedding_service.encode_text.return_value = np.zeros(2)
    reranker._embedding_service.encode_texts_batch.return_value = np.array([[1.0, 0.0]])
    candidates = [RecallResult(doc_id="doc", content="fixture", score=1.0, source="fixture")]
    with pytest.raises(ValueError, match="invalid BiEncoder embeddings"):
        reranker.rerank("q", candidates, strict=True)


def test_strict_ce_batch_does_not_turn_inference_failure_into_zero_scores():
    # The synchronous production method needs only these fields; no worker thread.
    aggregator = RerankBatchAggregator.__new__(RerankBatchAggregator)
    aggregator.max_batch_size = 2
    aggregator._lock = MagicMock()
    aggregator._stats = MagicMock()
    model = MagicMock()
    model.predict.side_effect = ValueError("fixture provider failure")
    pairs = [("q", "a"), ("q", "b")]
    assert aggregator.batch_predict(model, pairs) == [0.0, 0.0]
    with pytest.raises(ValueError, match="fixture provider failure"):
        aggregator.batch_predict(model, pairs, strict=True)


def test_strict_ce_batch_rejects_truncated_scores():
    aggregator = RerankBatchAggregator.__new__(RerankBatchAggregator)
    aggregator.max_batch_size = 2
    aggregator._lock = MagicMock()
    aggregator._stats = MagicMock()
    model = MagicMock()
    model.predict.return_value = [0.5]
    with pytest.raises(ValueError, match="incomplete CrossEncoder batch"):
        aggregator.batch_predict(model, [("q", "a"), ("q", "b")], strict=True)
