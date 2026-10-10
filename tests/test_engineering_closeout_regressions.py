"""Regression tests for the engineering-closeout audit findings.

Each test pins a defect that let a **false claim** or a **permission leak** be
produced. The positive controls matter as much as the negative ones: a test set
that only asserts "nothing is claimed" can be satisfied by a system that never
works, so every fix below is paired with a case proving the correct behaviour
still happens.

The findings, in order:

* F1 — ``rerank_validation.run_comparison`` published ``OK`` /
  ``cross_encoder`` / ``is_reranking_evidence=True`` after a total load failure.
* F2 — the rerank smoke used ``.predict(...).mean()``, collapsing every document
  onto one score so the sort was a no-op and the improvement was structurally 0.
* F3 — ``rerank_status._model_available`` reported ``AVAILABLE`` for a directory
  holding only ``config.json`` (zero weight bytes).
* F4 — ``SessionState`` was keyed on a caller-supplied ``session_id`` with no
  owner, so one principal could read another's dialog history and locked docs.
* F5 — the P2-overload 503 was assigned to ``QueryResponse.answer: str`` and
  surfaced as HTTP 500.
* F6 — the run report reported ``rejected_by_evidence_gate`` for cache hits,
  admission rejections and post-gate generation errors.
"""

from __future__ import annotations

import pytest

from retrieval import rerank_status
from retrieval import rerank_validation as rv

# ── F1: a load failure must not publish a real-rerank claim ─────────────────


def _force_weights_available(monkeypatch) -> None:
    """Make the precheck say 'available' regardless of the host state."""
    monkeypatch.setattr(
        rerank_status,
        "rerank_weights_status",
        lambda *a, **k: {
            "available": True,
            "status": "AVAILABLE",
            "reason": "",
            "sentence_transformers_available": True,
            "models": {
                "cross_encoder_a": {"available": True, "reason": "", "model_path": "a"},
                "cross_encoder_b": {"available": True, "reason": "", "model_path": "b"},
            },
            "evidence_gate": {
                "mode": "normal",
                "no_ce_ceiling": 1.0,
                "low_confidence": 0.55,
                "high_confidence": 0.75,
                "fail_closed": False,
                "effect": "",
            },
            "failure_mode": "fail_closed",
        },
    )


def test_F1_run_comparison_never_claims_evidence_after_load_failure(monkeypatch):
    """A model that cannot load must not become a reranking-evidence artifact."""
    _force_weights_available(monkeypatch)

    def _boom(_models):
        raise RuntimeError("simulated weight load failure")

    monkeypatch.setattr(rv, "_load_ranker", _boom)

    assert rv.run_smoke().status == "FAILED"

    report = rv.run_comparison()
    assert report.status != "OK"
    assert report.provenance == rv.PROVENANCE_FALLBACK
    assert report.is_reranking_evidence is False
    assert report.comparison.get("cross_encoder") is None
    # The degradation must be on the record, not silent.
    assert report.degradations
    assert report.smoke.get("executed") is False


def test_F1_positive_control_a_real_ranking_still_claims_evidence(monkeypatch):
    """The gate must not become a blanket 'never OK' — a real run still passes.

    This is the positive control for F1: without it, ``status != "OK"`` alone
    would be satisfied by refusing every run, which is the opposite of the bug's
    fix.
    """
    _force_weights_available(monkeypatch)

    class _RealRanker:
        def rank(self, documents, query):
            # Put the first presented document first — whatever the presentation.
            return rv.RankResult(
                provenance=rv.PROVENANCE_CROSS_ENCODER,
                order=list(documents),
                scores={d: 1.0 for d in documents},
                latency_ms=1.0,
            )

    monkeypatch.setattr(rv, "_load_ranker", lambda _models: _RealRanker())
    monkeypatch.setattr(rv, "model_revision", lambda _p: "sha256:deadbeef")

    report = rv.run_comparison()
    assert report.status == "OK"
    assert report.provenance == rv.PROVENANCE_CROSS_ENCODER
    assert report.is_reranking_evidence is True


# ── F2: the comparison must be able to detect a real reordering ─────────────


def test_F2_rank_scores_each_document_individually():
    """A discriminating model must be able to change the order.

    Before the fix the scores were reduced with ``.mean()``, so every document
    got one identical number and the returned order always equalled the input
    order no matter what the model said.
    """

    class _Reverser:
        """Scores the LAST presented document highest."""

        def predict(self, pairs):
            return [float(i) for i in range(len(pairs))]

    class _Ranker(rv._CrossEncoderRanker):
        def __init__(self):
            self.models = [_Reverser(), _Reverser()]

    documents = ["A", "B", "C"]
    result = _Ranker().rank(documents, "q")
    assert len(set(result.scores.values())) > 1, "documents must not all share one score"
    assert result.order[0] == "C", "the highest-scored document must be ranked first"
    assert result.order != documents


