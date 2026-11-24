"""Context-memory cache isolation and rewrite-order regression tests.

No external LLM or Redis. The cache is a fake key/value store: same session,
same query and same role/dept masks must not share contextual answers across
different authenticated owners.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.pipeline import OnlineRAGPipeline
from core.pipeline_context import GenerationResult, RequestContext
from tests.test_pipeline_ordering import _make_rewrite, _setup_pipeline_mocks


def _ctx(owner: str, *, session_id: str = "shared-session") -> RequestContext:
    return RequestContext(
        user_input="它的浓度是多少？",
        user_id=owner,
        session_id=session_id,
        user_role_mask=1,
        user_dept_mask=2,
    )


def _result(answer: str) -> GenerationResult:
    return GenerationResult(
        answer=answer,
        model_used="test-model",
        max_tokens=512,
        has_more=False,
    )


def test_context_cache_scopes_by_owner_and_session():
    key = "base-cache-key"
    alice = OnlineRAGPipeline._scope_cache_key_to_session(key, "shared", "alice")
    bob = OnlineRAGPipeline._scope_cache_key_to_session(key, "shared", "bob")
    other_session = OnlineRAGPipeline._scope_cache_key_to_session(key, "other", "alice")
    assert len({alice, bob, other_session}) == 3
    with pytest.raises(ValueError, match="owner_id and session_id"):
        OnlineRAGPipeline._scope_cache_key_to_session(key, "shared", "")
    with pytest.raises(ValueError, match="owner_id and session_id"):
        OnlineRAGPipeline._scope_cache_key_to_session(key, None, "alice")


@patch("core.pipeline.log_audit_event")
def test_rewrite_happens_before_cache_lookup_and_owner_cannot_reuse_foreign_answer(_audit):
    pipeline = OnlineRAGPipeline()
    rewrite = _make_rewrite(rewritten_query="烟酰胺 浓度", requires_context=True)
    _setup_pipeline_mocks(pipeline, rewrite=rewrite)
    pipeline._complexity_evaluator = MagicMock()
    pipeline._complexity_evaluator.evaluate.return_value = False
    pipeline._llm_client.generate.side_effect = [
        _result("Alice answer"),
        _result("Bob answer"),
        _result("Alice after another turn"),
    ]

    records = {}
    alice = _ctx("alice")
    # Represents a globally scoped answer written before context detection.
    records[pipeline._build_cache_key(alice)] = {"answer": "FOREIGN GLOBAL CACHE ANSWER"}

    def cache_get(key, role_mask, dept_mask):
        assert pipeline._query_rewriter.rewrite.called
        return records.get(key)

    pipeline._cache.get.side_effect = cache_get
    pipeline._cache.set.side_effect = lambda key, value, role_mask, dept_mask: records.update({key: value})

    assert pipeline.process(alice) == "Alice answer"
    assert alice.cache_hit_level == "MISS"
    assert pipeline.process(_ctx("bob")) == "Bob answer"

    # Same owner/session but the conversation has advanced: old answers are
    # invalidated, rather than replayed into the new conversational state.
    alice_revisit = _ctx("alice")
    assert pipeline.process(alice_revisit) == "Alice after another turn"
    assert alice_revisit.cache_hit_level == "MISS"
    assert pipeline._llm_client.generate.call_count == 3
    assert len(records) == 4

    # A retry against the same *unchanged* historical state may reuse a
    # verified owner/session/history key, without another generation.
    stable = _ctx("alice")
    stable.rewrite_result = rewrite
    stable_key = pipeline._scope_cache_key_to_session(
        pipeline._build_cache_key(stable), stable.session_id, stable.user_id
    )
    records[stable_key] = {"answer": "Same-context retry"}
    stable.rewrite_result = None
    assert pipeline.process(stable) == "Same-context retry"
    assert stable.cache_hit_level == "L2_SESSION"
    assert pipeline._llm_client.generate.call_count == 3
    assert pipeline._query_rewriter.rewrite.call_count == 4


@patch("core.pipeline.log_audit_event")
def test_contextual_anonymous_request_does_not_read_or_write_cache(_audit):
    pipeline = OnlineRAGPipeline()
    _setup_pipeline_mocks(pipeline, rewrite=_make_rewrite(requires_context=True))
    pipeline._complexity_evaluator = MagicMock()
    pipeline._complexity_evaluator.evaluate.return_value = False

    anonymous = _ctx("")
    assert pipeline.process(anonymous)
    assert anonymous.cache_hit_level == "DISABLED_UNSCOPED"
    pipeline._cache.get.assert_not_called()
    pipeline._cache.set.assert_not_called()


@patch("core.pipeline.log_audit_event")
def test_rewrite_failure_disables_all_cache_reuse(_audit):
    pipeline = OnlineRAGPipeline()
    _setup_pipeline_mocks(pipeline, rewrite=_make_rewrite(requires_context=False, fallback=True))
    pipeline._complexity_evaluator = MagicMock()
    pipeline._complexity_evaluator.evaluate.return_value = False

    ctx = _ctx("alice")
    assert pipeline.process(ctx)
    assert ctx.cache_hit_level == "DISABLED"
    pipeline._cache.get.assert_not_called()
    pipeline._cache.set.assert_not_called()


@patch("core.pipeline.log_audit_event")
@pytest.mark.parametrize(("owner", "session_id"), [("", "shared"), ("alice", None), ("", None)])
def test_unscoped_requests_do_not_load_or_persist_session_history(_audit, owner, session_id):
    pipeline = OnlineRAGPipeline()
    _setup_pipeline_mocks(pipeline, rewrite=_make_rewrite(requires_context=True))
    pipeline._complexity_evaluator = MagicMock()
    pipeline._complexity_evaluator.evaluate.return_value = False
    ctx = RequestContext(user_input="它是什么？", user_id=owner, session_id=session_id)
    with patch("core.pipeline_context.SessionState.get_or_create") as load:
        assert pipeline.process(ctx)
        load.assert_not_called()
    assert pipeline._query_rewriter.rewrite.call_args.kwargs["recent_dialogs"] == []
    pipeline._cache.get.assert_not_called()
    pipeline._cache.set.assert_not_called()
