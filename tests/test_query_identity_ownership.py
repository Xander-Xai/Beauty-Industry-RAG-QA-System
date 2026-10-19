"""Session ownership for ``/api/query`` is the verified identity, never the body.

These tests drive the **real** FastAPI dependency (``require_identity``) through the
app, not just ``query_handler`` directly, and pin the closed gap:

* a valid JWT owns the session, and a body-supplied ``user_id`` cannot override it;
* a blank-subject JWT and a missing token are rejected in non-dev mode (a blank
  principal previously slipped past the ``== "anonymous"`` check and let the body
  choose the session owner);
* dev mode trusts the ``X-User-*`` headers, still ignoring the body;
* a principal presenting another principal's ``session_id`` gets a fresh, empty
  session rather than the other principal's dialog history.

No real model, backend or Redis is used: the pipeline is a double and the session
store is the in-memory registry. This is identity-wiring evidence, not RAG evidence.
"""

from __future__ import annotations

import time
import types

import jwt as _jwt
import pytest
from fastapi.testclient import TestClient

from common.config import reload_config

_JWT_SECRET = "query-identity-test-secret-at-least-32-bytes!!"


def _token(sub: str, *, user_id: str | None = None, role_mask: int = 0x01, dept_mask: int = 0x01) -> str:
    payload: dict = {
        "sub": sub,
        "role_mask": role_mask,
        "dept_mask": dept_mask,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    if user_id is not None:
        payload["user_id"] = user_id
    return _jwt.encode(payload, _JWT_SECRET, algorithm="HS256")


def _install_fake_pipeline(monkeypatch, state: dict) -> None:
    from api import routes as routes_module

    class _Pipeline:
        def process(self, ctx):
            from core.pipeline_context import SessionState

            session = SessionState.get_or_create(ctx.session_id or "default", owner_id=ctx.user_id)
            state["owner"] = ctx.user_id
            state["seen_rounds"].append(len(session.dialog_rounds))
            session.add_round(ctx.user_input, "answer")
            ctx.final_response = "ok"

    monkeypatch.setattr(routes_module, "get_pipeline", lambda: _Pipeline())
    monkeypatch.setattr(
        routes_module,
        "get_metrics",
        lambda: types.SimpleNamespace(record_request=lambda ctx: None),
    )


@pytest.fixture
def secure_client(monkeypatch):
    """Non-dev mode: a valid JWT is required."""
    from core.pipeline_context import SessionState

    SessionState._sessions.clear()
    monkeypatch.setenv("JWT_SECRET", _JWT_SECRET)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    monkeypatch.delenv("JWT_ALGORITHM", raising=False)
    reload_config()

    state: dict = {"owner": None, "seen_rounds": []}
    _install_fake_pipeline(monkeypatch, state)

    from app import app

    yield TestClient(app), state
    SessionState._sessions.clear()
    monkeypatch.undo()
    reload_config()


@pytest.fixture
def dev_client(monkeypatch):
    """Dev mode: the ``X-User-*`` headers are trusted."""
    from core.pipeline_context import SessionState

    SessionState._sessions.clear()
    monkeypatch.setenv("JWT_SECRET", _JWT_SECRET)
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    reload_config()

    state: dict = {"owner": None, "seen_rounds": []}
    _install_fake_pipeline(monkeypatch, state)

    from app import app

    yield TestClient(app), state
    SessionState._sessions.clear()
    monkeypatch.undo()
    reload_config()


def test_valid_jwt_owns_the_session_and_body_user_id_is_ignored(secure_client):
    client, state = secure_client
    resp = client.post(
        "/api/query",
        json={"query": "q", "session_id": "s1", "user_id": "victim"},
        headers={"Authorization": f"Bearer {_token('alice')}"},
    )

    assert resp.status_code == 200, resp.text
    assert state["owner"] == "alice", "the body user_id became the session owner"


def test_blank_subject_jwt_is_rejected(secure_client):
    client, state = secure_client
    resp = client.post(
        "/api/query",
        json={"query": "q", "session_id": "s1", "user_id": "victim"},
        headers={"Authorization": f"Bearer {_token('', user_id='')}"},
    )

    assert resp.status_code == 401, resp.text
    assert state["owner"] is None, "a blank principal reached the pipeline"


def test_missing_token_is_rejected(secure_client):
    client, state = secure_client
    resp = client.post("/api/query", json={"query": "q", "session_id": "s1", "user_id": "victim"})

    assert resp.status_code == 401, resp.text
    assert state["owner"] is None


def test_whitespace_subject_jwt_is_rejected(secure_client):
    client, _ = secure_client
    resp = client.post(
        "/api/query",
        json={"query": "q"},
        headers={"Authorization": f"Bearer {_token('   ')}"},
    )

    assert resp.status_code == 401, resp.text


def test_dev_mode_uses_the_header_identity_and_ignores_the_body(dev_client):
    client, state = dev_client
    resp = client.post(
        "/api/query",
        json={"query": "q", "session_id": "s1", "user_id": "victim"},
        headers={"X-User-ID": "dev_user", "X-Role-Mask": "1", "X-Dept-Mask": "1"},
    )

    assert resp.status_code == 200, resp.text
    assert state["owner"] == "dev_user", "dev mode did not use the header identity"


def test_cross_user_session_id_does_not_expose_another_principals_history(secure_client):
    client, state = secure_client

    alice = client.post(
        "/api/query",
        json={"query": "alice secret", "session_id": "shared"},
        headers={"Authorization": f"Bearer {_token('alice')}"},
    )
    assert alice.status_code == 200, alice.text

    bob = client.post(
        "/api/query",
        json={"query": "bob", "session_id": "shared", "user_id": "alice"},
        headers={"Authorization": f"Bearer {_token('bob')}"},
    )
    assert bob.status_code == 200, bob.text

    # Bob's session is keyed by his verified identity, so Alice's round is invisible.
    assert state["owner"] == "bob"
    assert state["seen_rounds"] == [0, 0], "a foreign session was read"


def test_is_authenticated_principal_rejects_blank_and_anonymous():
    from common.auth import is_authenticated_principal

    assert is_authenticated_principal("alice") is True
    assert is_authenticated_principal("anonymous") is False
    assert is_authenticated_principal("") is False
    assert is_authenticated_principal("   ") is False
    assert is_authenticated_principal(None) is False
    assert is_authenticated_principal(123) is False