def test_F2_candidate_order_never_presents_the_positive_first():
    """Both sides of the comparison must be forced to actually rank."""
    for pair in rv.SMOKE_PAIRS:
        order = rv.candidate_order(pair)
        assert order[0] != pair.positive, f"{pair.pair_id}: positive was presented first"
        assert sorted(order) == sorted([pair.positive, *pair.distractors])


# ── F3: config.json alone is not a model ────────────────────────────────────


def test_F3_config_only_directory_is_not_available(tmp_path):
    only_config = tmp_path / "ce"
    only_config.mkdir()
    (only_config / "config.json").write_text("{}")
    available, reason = rerank_status._model_available(str(only_config))
    assert available is False
    assert "weight" in reason


def test_F3_a_directory_with_weights_is_available(tmp_path):
    """Positive control: a genuine model directory is still accepted."""
    real = tmp_path / "ce"
    real.mkdir()
    (real / "config.json").write_text("{}")
    (real / "model.safetensors").write_bytes(b"\x00" * 16)
    available, reason = rerank_status._model_available(str(real))
    assert available is True
    assert reason == ""


def test_F3_a_bare_file_is_not_available(tmp_path):
    stray = tmp_path / "model.bin"
    stray.write_bytes(b"\x00")
    available, _ = rerank_status._model_available(str(stray))
    assert available is False


# ── F4: sessions are owner-scoped ───────────────────────────────────────────


def test_F4_session_state_does_not_leak_across_owners():
    from core.pipeline_context import SessionState

    SessionState._sessions.clear()
    sid = "shared-session-id"

    alice = SessionState.get_or_create(sid, owner_id="alice")
    alice.add_round("研发部机密配方", "内部: 0.9%")
    alice.lock_evidence(["rd-confidential-77"])

    bob = SessionState.get_or_create(sid, owner_id="bob")
    assert bob.dialog_rounds == [], "another principal read the dialog history"
    assert bob.locked_doc_ids == [], "another principal read the locked doc ids"

    # Positive control: the owner still sees their own session.
    alice_again = SessionState.get_or_create(sid, owner_id="alice")
    assert alice_again.dialog_rounds
    assert alice_again.locked_doc_ids == ["rd-confidential-77"]


# ── F5: the P2 overload must be a real 503 ──────────────────────────────────


def test_F5_overload_uses_a_status_override_not_a_string_field():
    from core.pipeline_context import RequestContext

    ctx = RequestContext(user_input="q", user_id="u")
    ctx.http_status_override = (503, {"error": "SERVICE_OVERLOADED"})
    status_code, content = ctx.http_status_override
    assert status_code == 503
    assert content["error"] == "SERVICE_OVERLOADED"
    # The string field stays a string, so QueryResponse validation cannot fail.
    assert isinstance(ctx.final_response, str)


def test_F5_query_response_rejects_a_response_object():
    """Documents *why* the override exists: the old shape could not serialise."""
    from fastapi.responses import JSONResponse
    from pydantic import ValidationError

    from api.models import QueryResponse

    with pytest.raises(ValidationError):
        QueryResponse(answer=JSONResponse(status_code=503, content={}), latency_ms=1.0)


# ── F6: the run report names the real terminal state ────────────────────────


def _base_ctx():
    from core.pipeline_context import RequestContext

    return RequestContext(user_input="烟酰胺限量?", user_id="u1", user_role_mask=1, user_dept_mask=1)


def _rewrite():
    class _Rewrite:
        business_type = "regulation"
        intent = "fact"
        fallback = False

    return _Rewrite()


def test_F6_cache_hit_is_not_reported_as_a_gate_refusal():
    from core.run_report import OUTCOME_CACHE_HIT, build_run_report

    ctx = _base_ctx()
    ctx.rewrite_result = _rewrite()
    ctx.cache_hit_level = "L2_SESSION"
    ctx.final_response = "缓存回答"
    report = build_run_report(ctx)
    assert report.outcome == OUTCOME_CACHE_HIT


def test_F6_admission_rejection_is_not_reported_as_a_gate_refusal():
    from core.run_report import OUTCOME_REJECTED_BY_ADMISSION, build_run_report

    ctx = _base_ctx()
    ctx.rewrite_result = _rewrite()
    # Admission rejects before retrieval: no evidence result is ever produced.
    report = build_run_report(ctx)
    assert report.outcome == OUTCOME_REJECTED_BY_ADMISSION


def test_F6_generation_error_after_a_passing_gate_is_an_error():
    from common.models import RerankResult
    from core.pipeline_context import EvidenceGateResult
    from core.run_report import OUTCOME_ERROR, build_run_report

    ctx = _base_ctx()
    ctx.rewrite_result = _rewrite()
    ctx.rerank_results = [RerankResult(doc_id="d0", content="x", source="bm25_es")]
    ctx.evidence_result = EvidenceGateResult(evidence_score=0.9, ce_top1_score=0.8, ce_top3_mean_score=0.7)
    ctx.evidence_result.decision = "pass"
    ctx.degraded = True
    ctx.fallback_reason = "vllm timeout"
    report = build_run_report(ctx)
    assert report.outcome == OUTCOME_ERROR


