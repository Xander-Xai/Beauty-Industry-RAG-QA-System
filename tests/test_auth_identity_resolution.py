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


def test_rs256_rejected_when_algorithm_disabled(monkeypatch, tmp_path):
    """Unsetting JWT_ALGORITHM must disable RS256 verification, not just login."""
    private_path, public_path = generate_keypair(str(tmp_path))
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", private_path)
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", public_path)
    monkeypatch.delenv("JWT_ALGORITHM", raising=False)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    reload_config()

    token = create_access_token("disabled_user", role_mask=0x01, dept_mask=0x01)
    resp = TestClient(_protected_app()).get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401


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


# ── JWT authorization-claim validation ─────────────────────────────────────
#
# A cryptographically valid signature proves the token was issued by the key
# holder. It says nothing about whether the permission claims inside are
# well-formed. Before this, _identity_from_jwt() passed raw claim values
# straight into UserIdentity, whose mask fields are plain `int`, so Pydantic
# coerced "1" -> 1 and a stringly-typed mask became an authenticated identity.
#
# This is identity-ingress fail-closed validation against canonical uint32
# permission claims. Token issuance is a separate concern and is not touched:
# the tokens below are signed by the real keypair via extra_claims.
#
# Only the receiving boundary is verified. Nothing here claims JWT security is
# solved or that RBAC is fully secure.


_INVALID_ROLE_CLAIMS = ["1", 1.0, True, False, -1, 0x100000000, None]
_INVALID_DEPT_CLAIMS = ["2", 2.0, True, False, -1, 0x100000000, None]


def _rs256_token_with_claim(monkeypatch, tmp_path, claim_name, value):
    """Mint a validly-signed RS256 access token whose permission claim is malformed."""
    _set_rs256_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    reload_config()
    return create_access_token(
        "bad_mask_user",
        role_mask=1,
        dept_mask=2,
        extra_claims={claim_name: value},
    )


@pytest.mark.parametrize("value", _INVALID_ROLE_CLAIMS, ids=repr)
def test_malformed_role_mask_claim_is_rejected(monkeypatch, tmp_path, value):
    token = _rs256_token_with_claim(monkeypatch, tmp_path, "role_mask", value)
    resp = TestClient(_protected_app()).get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401, f"role_mask={value!r} must not authenticate"


@pytest.mark.parametrize("value", _INVALID_DEPT_CLAIMS, ids=repr)
def test_malformed_dept_mask_claim_is_rejected(monkeypatch, tmp_path, value):
    token = _rs256_token_with_claim(monkeypatch, tmp_path, "dept_mask", value)
    resp = TestClient(_protected_app()).get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401, f"dept_mask={value!r} must not authenticate"


def test_explicit_null_claim_is_distinguished_from_absent_claim(monkeypatch, tmp_path):
    """`role_mask: null` is a malformed claim, not an absent one."""
    from common.auth import _identity_from_jwt

    with pytest.raises(ValueError):
        _identity_from_jwt({"sub": "u", "role_mask": None, "dept_mask": 2})

    # Absent on both counts keeps the documented zero-mask default.
    identity = _identity_from_jwt({"sub": "u"})
    assert identity.user_role_mask == 0
    assert identity.user_dept_mask == 0


def test_present_but_invalid_claim_does_not_fall_back_to_named_roles():
    """A malformed explicit claim must not be silently ignored in favour of `roles`."""
    from common.auth import _identity_from_jwt

    with pytest.raises(ValueError):
        _identity_from_jwt({"sub": "u", "role_mask": "1", "roles": ["researcher"]})


def test_named_role_fallback_still_works_when_claim_absent():
    """§9/§18: absent claim still encodes from roles/depts."""
    from common.auth import _identity_from_jwt, encode_dept_mask, encode_role_mask

    identity = _identity_from_jwt({"sub": "u", "roles": ["researcher"], "depts": ["rd"]})
    assert identity.user_role_mask == encode_role_mask(["researcher"])
    assert identity.user_dept_mask == encode_dept_mask(["rd"])


@pytest.mark.parametrize("value", [0, 1, 0x7FFFFFFF, 0xFFFFFFFF], ids=repr)
def test_valid_uint32_boundaries_are_accepted(monkeypatch, tmp_path, value):
    """§15: 0 and 0xFFFFFFFF are valid uint32 identities."""
    _set_rs256_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    reload_config()
    token = create_access_token("boundary_user", role_mask=value, dept_mask=value)
    resp = TestClient(_protected_app()).get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, f"uint32 {value!r} must authenticate"
    assert resp.json()["role_mask"] == value


def test_legacy_hs256_malformed_claim_is_rejected(monkeypatch):
    """§17/§18: the legacy HS256 fallback shares the same identity boundary."""
    import jwt as pyjwt

    secret = "legacy-compat-secret-key-at-least-32-bytes"
    monkeypatch.setenv("JWT_SECRET", secret)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    reload_config()

    now = int(time.time())
    token = pyjwt.encode(
        {
            "sub": "legacy_bad",
            "user_id": "legacy_bad",
            "role_mask": "4",
            "dept_mask": 4,
            "iat": now,
            "exp": now + 3600,
        },
        secret,
        algorithm="HS256",
    )
    resp = TestClient(_protected_app()).get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_validator_rejects_coercion_and_out_of_range():
    """§6: the helper itself must not coerce."""
    from common.auth import _validate_permission_mask_claim

    assert _validate_permission_mask_claim(0, "role_mask") == 0
    assert _validate_permission_mask_claim(0xFFFFFFFF, "role_mask") == 0xFFFFFFFF
    for bad in ("1", 1.0, True, False, -1, 0x100000000, None, [], {}):
        with pytest.raises(ValueError):
            _validate_permission_mask_claim(bad, "role_mask")
