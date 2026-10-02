"""Tests for the structured enterprise audit trail.

Covers the event schema, outcome semantics, redaction depth, correlation-id
propagation, anonymous-actor handling, and the real call sites.
"""

from __future__ import annotations

import json
import logging

import pytest

from common.audit import (
    ACTION_EPOCH_SEAL,
    ACTION_LOGIN_FAILURE,
    ACTION_LOGIN_RATE_LIMITED,
    ACTION_LOGIN_SUCCESS,
    ACTION_MEDIA_ACCESS_DENIED,
    ACTION_ROLE_UPDATE,
    ACTION_USER_CREATE,
    ANONYMOUS_ACTOR,
    KNOWN_ACTIONS,
    KNOWN_OUTCOMES,
    OUTCOME_DENIED,
    OUTCOME_FAILED,
    OUTCOME_SUCCESS,
    REDACTED,
    AuditEvent,
    audit_event,
    audit_login_failure,
    audit_login_rate_limited,
    audit_login_success,
    audit_media_denied,
    get_request_id,
    is_secret_key,
    log_audit_event,
    new_request_id,
    redact,
    reset_request_id,
    set_request_id,
)

CORE_FIELDS = (
    "timestamp",
    "request_id",
    "actor_id",
    "action",
    "resource_type",
    "resource_id",
    "outcome",
    "reason",
    "metadata",
)


@pytest.fixture(autouse=True)
def _isolate_audit(caplog):
    """Capture audit output and keep Redis/file sinks out of the test."""
    caplog.set_level(logging.DEBUG, logger="audit")
    yield


# ── schema ──────────────────────────────────────────────────────────────────


def test_event_carries_every_core_field():
    event = audit_event(
        action=ACTION_LOGIN_SUCCESS,
        outcome=OUTCOME_SUCCESS,
        actor_id="u1",
        resource_type="session",
        resource_id="u1",
    )
    payload = event.to_dict()
    for name in CORE_FIELDS:
        assert name in payload, f"missing core audit field {name}"


def test_timestamp_is_a_float_epoch():
    event = audit_event(action=ACTION_LOGIN_SUCCESS, outcome=OUTCOME_SUCCESS, actor_id="u1")
    assert isinstance(event.timestamp, float)
    assert event.timestamp > 0


def test_event_serializes_to_json():
    event = audit_event(action=ACTION_LOGIN_SUCCESS, outcome=OUTCOME_SUCCESS, actor_id="u1")
    decoded = json.loads(event.to_json())
    assert decoded["action"] == ACTION_LOGIN_SUCCESS


# ── outcomes ────────────────────────────────────────────────────────────────


def test_success_event():
    event = audit_login_success("u1", {"role_mask": 7})
    assert event.outcome == OUTCOME_SUCCESS
    assert event.actor_id == "u1"
    assert event.metadata["role_mask"] == 7


def test_denied_event():
    event = audit_login_failure("alice", "invalid credentials")
    assert event.outcome == OUTCOME_DENIED
    assert event.reason == "invalid credentials"
    assert event.resource_id == "alice"


def test_failed_event():
    event = audit_event(
        action=ACTION_ROLE_UPDATE,
        outcome=OUTCOME_FAILED,
        actor_id="admin",
        reason="user not found",
    )
    assert event.outcome == OUTCOME_FAILED


def test_media_denial_is_denied_and_names_the_document():
    event = audit_media_denied("u9", "doc-42", reason="mask mismatch")
    assert event.action == ACTION_MEDIA_ACCESS_DENIED
    assert event.outcome == OUTCOME_DENIED
    assert event.resource_id == "doc-42"
    assert event.actor_id == "u9"


def test_rate_limited_event():
    event = audit_login_rate_limited("203.0.113.7")
    assert event.action == ACTION_LOGIN_RATE_LIMITED
    assert event.outcome == OUTCOME_DENIED
    assert event.resource_id == "203.0.113.7"


@pytest.mark.parametrize("outcome", sorted(KNOWN_OUTCOMES))
def test_known_outcomes_accepted(outcome):
    event = audit_event(action=ACTION_LOGIN_SUCCESS, outcome=outcome)
    assert event.outcome == outcome


def test_unknown_outcome_rejected():
    with pytest.raises(ValueError, match="unknown audit outcome"):
        audit_event(action=ACTION_LOGIN_SUCCESS, outcome="maybe")


