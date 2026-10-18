"""Contract tests for the single Rerank implementation (Issue #101).

Before this change the repository carried two parallel Rerank implementations:

* canonical:          ``retrieval/{bi_encoder,cross_encoder_ensemble,rerank_batch_aggregator}.py``
* duplicate (served): ``retrieval-service/rerank/{bi_encoder,cross_encoder,batch_aggregator}.py``

The two were not mechanically equivalent. The served ``cross_encoder`` and
``batch_aggregator`` had drifted *behind* the canonical ones (no Platt scaling, no
token-truncation, no async aggregation / CPU fallback / SLA-adaptive window), while the
served ``bi_encoder`` was *ahead* by one feature (a dedicated-model path the canonical
file lacked). Because ``retrieval-service`` is a deployed microservice, the divergence was
observable in production.

These tests pin the resolution: the dedicated-model path was ported into the canonical
``bi_encoder``, and the three served modules now re-export the canonical classes. They
exercise the **service** import path (``retrieval_service.rerank.*``) as well as the online
path, and assert that both entry points produce an identical ranking for a fixed input.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DEPLOYMENT_MODE", "development")

from common.models import RecallResult, RerankResult  # noqa: E402

ONLINE_BI = "retrieval.bi_encoder"
SERVED_BI = "retrieval_service.rerank.bi_encoder"


def _make_recall(doc_id: str, content: str = "content", score: float = 1.0) -> RecallResult:
    return RecallResult(doc_id=doc_id, content=content, score=score, source="test")


# ── one implementation per stage ────────────────────────────────────────────


def test_the_served_bi_encoder_is_the_canonical_class():
    from retrieval.bi_encoder import BiEncoderReranker as Canonical
    from retrieval_service.rerank.bi_encoder import BiEncoderReranker as Served

    assert Served is Canonical


def test_the_served_cross_encoder_is_the_canonical_class():
    from retrieval.cross_encoder_ensemble import CrossEncoderEnsemble as Canonical
    from retrieval_service.rerank.cross_encoder import CrossEncoderEnsemble as Served

    assert Served is Canonical


def test_the_served_batch_aggregator_is_the_canonical_class():
    from retrieval.rerank_batch_aggregator import RerankBatchAggregator as Canonical
    from retrieval_service.rerank.batch_aggregator import RerankBatchAggregator as Served

    assert Served is Canonical


def test_the_served_provenance_helper_is_the_canonical_function():
    from retrieval.bi_encoder import _to_rerank_result as canonical
    from retrieval_service.rerank.bi_encoder import _to_rerank_result as served

    assert served is canonical


# ── the dedicated-model path survives, and is reachable from the service ────


def test_the_canonical_bi_encoder_keeps_the_dedicated_model_path():
    from retrieval.bi_encoder import BiEncoderReranker

    assert hasattr(BiEncoderReranker, "dedicated_model_available")
    assert hasattr(BiEncoderReranker, "_rerank_with_dedicated_model")


def test_the_dedicated_model_path_runs_through_the_service_import():
    from retrieval_service.rerank.bi_encoder import BiEncoderReranker

    inst = BiEncoderReranker()
    inst._bi_encoder_model_path = "/models/dedicated"
    inst._dedicated_model = MagicMock()
    inst._dedicated_model.encode.side_effect = lambda texts, **kw: (
        np.array([1.0, 0.0]) if isinstance(texts, str) else np.array([[0.2, 0.0], [1.0, 0.0]])
    )

    results = inst.rerank("query", [_make_recall("a"), _make_recall("b")], top_k=2)

    assert [r.doc_id for r in results] == ["b", "a"]
    assert all(isinstance(r, RerankResult) for r in results)


def test_the_dedicated_model_path_raises_when_no_model_is_configured():
    from retrieval_service.rerank.bi_encoder import BiEncoderReranker

    inst = BiEncoderReranker()
    inst._bi_encoder_model_path = None
    inst._dedicated_model = None

    with pytest.raises(RuntimeError, match="Dedicated BiEncoder model not loaded"):
        inst._rerank_with_dedicated_model("query", [_make_recall("a")], top_k=5)


# ── normal / degraded contracts, exercised through both import paths ────────


@pytest.fixture(params=[ONLINE_BI, SERVED_BI], ids=["online", "served"])
def bi_encoder_module(request):
    return __import__(request.param, fromlist=["BiEncoderReranker"])


def _shared_model_reranker(module, vectors_by_doc):
    inst = module.BiEncoderReranker()
    inst._bi_encoder_model_path = None
    inst._dedicated_model = None
    emb = MagicMock()
    emb.encode_text.return_value.flatten.return_value = np.array([1.0, 0.0])
    emb.encode_texts_batch.side_effect = lambda texts: np.array([vectors_by_doc[text] for text in texts], dtype=float)
    inst._embedding_service = emb
    return inst


def test_shared_model_path_sorts_descending(bi_encoder_module):
    reranker = _shared_model_reranker(
        bi_encoder_module,
        {"close": [1.0, 0.0], "far": [0.0, 1.0]},
    )
    candidates = [_make_recall("far", "far"), _make_recall("close", "close")]

    results = reranker.rerank("q", candidates, top_k=2)

    assert [r.doc_id for r in results] == ["close", "far"]
    assert results[0].bi_score > results[1].bi_score


def test_empty_candidates_return_empty(bi_encoder_module):
    reranker = _shared_model_reranker(bi_encoder_module, {})

    assert reranker.rerank("q", [], top_k=10) == []


def test_encode_failure_degrades_to_zero_scores(bi_encoder_module):
    reranker = _shared_model_reranker(bi_encoder_module, {})
    reranker._embedding_service.encode_text.side_effect = RuntimeError("GPU OOM")
    candidates = [_make_recall("a"), _make_recall("b")]

    results = reranker.rerank("q", candidates, top_k=10)

    assert len(results) == 2
    assert all(r.bi_score == 0.0 for r in results)


# ── determinism: the two entry points agree on a fixed input ────────────────


def test_service_and_online_cross_encoder_produce_identical_ranking():
    """``core/pipeline.py`` and ``retrieval-service/main.py`` must rank identically.

    Both import a ``CrossEncoderEnsemble`` — the online path from
    ``retrieval.cross_encoder_ensemble`` and the service path from
    ``retrieval_service.rerank.cross_encoder``. With a deterministic scorer and fixed
    candidates they must return the same order and the same calibrated scores, or the two
    deployments could rank differently for the same query.
    """
    online = __import__("retrieval.cross_encoder_ensemble", fromlist=["CrossEncoderEnsemble"]).CrossEncoderEnsemble
    served = __import__(
        "retrieval_service.rerank.cross_encoder", fromlist=["CrossEncoderEnsemble"]
    ).CrossEncoderEnsemble

    ce_a_token = object()
    ce_b_token = object()

    class _FixedAggregator:
        def batch_predict(self, model, pairs):
            if model is ce_a_token:
                return [0.9, 0.1, 0.5]
            return [0.8, 0.2, 0.6]

    def _build(ensemble_cls):
        ensemble = ensemble_cls()
        ensemble._ce_a = ce_a_token
        ensemble._ce_b = ce_b_token
        ensemble._batch_aggregator = _FixedAggregator()
        return ensemble

    def _candidates():
        return [
            RerankResult(doc_id="d1", content="c1"),
            RerankResult(doc_id="d2", content="c2"),
            RerankResult(doc_id="d3", content="c3"),
        ]

    online_results = _build(online).rerank("q", _candidates(), top_k=3)
    served_results = _build(served).rerank("q", _candidates(), top_k=3)

    assert [r.doc_id for r in online_results] == [r.doc_id for r in served_results]
    assert [r.ce_score_ensemble for r in online_results] == [r.ce_score_ensemble for r in served_results]


def test_service_and_online_cross_encoder_evaluate_the_same_pairs():
    """The service path must run the same (query, doc) pairs as the online path.

    This is the regression guard for the drift that made the deployed cross-encoder a
    different implementation: an uncalibrated copy that skipped token truncation.
    """
    online = __import__("retrieval.cross_encoder_ensemble", fromlist=["CrossEncoderEnsemble"]).CrossEncoderEnsemble
    served = __import__(
        "retrieval_service.rerank.cross_encoder", fromlist=["CrossEncoderEnsemble"]
    ).CrossEncoderEnsemble

    seen: list[tuple[str, str]] = []

    ce_a_token = object()
    ce_b_token = object()

    class _RecordingAggregator:
        def batch_predict(self, model, pairs):
            if model is ce_a_token:
                seen.append(("A", tuple(pairs)))
            return [0.5] * len(pairs)

    def _build(ensemble_cls):
        ensemble = ensemble_cls()
        ensemble._ce_a = ce_a_token
        ensemble._ce_b = ce_b_token
        ensemble._batch_aggregator = _RecordingAggregator()
        return ensemble

    candidates = [RerankResult(doc_id="d1", content="内容一"), RerankResult(doc_id="d2", content="内容二")]

    _build(online).rerank("查询", [c.model_copy() for c in candidates], top_k=2)
    online_pairs = seen[-1][1]

    _build(served).rerank("查询", [c.model_copy() for c in candidates], top_k=2)
    served_pairs = seen[-1][1]

    assert online_pairs == served_pairs
    assert len(online_pairs) == 2
