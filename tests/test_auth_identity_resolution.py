"""Identity-resolution contract tests for the online auth path.

The browser login flow issues RS256 access tokens via ``auth.jwt_auth``. Bearer
verification must not depend on the legacy HS256 ``JWT_SECRET`` being set:
legacy HS256 is an optional backward-compatibility fallback.

These tests exercise ``common.auth.parse_identity`` through a real
``require_identity`` dependency rather than overriding it, so the actual token
verification order is covered.
"""

from __future__ import annotations

import time

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from auth.jwt_auth import create_access_token, generate_keypair
from common.auth import require_identity
from common.config import reload_config

_AUTH_ENV_KEYS = (
    "JWT_SECRET",
    "JWT_PRIVATE_KEY_PATH",
    "JWT_PUBLIC_KEY_PATH",
    "JWT_ALGORITHM",
    "JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
    "JWT_REFRESH_TOKEN_EXPIRE_DAYS",
    "AUTH_DEV_MODE",
)


def _protected_app() -> FastAPI:
    app = FastAPI()

    @app.get("/protected")
    async def protected(identity=Depends(require_identity)):
        return {
            "user_id": identity.user_id,
            "role_mask": identity.user_role_mask,
            "dept_mask": identity.user_dept_mask,
        }

    return app


@pytest.fixture(autouse=True)
def _clean_auth_env(monkeypatch):
    """Start each test from an unset auth environment and a fresh config."""
    for key in _AUTH_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    reload_config()
    yield
    reload_config()


def _set_rs256_env(monkeypatch, tmp_path):
    private_path, public_path = generate_keypair(str(tmp_path))
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", private_path)
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", public_path)
    monkeypatch.setenv("JWT_ALGORITHM", "RS256")
    return private_path, public_path


def test_rs256_token_accepted_without_legacy_secret(monkeypatch, tmp_path):
    """RS256 login tokens must verify even when JWT_SECRET is unset."""
    _set_rs256_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    reload_config()

    token = create_access_token("rs256_only_user", role_mask=0x01, dept_mask=0x02)
    resp = TestClient(_protected_app()).get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json() == {"user_id": "rs256_only_user", "role_mask": 0x01, "dept_mask": 0x02}


def test_legacy_hs256_fallback_still_accepted(monkeypatch):
    """Legacy HS256 tokens remain an optional fallback when JWT_SECRET is set."""
    import jwt as pyjwt

    secret = "legacy-compat-secret-key-at-least-32-bytes"
    monkeypatch.setenv("JWT_SECRET", secret)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    reload_config()

    now = int(time.time())
    token = pyjwt.encode(
        {
            "sub": "legacy_user",
            "user_id": "legacy_user",
            "role_mask": 0x04,
            "dept_mask": 0x04,
            "iat": now,
            "exp": now + 3600,
        },
        secret,
        algorithm="HS256",
    )
    resp = TestClient(_protected_app()).get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()["user_id"] == "legacy_user"


def test_invalid_token_is_rejected(monkeypatch, tmp_path):
    """An invalid Bearer token must not fall through to an authenticated identity."""
    _set_rs256_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    reload_config()

    resp = TestClient(_protected_app()).get("/protected", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401


def test_production_without_credentials_returns_401(monkeypatch, tmp_path):
    """Non-dev mode with no credentials must fail authentication."""
    _set_rs256_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    reload_config()

    resp = TestClient(_protected_app()).get("/protected")
    assert resp.status_code == 401


def test_dev_headers_trusted_in_dev_mode(monkeypatch):
    """X-User-* impersonation is only accepted when AUTH_DEV_MODE=true."""
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    reload_config()

    resp = TestClient(_protected_app()).get(
        "/protected",
        headers={"X-User-ID": "dev_user", "X-Role-Mask": "1", "X-Dept-Mask": "2"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"user_id": "dev_user", "role_mask": 1, "dept_mask": 2}


def test_dev_headers_ignored_when_not_dev_mode(monkeypatch, tmp_path):
    """X-User-* headers must not authenticate in non-dev mode."""
    _set_rs256_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    reload_config()

    resp = TestClient(_protected_app()).get(
        "/protected",
        headers={"X-User-ID": "dev_user", "X-Role-Mask": "1", "X-Dept-Mask": "2"},
    )
    assert resp.status_code == 401