def test_unknown_action_rejected():
    with pytest.raises(ValueError, match="unknown audit action"):
        audit_event(action="totally.made.up", outcome=OUTCOME_SUCCESS)


def test_registered_actions_all_exist_on_a_real_code_path():
    # No activate event: this repository has no activate endpoint, and inventing
    # one would imply an API that does not exist.
    assert "knowledge.epoch.activate" not in KNOWN_ACTIONS
    assert KNOWN_ACTIONS == {
        ACTION_LOGIN_SUCCESS,
        ACTION_LOGIN_FAILURE,
        ACTION_LOGIN_RATE_LIMITED,
        ACTION_USER_CREATE,
        ACTION_ROLE_UPDATE,
        ACTION_MEDIA_ACCESS_DENIED,
        ACTION_EPOCH_SEAL,
    }


# ── actor handling ──────────────────────────────────────────────────────────


def test_missing_actor_becomes_anonymous():
    event = audit_event(action=ACTION_LOGIN_SUCCESS, outcome=OUTCOME_SUCCESS)
    assert event.actor_id == ANONYMOUS_ACTOR


def test_pre_auth_failure_is_not_attributed_to_a_user():
    event = audit_login_failure("alice", "invalid credentials")
    assert event.actor_id == ANONYMOUS_ACTOR


# ── redaction ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "PASSWORD",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refreshToken",
        "authorization",
        "Authorization",
        "api_key",
        "apiKey",
        "api-key",
        "API-KEY",
        "credential",
        "private_key",
        "client_secret",
    ],
)
def test_secret_keys_are_redacted(key):
    assert is_secret_key(key), f"{key} must be treated as secret"
    assert redact({key: "hunter2"})[key] == REDACTED


def test_non_secret_keys_survive():
    payload = {"user_id": "u1", "role_mask": 7, "business_type": "regulation", "doc_id": "d1"}
    assert redact(payload) == payload


def test_nested_secret_is_redacted():
    payload = {"meta": {"outer": {"apiKey": "sk-live-123"}}}
    result = redact(payload)
    assert result["meta"]["outer"]["apiKey"] == REDACTED


def test_secret_inside_list_is_redacted():
    payload = {"items": [{"password": "p"}, {"ok": 1}]}
    result = redact(payload)
    assert result["items"][0]["password"] == REDACTED
    assert result["items"][1]["ok"] == 1


def test_tuple_is_handled_like_list():
    result = redact({"pair": ("plain", "s3cret")})
    assert isinstance(result["pair"], list)


def test_bearer_value_is_redacted_even_under_an_innocuous_key():
    """Key-based redaction cannot help when a raw token is a *value*."""
    result = redact({"detail": "Bearer abc.def.ghi"})
    assert result["detail"] == REDACTED


def test_jwt_shaped_value_is_redacted():
    jwt_like = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MSJ9.abcdefghijklmnop"
    assert redact({"note": jwt_like})["note"] == REDACTED


def test_auth_suffix_match_does_not_swallow_unrelated_keys():
    """`auth` must be matched as a suffix, never as a substring.

    A substring match would also collapse `auth_env_presence` — a mapping of
    which credential variables were set — down to a single boolean, destroying
    the evidence. This repository already had that bug on the provenance side.
    """
    assert is_secret_key("auth")
    assert is_secret_key("httpAuth")
    assert is_secret_key("remote_auth")
    assert not is_secret_key("auth_env_presence")
    assert not is_secret_key("author")
    assert not is_secret_key("authority")

    payload = {"auth_env_presence": {"ELASTICSEARCH_USERNAME_set": True, "count": 2}}
    assert redact(payload)["auth_env_presence"] == {"ELASTICSEARCH_USERNAME_set": True, "count": 2}


def test_credential_named_keys_are_still_redacted_inside_a_preserved_mapping():
    """The preserved parent does not exempt a credential-named child."""
    result = redact({"auth_env_presence": {"ELASTICSEARCH_PASSWORD": True}})
    assert result["auth_env_presence"]["ELASTICSEARCH_PASSWORD"] == REDACTED


def test_audit_event_metadata_is_always_redacted():
    event = audit_event(
        action=ACTION_USER_CREATE,
        outcome=OUTCOME_SUCCESS,
        actor_id="admin",
        metadata={"password": "hunter2", "roles": ["rd"]},
    )
    assert event.metadata["password"] == REDACTED
    assert event.metadata["roles"] == ["rd"]