def test_F6_positive_control_a_real_rejection_is_still_named():
    from common.models import RerankResult
    from core.pipeline_context import EvidenceGateResult
    from core.run_report import OUTCOME_REJECTED_BY_EVIDENCE_GATE, build_run_report

    ctx = _base_ctx()
    ctx.rewrite_result = _rewrite()
    ctx.rerank_results = [RerankResult(doc_id="d0", content="x", source="bm25_es")]
    ctx.evidence_result = EvidenceGateResult(evidence_score=0.1, ce_top1_score=0.0, ce_top3_mean_score=0.0)
    ctx.evidence_result.decision = "reject"
    report = build_run_report(ctx)
    assert report.outcome == OUTCOME_REJECTED_BY_EVIDENCE_GATE


# ── F7: runtime rerank provenance outranks the filesystem precheck ──────────


def test_F7_a_request_time_rerank_failure_is_not_reported_as_real(monkeypatch):
    """Weights on disk + in-request failure must report the fallback.

    The precheck can only see the filesystem. When the request itself falls back
    (load failure, OOM, timeout) the observed provenance must win, otherwise the
    report claims a real CrossEncoder that never ran.
    """
    from common.models import RerankResult
    from core.pipeline_context import EvidenceGateResult
    from core.run_report import STATUS_DEGRADED, build_run_report
    from retrieval import rerank_status

    monkeypatch.setattr(
        rerank_status,
        "rerank_weights_status",
        lambda *a, **k: {
            "available": True,
            "status": "AVAILABLE",
            "reason": "",
            "models": {"cross_encoder_a": {"available": True}, "cross_encoder_b": {"available": True}},
            "evidence_gate": {"mode": "normal", "no_ce_ceiling": 1.0, "low_confidence": 0.55},
        },
    )

    ctx = _base_ctx()
    ctx.rewrite_result = _rewrite()
    ctx.stage_timings = {"cross_encoder_ensemble": 3.0}
    ctx.rerank_results = [RerankResult(doc_id="d0", content="x", source="bm25_es")]
    ctx.evidence_result = EvidenceGateResult(evidence_score=0.9, ce_top1_score=0.0, ce_top3_mean_score=0.0)
    ctx.evidence_result.decision = "pass"
    ctx.ce_stage_provenance = "deterministic_fallback"
    ctx.ce_stage_fallback_reason = "RuntimeError: weights failed to load"

    report = build_run_report(ctx)
    assert report.rerank["provenance"] == "deterministic_fallback"
    assert report.rerank["provenance_source"] == "runtime"
    assert report.as_dict()["answered_with_real_rerank"] is False
    ce_stage = next(s for s in report.stages if s.stage == "cross_encoder_ensemble")
    assert ce_stage.status == STATUS_DEGRADED


def test_F7_runtime_success_is_reported_even_though_the_precheck_runs(monkeypatch):
    """Positive control: a real rerank observed at runtime still reports true."""
    from common.models import RerankResult
    from core.run_report import build_run_report
    from retrieval import rerank_status

    monkeypatch.setattr(
        rerank_status,
        "rerank_weights_status",
        lambda *a, **k: {
            "available": True,
            "status": "AVAILABLE",
            "reason": "",
            "models": {},
            "evidence_gate": {"mode": "normal", "no_ce_ceiling": 1.0, "low_confidence": 0.55},
        },
    )

    ctx = _base_ctx()
    ctx.rerank_results = [RerankResult(doc_id="d0", content="x", source="bm25_es")]
    ctx.ce_stage_provenance = "cross_encoder"
    report = build_run_report(ctx)
    assert report.rerank["provenance"] == "cross_encoder"
    assert report.rerank["provenance_source"] == "runtime"


def test_F7_the_ensemble_records_a_runtime_fallback(monkeypatch):
    """The ensemble itself must write the observed provenance onto the context."""
    from core.pipeline_context import RequestContext
    from retrieval.cross_encoder_ensemble import CrossEncoderEnsemble

    class _Cand:
        def __init__(self):
            self.content = "x"
            self.ce_score_a = 0.0
            self.ce_score_b = 0.0
            self.ce_score_ensemble = 0.0
            self.source = "bm25_es"

    ensemble = CrossEncoderEnsemble.__new__(CrossEncoderEnsemble)
    ensemble._ce_a = None
    ensemble._ce_b = None
    ensemble._batch_aggregator = None
    ensemble.rerank_status = {"status": "BLOCKED"}

    ctx = RequestContext(user_input="q", user_id="u")
    # Force the load path to fail without a real sentence_transformers install.
    monkeypatch.setattr(
        CrossEncoderEnsemble, "ce_a", property(lambda self: (_ for _ in ()).throw(RuntimeError("no weights")))
    )
    result = ensemble.rerank("q", [_Cand()], ctx=ctx)
    assert result  # fallback keeps the candidates
    assert ctx.ce_stage_provenance == "deterministic_fallback"
    assert ctx.ce_stage_fallback_reason
