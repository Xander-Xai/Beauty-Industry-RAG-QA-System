"""Contract: the real /api/chat history must end with the exchange just answered.

`tests/test_demo_corpus_rbac_consistency.py` pins the documented shape against the
demo fixture: `history` is the complete transcript and its last two messages are
the user message that was just sent and the answer that was just produced. The
real FastAPI handler is the contract the enterprise client actually calls, so it
has to agree with the fixture rather than quietly returning a shorter transcript.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app import app

    return TestClient(app)


@pytest.fixture
def auth_header():
    import time

    import jwt as _jwt

    os.environ["JWT_SECRET"] = "test-secret-for-unit-tests-only"
    payload = {
        "sub": "test_user",
        "user_id": "test_user",
        "role_mask": 0x01,
        "dept_mask": 0x01,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    token = _jwt.encode(payload, "test-secret-for-unit-tests-only", algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def _stub_pipeline(monkeypatch, routes):
    from core.pipeline_context import RequestContext

    class _StubPipeline:
        def process(self, ctx: RequestContext) -> None:
            # Mimic the real pipeline: it publishes the turn it just answered.
            from core.pipeline_context import SessionState

            session = SessionState.for_identity(
                ctx.session_id,
                ctx.user_id,
                ctx.user_role_mask,
                ctx.user_dept_mask,
                "default",
            )
            session.add_round(ctx.user_input, "STUB_ANSWER", ctx.rewrite_result)
            ctx.final_response = "STUB_ANSWER"

    monkeypatch.setattr(routes, "get_pipeline", lambda: _StubPipeline())


def test_chat_history_ends_with_the_exchange_that_was_just_answered(client, auth_header, monkeypatch):
    import api.routes as routes

    _stub_pipeline(monkeypatch, routes)

    response = client.post(
        "/api/chat",
        json={"message": "UNIQUE_QUESTION_MARKER", "session_id": "sess-hist-contract"},
        headers=auth_header,
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["answer"] == "STUB_ANSWER"
    assert body["history"][-1] == {"role": "assistant", "content": body["answer"]}
    assert body["history"][-2] == {"role": "user", "content": "UNIQUE_QUESTION_MARKER"}


def test_chat_history_does_not_repeat_the_current_exchange(client, auth_header, monkeypatch):
    import api.routes as routes

    _stub_pipeline(monkeypatch, routes)

    response = client.post(
        "/api/chat",
        json={"message": "REPEAT_CHECK_MARKER", "session_id": "sess-hist-repeat"},
        headers=auth_header,
    )
    assert response.status_code == 200, response.text
    body = response.json()

    user_msgs = [m["content"] for m in body["history"] if m["role"] == "user"]
    assistant_msgs = [m["content"] for m in body["history"] if m["role"] == "assistant"]

    assert user_msgs.count("REPEAT_CHECK_MARKER") == 1, body["history"]
    assert assistant_msgs.count("STUB_ANSWER") == 1, body["history"]


class _FakeRedis:
    """Minimal Redis double.

    A Redis-backed session is *deserialized* on every lookup, so the handler
    cannot rely on in-process object aliasing the way the in-memory session
    does.
    """

    def __init__(self):
        self.store: dict[str, str] = {}

    def setex(self, key, ttl, value):
        self.store[key] = value

    def get(self, key):
        return self.store.get(key)

    def expire(self, key, ttl):
        return True

    def scan(self, cursor=0, match=None, count=100):
        return 0, list(self.store.keys())

    def delete(self, key):
        self.store.pop(key, None)


def test_chat_history_contract_holds_with_a_redis_backed_session(client, auth_header, monkeypatch):
    """The transcript contract must not depend on which session backend is configured.

    The in-memory session object is aliased, so reading it before the pipeline
    answers still shows the new turn. Redis returns a deserialized copy, so a
    handler that reads the session before answering returns a transcript that is
    missing the exchange it just produced.
    """
    import api.routes as routes
    from core import pipeline_context

    fake = _FakeRedis()
    monkeypatch.setattr(pipeline_context, "_get_redis_client", lambda: fake)
    pipeline_context.SessionState._sessions.clear()

    _stub_pipeline(monkeypatch, routes)

    response = client.post(
        "/api/chat",
        json={"message": "REDIS_QUESTION_MARKER", "session_id": "sess-hist-redis"},
        headers=auth_header,
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["history"][-1] == {"role": "assistant", "content": body["answer"]}
    assert body["history"][-2] == {"role": "user", "content": "REDIS_QUESTION_MARKER"}