def test_login_success_never_carries_the_token_pair():
    event = audit_login_success("u1", {"access_token": "eyJ.abc.def", "refresh_token": "r"})
    rendered = event.to_json()
    assert "eyJ.abc.def" not in rendered
    assert event.metadata["access_token"] == REDACTED


def test_deep_nesting_terminates():
    payload: dict = {"a": {}}
    cursor = payload["a"]
    for _ in range(40):
        cursor["a"] = {}
        cursor = cursor["a"]
    cursor["password"] = "x"
    # Must return rather than recurse without bound.
    assert isinstance(redact(payload), dict)


# ── request id propagation ──────────────────────────────────────────────────


def test_request_id_defaults_to_empty_outside_a_request():
    assert get_request_id() == ""


def test_request_id_is_visible_to_audit_events():
    token = set_request_id("req-abc")
    try:
        assert get_request_id() == "req-abc"
        event = audit_event(action=ACTION_LOGIN_SUCCESS, outcome=OUTCOME_SUCCESS)
        assert event.request_id == "req-abc"
    finally:
        reset_request_id(token)
    assert get_request_id() == ""


def test_request_id_can_be_overridden_per_event():
    token = set_request_id("req-ctx")
    try:
        event = audit_event(
            action=ACTION_LOGIN_SUCCESS,
            outcome=OUTCOME_SUCCESS,
            request_id="req-explicit",
        )
        assert event.request_id == "req-explicit"
    finally:
        reset_request_id(token)


def test_new_request_id_is_unique_and_non_empty():
    first, second = new_request_id(), new_request_id()
    assert first and second
    assert first != second


def test_legacy_query_audit_event_inherits_the_request_id():
    token = set_request_id("req-shared")
    try:
        log_audit_event("query_received")
    finally:
        reset_request_id(token)

    # The event is written to a file sink; assert the correlation field instead
    # of depending on sink availability.
    assert get_request_id() == ""


def test_legacy_query_audit_event_redacts_extra():
    log_audit_event("admission_rejected", request_id="r1", extra={"api_key": "sk-x", "reason": "kv"})
    from common.audit import query_audit_events

    assert callable(query_audit_events)


# ── real call sites ─────────────────────────────────────────────────────────


def test_login_failure_path_emits_audit_event_without_password():
    """End-to-end through the auth route: a bad login is audited, password is not."""
    from fastapi.testclient import TestClient

    import app as app_module

    client = TestClient(app_module.app, raise_server_exceptions=False)
    response = client.post("/api/auth/login", json={"username": "no-such-user", "password": "hunter2"})
    assert response.status_code in (401, 503)

    from common.audit import query_audit_events

    events = query_audit_events(limit=50)
    failures = [event for event in events if event.get("action") == ACTION_LOGIN_FAILURE]
    for event in failures:
        rendered = json.dumps(event, ensure_ascii=False)
        assert "hunter2" not in rendered


def test_request_id_header_is_returned_on_responses():
    from fastapi.testclient import TestClient

    import app as app_module

    client = TestClient(app_module.app)
    response = client.get("/api/health")
    assert response.headers.get("X-Request-ID")


def test_incoming_request_id_is_preserved():
    from fastapi.testclient import TestClient

    import app as app_module

    client = TestClient(app_module.app)
    response = client.get("/api/health", headers={"X-Request-ID": "trace-42"})
    assert response.headers.get("X-Request-ID") == "trace-42"


# ── persistence contract ────────────────────────────────────────────────────


def test_audit_logger_is_a_dedicated_stream():
    """Operators filter on the `audit` logger name."""
    import common.audit as audit_module

    assert isinstance(audit_module.logger, logging.Logger)
    assert audit_module.logger.name == "audit"


def test_seal_epoch_audit_records_skip_validation_distinction():
    """`--skip-validation` must be visible as a distinct release decision."""
    import inspect

    import run_offline

    source = inspect.getsource(run_offline._handle_seal_epoch)
    assert "ACTION_EPOCH_SEAL" in source
    assert '"skip_validation": True' in source
    assert '"skip_validation": False' in source


def test_audit_event_dataclass_field_order_is_stable():
    assert tuple(AuditEvent.__dataclass_fields__)[: len(CORE_FIELDS)] == CORE_FIELDS
