"""Correctness fixes outside the CLIP recall path.

Seven defects, each of which either made a gate more permissive than it should be, made
a configuration inert, or reported a success where none happened. Each test here fails
without its fix.

- ``evidence_gate._compute_doc_consistency`` returned a fixed ``0.8`` when the NLI call
  raised. That is a *raised* default: a successful check that found contradictions scores
  lower, so an NLI outage made the gate more permissive than a working one.
- ``RerankResult`` had no provenance fields at all, and both bi-encoders built it from
  the recall result without carrying ``source_path`` / ``content_hash`` / ``chunk_id``,
  so the evidence gate and the citations could not name where a passage came from.
- The Answer Gate rejection path returned *after* ``evidence_locked_doc_ids`` had been
  populated, so a refusal response still listed the documents that had been retrieved.
- ``auth/user_identity.py`` was a second, unwired authentication implementation whose
  docstring still advertised a super-admin fallback the code no longer had.
- ``retrieval-service/rerank/`` carried its own copies of the gates; the deployed
  microservice ran a weaker Evidence Gate with no fail-closed marker.
- ``POST /api/continuation`` returned ``answer: ""`` with ``has_more: false``, which a
  client cannot distinguish from a real, empty answer.
- ``QueryRewriter._call_llm`` built a payload from ``config.json`` and then ignored it,
  hardcoding ``max_tokens=192``.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── A2-3: the Evidence Gate must fail closed ──────────────────────────────


class TestDocConsistencyFailsClosed:
    @staticmethod
    def _gate_with_broken_nli():
        from retrieval.evidence_gate import EvidenceEnsembleGate

        gate = EvidenceEnsembleGate()
        broken = MagicMock()
        broken._batch_nli_inference.side_effect = RuntimeError("NLI unavailable")
        broken._nli_inference.side_effect = RuntimeError("NLI unavailable")
        gate._answer_gate = broken
        return gate

    def _results(self):
        from tests.test_evidence_gate import make_rerank_result

        return [make_rerank_result("d1", 0.9), make_rerank_result("d2", 0.9)]

    def test_a_failed_check_scores_zero_not_a_raised_default(self):
        gate = self._gate_with_broken_nli()

        score = gate._compute_doc_consistency("query", self._results())

        assert score == 0.0, "an unmeasurable component must not contribute confidence"

    def test_the_failed_component_cannot_lift_the_evidence_score(self):
        """The direct consequence: the ceiling with a broken check is below the one with
        a genuine check that found contradictions."""
        weights = __import__("common.config", fromlist=["get_config_dict"]).get_config_dict()["retrieval"][
            "evidence_gate"
        ]["weights"]
        w4 = float(weights.get("w4", 0.0))

        broken = self._gate_with_broken_nli()
        broken_result = broken.evaluate("query", self._results(), retrieval_agreement_score=0.9)

        # A working check that genuinely contradicted would have scored below 1.0.
        assert broken_result.evidence_score <= w4 * 0.9 + sum(float(weights.get(f"w{i}", 0.0)) * 0.9 for i in (1, 2, 3))

    def test_the_degraded_state_is_observable(self):
        gate = self._gate_with_broken_nli()
        gate.rerank_status = {"status": "READY"}

        gate.evaluate("query", self._results(), retrieval_agreement_score=0.9)

        assert gate._gate_mode() == "fail_closed_doc_consistency_unavailable"

    def test_a_working_check_is_not_marked_degraded(self):
        gate = self._gate_with_broken_nli()
        gate.rerank_status = {"status": "READY"}
        working = MagicMock()
        working._batch_nli_inference.return_value = [(0.1, 0.9)]
        gate._answer_gate = working

        gate.evaluate("query", self._results(), retrieval_agreement_score=0.9)

        assert gate._gate_mode() == "normal"

    def test_the_service_module_no_longer_carries_a_weaker_gate(self):
        """The deployed microservice must share the canonical, hardened gate."""
        from retrieval.evidence_gate import EvidenceEnsembleGate as Canonical
        from retrieval_service.rerank.evidence_gate import EvidenceEnsembleGate as Served

        assert Served is Canonical

    def test_the_service_module_no_longer_carries_a_weaker_answer_gate(self):
        from retrieval.answer_gate import AnswerGate as Canonical
        from retrieval_service.rerank.answer_gate import AnswerGate as Served

        assert Served is Canonical


# ── A2-4: provenance must survive the rerank boundary ─────────────────────


_PROVENANCE = {
    "source_path": "regulations/2024/limit.md",
    "content_hash": "sha256:abc123",
    "chunk_id": "chunk-7",
    "doc_version_epoch": "epoch-3",
}


def _candidate(doc_id="d1"):
    from core.pipeline_context import RecallResult

    return RecallResult(
        doc_id=doc_id,
        content="烟酰胺 2%",
        score=0.5,
        source="dense_bge",
        metadata=dict(_PROVENANCE),
    )


class TestRerankProvenanceIsPreserved:
    def test_the_model_carries_the_provenance_fields(self):
        from common.models import RerankResult

        fields = set(RerankResult.model_fields)

        assert {"source_path", "content_hash", "chunk_id", "metadata"} <= fields

    def test_provenance_defaults_are_empty_not_absent(self):
        from common.models import RerankResult

        result = RerankResult(doc_id="d", content="c")

        assert (result.source_path, result.content_hash, result.chunk_id) == ("", "", "")
        assert result.metadata == {}

    @pytest.mark.parametrize("module_path", ["retrieval.bi_encoder", "retrieval_service.rerank.bi_encoder"])
    def test_both_bi_encoders_carry_provenance_through(self, module_path):
        module = __import__(module_path, fromlist=["_to_rerank_result"])

        result = module._to_rerank_result(_candidate(), bi_score=0.42)

        assert result.source_path == _PROVENANCE["source_path"]
        assert result.content_hash == _PROVENANCE["content_hash"]
        assert result.chunk_id == _PROVENANCE["chunk_id"]
        assert result.bi_score == 0.42
        assert result.metadata["doc_version_epoch"] == "epoch-3"

    @pytest.mark.parametrize("module_path", ["retrieval.bi_encoder", "retrieval_service.rerank.bi_encoder"])
    def test_provenance_survives_a_candidate_without_metadata(self, module_path):
        from core.pipeline_context import RecallResult

        module = __import__(module_path, fromlist=["_to_rerank_result"])
        bare = RecallResult(doc_id="d2", content="c", score=0.1, source="bm25_es")

        result = module._to_rerank_result(bare)

        assert (result.source_path, result.content_hash, result.chunk_id) == ("", "", "")
        assert result.source == "bm25_es"

    def test_the_cross_encoder_stage_preserves_the_fields(self):
        """The CE ensemble mutates candidates in place, so it must not rebuild them."""
        from common.models import RerankResult
        from retrieval.bi_encoder import _to_rerank_result

        candidate = _to_rerank_result(_candidate(), bi_score=0.5)

        # Mirrors what cross_encoder_ensemble.rerank() does to each candidate.
        candidate.ce_score_ensemble = 0.77

        assert isinstance(candidate, RerankResult)
        assert candidate.source_path == _PROVENANCE["source_path"]
        assert candidate.content_hash == _PROVENANCE["content_hash"]
        assert candidate.chunk_id == _PROVENANCE["chunk_id"]


# ── A2-5: a refusal must not enumerate the documents it considered ────────


class TestRefusalDoesNotExposeEvidenceIds:
    def test_the_answer_gate_rejection_clears_the_locked_document_ids(self):
        """Source-level assertion of the fix, plus the reason it is needed.

        ``evidence_locked_doc_ids`` is populated before generation, and the API returns it
        unconditionally, so a refusal that returned after that point named the documents
        that had been retrieved.
        """
        source = inspect.getsource(
            __import__("core.pipeline", fromlist=["OnlineRAGPipeline"]).OnlineRAGPipeline.process
        )
        reject_block = source.split("if not ctx.answer_gate_result.passed:")[1].split("return ctx.final_response")[0]

        assert "ctx.evidence_locked_doc_ids = []" in reject_block

    def test_the_evidence_gate_rejection_never_populated_it_at_all(self):
        """The earlier rejection path returns before the ids are locked."""
        source = inspect.getsource(
            __import__("core.pipeline", fromlist=["OnlineRAGPipeline"]).OnlineRAGPipeline.process
        )
        reject_block = source.split('if decision == "reject":')[1].split("return ctx.final_response")[0]
        locking_index = source.index("ctx.evidence_locked_doc_ids = [r.doc_id")
        reject_index = source.index('if decision == "reject":')

        assert "ctx.evidence_locked_doc_ids" not in reject_block
        assert reject_index < locking_index


# ── A2-1 / A2-6 / A2-7 ────────────────────────────────────────────────────


class TestLegacyAndStubSurface:
    def test_the_unwired_authentication_module_is_gone(self):
        assert not (REPO_ROOT / "auth" / "user_identity.py").exists()

    def test_no_module_still_imports_it(self):
        offenders = []
        this_file = Path(__file__).resolve()
        for path in REPO_ROOT.rglob("*.py"):
            if ".git" in path.parts or "__pycache__" in path.parts or path.resolve() == this_file:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "auth.user_identity" in text or "from auth import user_identity" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
        assert offenders == []

    def test_the_replacement_authentication_path_is_the_one_in_use(self):
        """``common/auth.py`` is the canonical ingress and stays wired to the API."""
        assert "require_identity" in (REPO_ROOT / "api" / "routes.py").read_text(encoding="utf-8")

    def test_continuation_returns_not_implemented_instead_of_an_empty_answer(self):
        source = (REPO_ROOT / "api" / "routes.py").read_text(encoding="utf-8")
        block = source.split("def continuation_handler")[1].split("# ─── GET /api/dialog_history")[0]

        assert "status.HTTP_501_NOT_IMPLEMENTED" in block
        # The old stub answered 200 with an empty body a client could read as a result.
        assert '"answer": ""' not in block

    def test_continuation_still_authenticates_before_declaring_it_unsupported(self):
        source = (REPO_ROOT / "api" / "routes.py").read_text(encoding="utf-8")
        block = source.split("def continuation_handler")[1].split("# ─── GET /api/dialog_history")[0]

        assert "Depends(require_identity)" in block
        assert "SessionState.get_or_create" in block

    def test_the_rewriter_applies_the_configured_token_budget(self):
        """``max_tokens=192`` was hardcoded while ``config.json`` also said 192, so the
        setting was inert rather than visibly wrong."""
        from rewrite.query_rewriter import QueryRewriter

        captured = {}

        class _Router:
            def route_completion(self, endpoint_key, prompt, max_tokens=512, temperature=0.7):
                captured.update(
                    endpoint_key=endpoint_key, prompt=prompt, max_tokens=max_tokens, temperature=temperature
                )
                return "{}"

        rewriter = QueryRewriter()
        rewriter.max_output_tokens = 321  # deliberately not the historical 192
        rewriter.temperature = 0.25

        import sys
        from unittest.mock import patch

        router_module = type(sys)("router.stateless_router")
        router_module.StatelessRouter = _Router
        with patch.dict(sys.modules, {"router.stateless_router": router_module}):
            rewriter._call_llm("prompt text")

        assert captured["max_tokens"] == 321, "the configured budget must reach the endpoint call"
        assert captured["temperature"] == 0.25
        assert captured["prompt"] == "prompt text"

    def test_an_explicit_temperature_overrides_the_configured_one(self):
        from rewrite.query_rewriter import QueryRewriter

        captured = {}

        class _Router:
            def route_completion(self, endpoint_key, prompt, max_tokens=512, temperature=0.7):
                captured["temperature"] = temperature
                return "{}"

        import sys
        from unittest.mock import patch

        rewriter = QueryRewriter()
        rewriter.temperature = 0.25
        router_module = type(sys)("router.stateless_router")
        router_module.StatelessRouter = _Router
        with patch.dict(sys.modules, {"router.stateless_router": router_module}):
            rewriter._call_llm("p", temperature=0.0)

        assert captured["temperature"] == 0.0

    def test_the_rewriter_still_logs_and_re_raises(self):
        """Degradation is the caller's decision; swallowing here would hide outages."""
        from rewrite.query_rewriter import QueryRewriter

        class _Router:
            def route_completion(self, *a, **k):
                raise RuntimeError("endpoint down")

        import sys
        from unittest.mock import patch

        router_module = type(sys)("router.stateless_router")
        router_module.StatelessRouter = _Router
        with patch.dict(sys.modules, {"router.stateless_router": router_module}):
            with pytest.raises(RuntimeError, match="endpoint down"):
                QueryRewriter()._call_llm("p")
