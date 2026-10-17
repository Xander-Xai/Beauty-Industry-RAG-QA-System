"""CLIP recall path: vector space, structured outcomes, and a real latency bound.

Three defects are covered here. All three let the visual path look healthy while
contributing nothing, which is how an unconditional ``AttributeError`` in the encoder
went unnoticed behind 3094 passing tests.

1. ``core/pipeline._should_use_clip_sync`` compared a **768-d BGE** query vector against a
   **512-d CLIP** image centroid. ``np.dot`` raised ``ValueError`` on every call, the
   surrounding ``except Exception`` logged at ``debug``, and the third PRD §4.5
   discriminator component was permanently ``0.0``.
2. ``_recall_clip`` returned ``[]`` for both a healthy zero-hit recall and every failure
   mode, so a broken path was indistinguishable from an empty collection. The aggregate
   degraded-path warning only fired when the whole request returned fewer than 10
   documents, so a failure on an otherwise healthy request was invisible.
3. The ``clip_sync.timeout_ms`` budget did not bound anything: the per-call
   ``ThreadPoolExecutor`` ran ``shutdown(wait=True)`` on context exit, so a timed-out
   attempt still blocked until the CLIP load finished (measured at ~1502 ms against a
   120 ms budget), and a fresh ``EmbeddingService`` per request re-read the weights every
   time.

These assert contract and shape with deterministic doubles. Real CLIP recall quality
still requires real weights and stays ``VAL-MODEL-002`` / issue #8; nothing here is a
model-quality or latency claim.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from core.pipeline import OnlineRAGPipeline
from core.pipeline_context import QueryRewriteResult, RecallResult
from models.embedding_service import clip_cosine_similarity
from retrieval.clip_path_status import ClipPathOutcome, ClipPathStatus
from retrieval.parallel_recall import ParallelRecallManager

BGE_DIM = 768
CLIP_DIM = 512


def _unit(dim: int, sign: float = 1.0) -> np.ndarray:
    return np.full(dim, sign / np.sqrt(dim), dtype=np.float32)


class _CentroidService:
    """Encoding service exposing both vector spaces, as production does.

    ``encode_text`` is BGE at 768-d and ``encode_text_clip`` is CLIP at 512-d; the image
    centroid is CLIP at 512-d. Keeping the real dimensions here is the point: a test that
    made both sides 512-d would not have caught the original defect.
    """

    def __init__(self, centroid=None, centroid_status="ok", align=1.0, clip_dim=CLIP_DIM):
        self.centroid = centroid
        self.centroid_status = centroid_status
        self.align = align
        self.clip_dim = clip_dim

    def image_centroid_with_status(self):
        return self.centroid, self.centroid_status

    def get_image_centroid(self):
        return self.centroid

    def encode_text(self, text):  # BGE — deliberately the wrong space for the centroid
        return _unit(BGE_DIM)

    def encode_text_clip(self, text):  # CLIP — same joint space as the image vectors
        return _unit(self.clip_dim, self.align)


def _pipeline_with(service) -> OnlineRAGPipeline:
    pipeline = OnlineRAGPipeline()
    pipeline._embedding_service = service
    return pipeline


def _rewrite(query: str, intent: str = "general", business: str = "general") -> QueryRewriteResult:
    return QueryRewriteResult(
        rewritten_query=query,
        business_type=business,
        intent=intent,
        requires_context=True,
    )


# ───────────────────────── A1-1 vector space ─────────────────────────


class TestClipCentroidVectorSpace:
    """The centroid component must compare two CLIP-space vectors."""

    def test_aligned_centroid_lets_the_third_component_decide_routing(self):
        """A visually-related query with no visual keyword must still route on the centroid.

        This is the deterministic proof that the third component participates. Before the
        fix the dot product raised on every call, so the score was always 0.0 and this
        query could only ever route by keyword or intent.
        """
        pipeline = _pipeline_with(_CentroidService(centroid=_unit(CLIP_DIM), align=1.0))

        use_clip, top_k = pipeline._should_use_clip_sync(_rewrite("完全不含视觉关键词的询问"))

        assert use_clip is True
        assert top_k == 50, "a cosine of 1.0 must reach the full-sync tier on its own"

    def test_anti_aligned_centroid_does_not_route(self):
        pipeline = _pipeline_with(_CentroidService(centroid=_unit(CLIP_DIM), align=-1.0))

        use_clip, top_k = pipeline._should_use_clip_sync(_rewrite("你好世界"))

        assert (use_clip, top_k) == (False, 0)

    def test_keyword_routing_is_unaffected_by_the_centroid(self):
        pipeline = _pipeline_with(_CentroidService(centroid=None, centroid_status="no_image_vectors"))

        use_clip, _ = pipeline._should_use_clip_sync(_rewrite("请扫描标签上的成分表"))

        assert use_clip is True

    def test_intent_routing_is_unaffected_by_the_centroid(self):
        pipeline = _pipeline_with(_CentroidService(centroid=None, centroid_status="no_image_vectors"))

        use_clip, top_k = pipeline._should_use_clip_sync(_rewrite("烟酰胺浓度", "ingredient", "ingredient"))

        assert use_clip is True
        assert top_k == 20

    def test_empty_image_collection_is_not_a_failure(self):
        """No image vectors is a legitimate state; it must not masquerade as an error."""
        pipeline = _pipeline_with(_CentroidService(centroid=None, centroid_status="no_image_vectors"))

        use_clip, top_k = pipeline._should_use_clip_sync(_rewrite("你好世界"))

        assert (use_clip, top_k) == (False, 0)

    def test_dimension_mismatch_is_refused_not_coerced(self, caplog):
        """A 768-d query vector against a 512-d centroid must not be padded or truncated."""
        pipeline = _pipeline_with(
            _CentroidService(centroid=_unit(CLIP_DIM), clip_dim=BGE_DIM),
        )

        with caplog.at_level("WARNING"):
            use_clip, top_k = pipeline._should_use_clip_sync(_rewrite("你好世界"))

        assert (use_clip, top_k) == (False, 0)
        assert "质心分量不可计算" in caplog.text

    def test_zero_norm_centroid_is_refused(self, caplog):
        pipeline = _pipeline_with(_CentroidService(centroid=np.zeros(CLIP_DIM, dtype=np.float32)))

        with caplog.at_level("WARNING"):
            pipeline._should_use_clip_sync(_rewrite("你好世界"))

        assert "质心分量不可计算" in caplog.text

    def test_encoder_failure_is_distinguished_from_no_match(self, caplog):
        """An unavailable encoder must be reported, not folded into a plausible 0.0."""

        class _BrokenEncoder(_CentroidService):
            def encode_text_clip(self, text):
                raise RuntimeError("no such model directory")

        pipeline = _pipeline_with(_BrokenEncoder(centroid=_unit(CLIP_DIM)))

        with caplog.at_level("WARNING"):
            pipeline._should_use_clip_sync(_rewrite("你好世界"))

        assert "CLIP 查询编码失败" in caplog.text

    def test_pipeline_survives_a_service_without_the_new_method(self):
        """A substituted service must not turn a routing decision into an exception."""

        class _LegacyService(_CentroidService):
            """A service written before the structured centroid accessor existed."""

            image_centroid_with_status = property(lambda self: (_ for _ in ()).throw(AttributeError("no such method")))

        pipeline = _pipeline_with(_LegacyService(centroid=_unit(CLIP_DIM)))

        assert pipeline._should_use_clip_sync(_rewrite("你好世界")) == (False, 0)


class TestClipCosineSimilarity:
    def test_rejects_mismatched_lengths(self):
        with pytest.raises(ValueError, match="vector length mismatch"):
            clip_cosine_similarity(_unit(BGE_DIM), _unit(CLIP_DIM))

    def test_rejects_zero_norm(self):
        with pytest.raises(ValueError, match="zero-norm"):
            clip_cosine_similarity(np.zeros(CLIP_DIM, dtype=np.float32), _unit(CLIP_DIM))

    def test_rejects_non_finite_norm(self):
        with pytest.raises(ValueError, match="non-finite"):
            clip_cosine_similarity(np.full(CLIP_DIM, np.inf, dtype=np.float32), _unit(CLIP_DIM))

    def test_identical_directions_score_one(self):
        assert clip_cosine_similarity(_unit(CLIP_DIM), _unit(CLIP_DIM)) == pytest.approx(1.0, abs=1e-6)


# ───────────────────────── A1-2 structured outcomes ─────────────────────────


class _ClipService:
    def __init__(self, hits=1, fail=None, delay=0.0):
        self.hits = hits
        self.fail = fail
        self.delay = delay
        self.encode_calls = 0

    def encode_text_clip(self, query):
        self.encode_calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail is not None:
            raise self.fail
        return _unit(CLIP_DIM)

    def search(self):
        return [
            {
                "doc_id": f"d{i}",
                "content": "c",
                "score": 0.9,
                "image_uri": "u.png",
                "metadata": {"role_mask": 3, "dept_mask": 3},
            }
            for i in range(self.hits)
        ]


class _StubClipRetriever:
    def __init__(self, service):
        self._service = service
        self.search_calls = 0

    def search(self, clip_embedding, qdrant_filter=None, top_k=20):
        self.search_calls += 1
        return self._service.search()


def _manager(service) -> ParallelRecallManager:
    manager = ParallelRecallManager()
    manager._clip_retriever = _StubClipRetriever(service)
    manager._clip_embedding_service = service
    return manager


class TestClipPathStructuredOutcomes:
    def _run(self, **kwargs):
        service = _ClipService(**kwargs)
        manager = _manager(service)
        results = manager._recall_clip("包装外观", None, 5)
        return results, manager.clip_path_status()

    def test_success_is_reported_as_ok(self):
        results, status = self._run(hits=2)

        assert len(results) == 2
        assert status.outcome is ClipPathOutcome.OK
        assert status.hit_count == 2
        assert not status.is_failure

    def test_genuine_zero_hits_is_a_success_not_a_failure(self):
        """The distinction that was missing: an empty collection is not an outage."""
        results, status = self._run(hits=0)

        assert results == []
        assert status.outcome is ClipPathOutcome.EMPTY
        assert status.is_failure is False

    def test_model_unavailable_is_distinguished(self):
        results, status = self._run(fail=FileNotFoundError("clip weights"))

        assert results == []
        assert status.outcome is ClipPathOutcome.MODEL_UNAVAILABLE
        assert status.detail == "missing_weights"
        assert status.is_failure

    def test_transformers_missing_is_distinguished(self):
        results, status = self._run(fail=ImportError("no transformers"))

        assert status.outcome is ClipPathOutcome.MODEL_UNAVAILABLE
        assert status.detail == "not_installed"

    def test_dimension_mismatch_is_distinguished(self):
        results, status = self._run(fail=ValueError("Wrong input shape for vector"))

        assert status.outcome is ClipPathOutcome.DIMENSION_MISMATCH
        assert status.detail == "query_vs_collection"

    def test_qdrant_failure_is_distinguished(self):
        from qdrant_client.http.exceptions import UnexpectedResponse

        exc = UnexpectedResponse(503, "unavailable", b"", {})
        results, status = self._run(fail=exc)

        assert status.outcome is ClipPathOutcome.QDRANT_FAILED
        assert status.detail == "qdrant_error"

    def test_unrecognised_failure_is_never_reported_as_a_healthy_empty(self):
        results, status = self._run(fail=KeyError("surprise"))

        assert status.outcome is ClipPathOutcome.UNKNOWN
        assert status.is_failure

    def test_failure_detail_never_leaks_the_query_or_document_ids(self, caplog):
        secret_query = "机密配方-9f3a"
        service = _ClipService(fail=ValueError(f"bad vector for {secret_query}"))
        manager = _manager(service)

        with caplog.at_level("DEBUG"):
            manager._recall_clip(secret_query, None, 5)

        status = manager.clip_path_status()
        assert secret_query not in status.detail
        assert secret_query not in status.series_suffix()
        assert secret_query not in caplog.text

    def test_returns_recall_results_so_fusion_contract_is_unchanged(self):
        results, _ = self._run(hits=1)

        assert isinstance(results, list)
        assert all(isinstance(r, RecallResult) for r in results)
        assert results[0].source == "clip_visual"
        assert results[0].metadata["image_uri"] == "u.png"

    def test_permission_metadata_survives_to_the_post_filter(self):
        """RBAC reads role/dept off the result, so the visual path must carry them.

        The stored masks (3/3) must reach the result at all: a path that dropped them
        would leave the post-filter unable to make a decision. A caller whose masks share
        no bit with the document (2/2) is then denied, and a caller matching them is
        allowed.
        """
        results, _ = self._run(hits=1)
        manager = ParallelRecallManager()

        assert results[0].metadata["role_mask"] == 3
        assert results[0].metadata["dept_mask"] == 3

        denied = manager._apply_rbac_filter(results, 4, 4)  # 3 & 4 == 0, no shared bit
        allowed = manager._apply_rbac_filter(results, 3, 3)

        assert [r.doc_id for r in denied] == []
        assert [r.doc_id for r in allowed] == ["d0"]


class TestClipPathStatusVocabulary:
    def test_every_outcome_is_a_stable_lowercase_token(self):
        for outcome in ClipPathOutcome:
            assert outcome.value == outcome.value.lower()
            assert outcome.value.replace("_", "").isalnum()

    def test_only_ok_empty_and_unset_are_non_failures(self):
        non_failures = {o for o in ClipPathOutcome if not o.is_failure}
        assert non_failures == {ClipPathOutcome.OK, ClipPathOutcome.EMPTY, ClipPathOutcome.UNSET}

    def test_series_suffix_appends_detail_only_for_failures(self):
        failure = ClipPathStatus(ClipPathOutcome.TIMEOUT, detail="cold_start")
        success = ClipPathStatus(ClipPathOutcome.OK, hit_count=3, detail="ignored")

        assert failure.series_suffix() == "clip.path.outcome.timeout.cold_start"
        assert success.series_suffix() == "clip.path.outcome.ok"

    def test_negative_hit_count_is_rejected(self):
        with pytest.raises(ValueError):
            ClipPathStatus(ClipPathOutcome.OK, hit_count=-1)


class TestDegradationReporting:
    """A CLIP failure must be reported even when the other channels answer well."""

    def _manager_with_paths(self, clip_results, dense_count=20):
        manager = ParallelRecallManager()
        manager._recall_dense = lambda *a, **k: [
            RecallResult(doc_id=f"t{i}", content="c", score=0.5, source="dense_bge") for i in range(dense_count)
        ]
        manager._recall_bm25 = lambda *a, **k: []
        manager._recall_rewrite_variants = lambda *a, **k: []
        manager._recall_clip = lambda *a, **k: list(clip_results)
        return manager

    def test_healthy_clip_path_is_not_reported_as_degraded(self, caplog):
        manager = self._manager_with_paths(
            [RecallResult(doc_id="v1", content="c", score=0.9, source="clip_visual")],
        )
        status = ClipPathStatus(ClipPathOutcome.OK, hit_count=1)
        manager._set_clip_path_status(status)

        with caplog.at_level("WARNING"):
            manager.execute(
                "q",
                _unit(BGE_DIM),
                3,
                3,
                use_clip=True,
                top_k_per_path={"dense_bge": {"top_k": 5}, "clip_visual": {"top_k": 5}},
            )

        assert "CLIP 视觉召回降级" not in caplog.text

    def test_failed_clip_path_is_reported_even_with_many_dense_hits(self, caplog):
        manager = self._manager_with_paths([], dense_count=20)
        manager._set_clip_path_status(
            ClipPathStatus(ClipPathOutcome.MODEL_UNAVAILABLE, detail="missing_weights"),
        )

        with caplog.at_level("WARNING"):
            manager.execute(
                "q",
                _unit(BGE_DIM),
                3,
                3,
                use_clip=True,
                top_k_per_path={"dense_bge": {"top_k": 5}, "clip_visual": {"top_k": 5}},
            )

        assert "CLIP 视觉召回降级" in caplog.text
        assert "model_unavailable" in caplog.text

    def test_zero_hit_clip_path_is_not_reported_as_a_failure(self, caplog):
        manager = self._manager_with_paths([], dense_count=20)
        manager._set_clip_path_status(ClipPathStatus(ClipPathOutcome.EMPTY, hit_count=0))

        with caplog.at_level("WARNING"):
            manager.execute(
                "q",
                _unit(BGE_DIM),
                3,
                3,
                use_clip=True,
                top_k_per_path={"dense_bge": {"top_k": 5}, "clip_visual": {"top_k": 5}},
            )

        assert "CLIP 视觉召回降级" not in caplog.text


class TestClipPathMetrics:
    """The outcomes must be visible in the existing collector, not just in a log line."""

    @staticmethod
    def _run(**kwargs):
        service = _ClipService(**kwargs)
        manager = _manager(service)
        return manager._recall_clip("包装外观", None, 5)

    def _counters(self, fn) -> dict:
        """Per-call deltas, so a series touched by an earlier test does not leak in."""
        from common.tracing import get_metrics

        before = dict(get_metrics()._counters)
        fn()
        after = get_metrics()._counters
        deltas = {k: after[k] - before.get(k, 0) for k in after if k.startswith("clip.path")}
        return {k: v for k, v in deltas.items() if v}

    def test_a_zero_hit_recall_is_not_counted_as_degraded(self):
        counters = self._counters(lambda: self._run(hits=0))

        assert counters["clip.path.outcome.empty"] == 1
        assert counters.get("clip.path.degraded", 0) == 0

    def test_a_failed_recall_is_counted_as_degraded(self):
        counters = self._counters(lambda: self._run(fail=FileNotFoundError("w")))

        assert counters["clip.path.attempts"] == 1
        assert counters["clip.path.degraded"] == 1
        assert counters["clip.path.outcome.model_unavailable.missing_weights"] == 1

    def test_every_attempt_yields_exactly_one_outcome(self):
        counters = self._counters(lambda: [self._run(hits=1), self._run(hits=0), self._run(fail=OSError("w"))])

        outcome_series = [k for k in counters if k.startswith("clip.path.outcome.")]
        assert len(outcome_series) == 3
        assert sum(counters[k] for k in outcome_series) == counters["clip.path.attempts"]

    def test_authorization_filtered_is_counted_apart_from_degradation(self):
        from common.tracing import get_metrics

        before = dict(get_metrics()._counters)
        ParallelRecallManager._record_clip_authorization_filtered(4)
        after = get_metrics()._counters

        assert after["clip.path.authorization_filtered"] - before.get("clip.path.authorization_filtered", 0) == 1
        assert after.get("clip.path.degraded", 0) == before.get("clip.path.degraded", 0)


# ───────────────────────── A1-3 latency bound ─────────────────────────


class TestClipLatencyIsBounded:
    def test_timeout_bounds_the_caller_instead_of_blocking_past_it(self):
        """The regression: 1502 ms of wall time against a 120 ms budget.

        The old code wrapped the future in ``with ThreadPoolExecutor(...)``, whose exit
        calls ``shutdown(wait=True)``; a timeout therefore still waited for the load to
        finish. The budget must now bound what the caller waits for.
        """
        service = _ClipService(delay=1.5)
        manager = _manager(service)

        started = time.perf_counter()
        results = manager._recall_clip("q", None, 5)
        elapsed_ms = (time.perf_counter() - started) * 1000

        assert results == []
        assert manager.clip_path_status().outcome is ClipPathOutcome.TIMEOUT
        assert elapsed_ms < 1000, f"caller waited {elapsed_ms:.0f}ms against a 120ms budget"

    def test_timed_out_attempt_is_not_joined(self):
        """CLIP inference cannot be cancelled safely, so it is abandoned, not awaited."""
        service = _ClipService(delay=0.6)
        manager = _manager(service)

        started = time.perf_counter()
        manager._recall_clip("q", None, 5)
        elapsed_ms = (time.perf_counter() - started) * 1000

        assert elapsed_ms < 400
        assert manager.clip_path_status().outcome is ClipPathOutcome.TIMEOUT

    def test_a_second_concurrent_request_fails_fast_instead_of_queueing(self):
        """Without a single-slot gate, requests pile up behind a slow weight load."""
        service = _ClipService(delay=0.6)
        manager = _manager(service)
        errors: list[Exception] = []

        def _first():
            try:
                manager._recall_clip("first", None, 5)
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(exc)

        thread = threading.Thread(target=_first)
        thread.start()
        time.sleep(0.05)  # let the first attempt take the slot

        started = time.perf_counter()
        manager._recall_clip("second", None, 5)
        elapsed_ms = (time.perf_counter() - started) * 1000

        assert elapsed_ms < 100, "a busy path must fail fast, not wait for the in-flight load"
        assert manager.clip_path_status().outcome is ClipPathOutcome.BUSY
        thread.join(timeout=5)
        assert not errors

    def test_the_slot_is_released_so_a_later_request_can_run(self):
        service = _ClipService(delay=0.05)
        manager = _manager(service)

        manager._recall_clip("q", None, 5)
        assert manager.clip_path_status().outcome is ClipPathOutcome.OK
        time.sleep(0.4)  # let the abandoned/aborted work settle

        manager._recall_clip("q2", None, 5)
        assert manager.clip_path_status().outcome is ClipPathOutcome.OK

    def test_weights_are_loaded_once_across_requests(self):
        """A per-request service turned lazy loading into a full reload every request."""
        service = _ClipService()
        manager = ParallelRecallManager()
        manager._clip_retriever = _StubClipRetriever(service)
        manager._clip_embedding_service = service

        for _ in range(5):
            manager._recall_clip("q", None, 5)

        assert service.encode_calls == 5, "each request encodes once"
        assert manager.clip_embedding_service is service, "but they must share one service instance"

    def test_manager_reuses_one_service_across_recalls(self):
        manager = ParallelRecallManager()
        first = manager.clip_embedding_service
        second = manager.clip_embedding_service

        assert first is second


class TestClipPoolLifecycle:
    def test_manager_built_without_init_still_has_a_single_slot(self):
        """The suite builds the manager via ``__new__``; the gate must still exist."""
        manager = ParallelRecallManager.__new__(ParallelRecallManager)
        service = _ClipService()
        manager._clip_retriever = _StubClipRetriever(service)
        manager._clip_embedding_service = service

        results = manager._recall_clip("q", None, 5)

        assert len(results) == 1
        assert manager.clip_path_status().outcome is ClipPathOutcome.OK
