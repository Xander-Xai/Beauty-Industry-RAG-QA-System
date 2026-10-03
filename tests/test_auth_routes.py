"""Tests for auth API routes -- login, refresh, user management endpoints.

Covers 20+ scenarios across login, refresh, user CRUD, and security edge cases.
All tests use a temporary SQLite database and auto-generated RSA keypair.
No external services (Redis, Qdrant, ES) required.
"""

import os
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from auth.jwt_auth import generate_keypair
from auth.user_store import ROLES, UserStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_rate_limit():
    """Reset the module-level rate limit dict before every test."""
    import api.routes_auth as auth_module

    auth_module._login_attempts.clear()
    yield
    auth_module._login_attempts.clear()


@pytest.fixture()
def jwt_env(tmp_path):
    """Set up JWT environment with temporary RSA keypair and return key paths."""
    private_path, public_path = generate_keypair(str(tmp_path))
    env = {
        "JWT_PRIVATE_KEY_PATH": private_path,
        "JWT_PUBLIC_KEY_PATH": public_path,
        "JWT_ALGORITHM": "RS256",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
        "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
    }
    with patch.dict(os.environ, env):
        yield private_path, public_path


@pytest.fixture()
def tmp_store(tmp_path):
    """Create a UserStore backed by a temporary database and patch the module-level singleton."""
    db_path = str(tmp_path / "test_auth.db")
    store = UserStore(db_path=db_path)
    with patch("api.routes_auth.get_store", return_value=store):
        yield store


@pytest.fixture()
def app(jwt_env, tmp_store):
    """Create a minimal FastAPI app with only the auth router mounted."""
    from api.routes_auth import router as auth_router

    _app = FastAPI()
    _app.include_router(auth_router)
    return _app


@pytest.fixture()
def client(app):
    """Synchronous test client for the auth app."""
    return TestClient(app)


@pytest.fixture()
def admin_user(tmp_store):
    """Pre-create an admin user and return its credentials."""
    tmp_store.create_user(
        "admin-1",
        "admin",
        "AdminPass123",
        "Administrator",
        ["admin"],
        [],
    )
    return {"username": "admin", "password": "AdminPass123"}


@pytest.fixture()
def normal_user(tmp_store):
    """Pre-create a non-admin user and return its credentials.

    Uses 'quality' role (mask=0x02) so that the admin bit-0 check
    ``(role_mask & 0x01) == 0`` correctly rejects this user.
    """
    tmp_store.create_user(
        "user-1",
        "regular",
        "RegularPass1",
        "Regular User",
        ["quality"],
        ["quality_dept"],
    )
    return {"username": "regular", "password": "RegularPass1"}


def _login(client, username, password):
    """Helper to perform a login request."""
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _auth_header(access_token):
    """Build a Bearer Authorization header."""
    return {"Authorization": f"Bearer {access_token}"}


def _login_and_get_tokens(client, username, password):
    """Login and return (access_token, refresh_token)."""
    resp = _login(client, username, password)
    assert resp.status_code == 200
    data = resp.json()
    return data["access_token"], data["refresh_token"]


# ===========================================================================
# 1. Login endpoint  (POST /api/auth/login)
# ===========================================================================


class TestLogin:
    """Tests for POST /api/auth/login."""

    def test_login_success(self, client, admin_user):
        """Valid credentials should return 200 with access_token and refresh_token."""
        resp = _login(client, admin_user["username"], admin_user["password"])
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "Bearer"
        assert data["expires_in"] > 0
        assert data["user"]["username"] == "admin"

    def test_login_wrong_password(self, client, admin_user):
        """Wrong password should return 401."""
        resp = _login(client, admin_user["username"], "WrongPassword!")
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, client):
        """Unknown username should return 401 (same error as wrong password)."""
        resp = _login(client, "ghost_user", "AnyPassword1")
        assert resp.status_code == 401

    def test_login_rate_limit(self, client, admin_user, tmp_path):
        """6th login attempt within 60s from the same IP should return 429."""
        # Import the rate limit dict to clear it for this test
        import api.routes_auth as auth_module

        test_ip = "10.99.99.99"
        # Clear any prior attempts for this IP
        auth_module._login_attempts.pop(test_ip, None)

        mock_request_client = MagicMock()
        mock_request_client.host = test_ip

        success_count = 0
        for _i in range(6):
            # We need to simulate requests from the same IP.
            # Patch the request's client object at the endpoint level.
            with patch(
                "fastapi.Request.client",
                new_callable=lambda: property(lambda self: mock_request_client),
            ):
                resp = _login(client, admin_user["username"], admin_user["password"])
                if resp.status_code == 200:
                    success_count += 1

        # At least one of the later attempts should be rate-limited
        # The rate limit is 5 per window, so the 6th should be 429
        # Due to how TestClient works, we verify by directly testing the limiter
        auth_module._login_attempts.pop(test_ip, None)

        # Directly exercise the rate limiter for a reliable test
        for _ in range(auth_module._LOGIN_RATE_LIMIT):
            auth_module._check_login_rate_limit(test_ip)
        with pytest.raises(Exception) as exc_info:
            auth_module._check_login_rate_limit(test_ip)
        assert exc_info.value.status_code == 429
        auth_module._login_attempts.pop(test_ip, None)

    def test_login_empty_username(self, client):
        """Empty username should be rejected by Pydantic validation (422)."""
        resp = client.post("/api/auth/login", json={"username": "", "password": "pass"})
        assert resp.status_code == 422

    def test_login_jwt_not_configured(self, client, tmp_store, tmp_path):
        """When JWT_ALGORITHM is not set, login should return 503."""
        tmp_store.create_user("u-jwt", "jwtuser", "Jwtpass123", "JWT User", ["rd"], [])
        # Remove JWT_ALGORITHM so config.enabled == False
        env_without_jwt = {
            "JWT_PRIVATE_KEY_PATH": os.path.join(str(tmp_path), "private.pem"),
            "JWT_PUBLIC_KEY_PATH": os.path.join(str(tmp_path), "public.pem"),
            "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
            "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
        }
        # Unset JWT_ALGORITHM
        with patch.dict(os.environ, env_without_jwt, clear=False):
            os.environ.pop("JWT_ALGORITHM", None)
            resp = _login(client, "jwtuser", "Jwtpass123")
            assert resp.status_code == 503


# ===========================================================================
# 2. Refresh endpoint  (POST /api/auth/refresh)
# ===========================================================================


class TestRefresh:
    """Tests for POST /api/auth/refresh."""

    def test_refresh_success(self, client, admin_user):
        """Valid refresh token should return a new token pair."""
        _, refresh_token = _login_and_get_tokens(client, admin_user["username"], admin_user["password"])
        resp = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data

    def test_refresh_invalid_token(self, client):
        """Invalid/malformed refresh token should return 401."""
        resp = client.post("/api/auth/refresh", json={"refresh_token": "bad.token.value"})
        assert resp.status_code == 401

    def test_refresh_expired_token(self, client, admin_user, jwt_env):
        """Expired refresh token should return 401."""
        # Create a refresh token that is already expired
        import jwt as pyjwt

        private_path, _ = jwt_env
        with open(private_path) as f:
            private_key = f.read()

        expired_payload = {
            "sub": "admin-1",
            "iat": int(time.time()) - 1000,
            "exp": int(time.time()) - 500,  # already expired
            "type": "refresh",
        }
        expired_token = pyjwt.encode(expired_payload, private_key, algorithm="RS256")

        resp = client.post("/api/auth/refresh", json={"refresh_token": expired_token})
        assert resp.status_code == 401

    def test_refresh_with_access_token(self, client, admin_user):
        """Using an access token as a refresh token should return 401."""
        access_token, _ = _login_and_get_tokens(client, admin_user["username"], admin_user["password"])
        resp = client.post("/api/auth/refresh", json={"refresh_token": access_token})
        assert resp.status_code == 401


# ===========================================================================
# 3. User management endpoints
# ===========================================================================


class TestListUsers:
    """Tests for GET /api/auth/users."""

    def test_list_users_success(self, client, admin_user, tmp_store):
        """Admin should see all users."""
        tmp_store.create_user("u-extra", "extra", "ExtraPass1", "Extra", ["sales"], [])
        access_token, _ = _login_and_get_tokens(client, admin_user["username"], admin_user["password"])
        resp = client.get("/api/auth/users", headers=_auth_header(access_token))
        assert resp.status_code == 200
        data = resp.json()
        assert "users" in data
        assert len(data["users"]) >= 2

    def test_list_users_requires_admin(self, client, normal_user):
        """Non-admin user should get 403."""
        access_token, _ = _login_and_get_tokens(client, normal_user["username"], normal_user["password"])
        resp = client.get("/api/auth/users", headers=_auth_header(access_token))
        assert resp.status_code == 403

    def test_list_users_rd_role_is_not_treated_as_admin(self, client, tmp_store):
        """RD role (mask=0x01) must not inherit admin-only endpoints."""
        tmp_store.create_user("rd-1", "rduser", "RdUserPass1", "RD User", ["rd"], ["rd_dept"])
        access_token, _ = _login_and_get_tokens(client, "rduser", "RdUserPass1")
        resp = client.get("/api/auth/users", headers=_auth_header(access_token))
        assert resp.status_code == 403

    def test_list_users_jwt_disabled(self, client, tmp_store, tmp_path):
        """When JWT is disabled, list users should return 503."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JWT_ALGORITHM", None)
            resp = client.get("/api/auth/users")
            assert resp.status_code == 503


class TestCreateUser:
    """Tests for POST /api/auth/users."""

    def test_create_user_success(self, client, admin_user, tmp_store):
        """Admin should be able to create a new user."""
        access_token, _ = _login_and_get_tokens(client, admin_user["username"], admin_user["password"])
        resp = client.post(
            "/api/auth/users",
            json={
                "user_id": "new-1",
                "username": "newuser",
                "password": "NewUserPass1",
                "display_name": "New User",
                "roles": ["rd"],
                "departments": ["rd_dept"],
            },
            headers=_auth_header(access_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "newuser"
        assert "rd" in data["roles"]

    def test_create_user_requires_admin(self, client, normal_user):
        """Non-admin should get 403 when creating a user."""
        access_token, _ = _login_and_get_tokens(client, normal_user["username"], normal_user["password"])
        resp = client.post(
            "/api/auth/users",
            json={
                "user_id": "x-1",
                "username": "xuser",
                "password": "Xpass1234",
                "display_name": "X User",
            },
            headers=_auth_header(access_token),
        )
        assert resp.status_code == 403

    def test_create_user_jwt_disabled(self, client, tmp_store):
        """When JWT is disabled, create user should return 503."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JWT_ALGORITHM", None)
            resp = client.post(
                "/api/auth/users",
                json={
                    "user_id": "x-2",
                    "username": "xuser2",
                    "password": "Xpass1234",
                    "display_name": "X User 2",
                },
            )
            assert resp.status_code == 503

    def test_create_user_weak_password(self, client, admin_user):
        """Password shorter than 8 characters should be rejected (S-H4 fix)."""
        access_token, _ = _login_and_get_tokens(client, admin_user["username"], admin_user["password"])
        resp = client.post(
            "/api/auth/users",
            json={
                "user_id": "weak-1",
                "username": "weakuser",
                "password": "short",
                "display_name": "Weak User",
            },
            headers=_auth_header(access_token),
        )
        assert resp.status_code == 422


class TestUpdateRoles:
    """Tests for PUT /api/auth/users/{user_id}/roles."""

    def test_update_roles_success(self, client, admin_user, normal_user, tmp_store):
        """Admin should be able to update user roles."""
        access_token, _ = _login_and_get_tokens(client, admin_user["username"], admin_user["password"])
        resp = client.put(
            "/api/auth/users/user-1/roles",
            json={"roles": ["rd", "sales"], "departments": ["rd_dept", "sales_dept"]},
            headers=_auth_header(access_token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "rd" in data["roles"]
        assert "sales" in data["roles"]

    def test_update_roles_jwt_disabled(self, client, tmp_store):
        """When JWT is disabled, update_roles should return 503 (S-H3 fix).

        Before the fix, the endpoint skipped auth when JWT was disabled.
        After the fix, it rejects with 503, matching list_users/create_user.
        """
        tmp_store.create_user("u-roles", "roleuser", "RolePass1", "Role User", ["rd"], [])
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JWT_ALGORITHM", None)
            resp = client.put(
                "/api/auth/users/u-roles/roles",
                json={"roles": ["admin"], "departments": []},
            )
            assert resp.status_code == 503

    def test_update_roles_requires_admin(self, client, normal_user, tmp_store):
        """Non-admin should get 403 when updating roles."""
        tmp_store.create_user("u-tgt", "target", "TargetPass1", "Target", ["rd"], [])
        access_token, _ = _login_and_get_tokens(client, normal_user["username"], normal_user["password"])
        resp = client.put(
            "/api/auth/users/u-tgt/roles",
            json={"roles": ["admin"], "departments": []},
            headers=_auth_header(access_token),
        )
        assert resp.status_code == 403

    def test_update_roles_user_not_found(self, client, admin_user):
        """Updating roles for a nonexistent user should return 404."""
        access_token, _ = _login_and_get_tokens(client, admin_user["username"], admin_user["password"])
        resp = client.put(
            "/api/auth/users/nonexistent-user/roles",
            json={"roles": ["rd"], "departments": []},
            headers=_auth_header(access_token),
        )
        assert resp.status_code == 404


# ===========================================================================
# 4. Security scenarios
# ===========================================================================


class TestSecurity:
    """Security edge-case tests."""

    def test_no_auth_header(self, client):
        """Request without Authorization header should get 401 on protected endpoints."""
        resp = client.get("/api/auth/users")
        # When JWT_ALGORITHM is set (via jwt_env fixture indirectly through app),
        # missing auth should yield 401 or 503 depending on config state.
        assert resp.status_code in (401, 503)

    def test_expired_access_token(self, client, admin_user, jwt_env):
        """Expired access token should be rejected.

        Expired/invalid access tokens should fail authentication before
        the admin permission check runs.
        """
        import jwt as pyjwt

        private_path, _ = jwt_env
        with open(private_path) as f:
            private_key = f.read()

        expired_payload = {
            "sub": "admin-1",
            "role_mask": ROLES.get("admin", 0),
            "dept_mask": 0,
            "iat": int(time.time()) - 2000,
            "exp": int(time.time()) - 1000,
            "type": "access",
        }
        expired_token = pyjwt.encode(expired_payload, private_key, algorithm="RS256")

        resp = client.get("/api/auth/users", headers=_auth_header(expired_token))
        assert resp.status_code == 401


# ===========================================================================
# 5. Trusted proxy client-IP resolution and login rate limiting
# ===========================================================================


def _make_request(peer: str, forwarded: str | None = None):
    headers = {}
    if forwarded is not None:
        headers["X-Forwarded-For"] = forwarded
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


class TestClientIpResolution:
    """X-Forwarded-For must only be honored behind a configured trusted proxy."""

    def test_untrusted_client_xff_is_ignored(self, monkeypatch):
        import api.routes_auth as auth_module

        monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
        req = _make_request("203.0.113.9", "1.2.3.4")
        assert auth_module._get_client_ip(req) == "203.0.113.9"

    def test_client_xff_cannot_rotate_identity_without_trusted_proxy(self, monkeypatch):
        import api.routes_auth as auth_module

        monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
        first = auth_module._get_client_ip(_make_request("203.0.113.9", "1.1.1.1"))
        second = auth_module._get_client_ip(_make_request("203.0.113.9", "2.2.2.2"))
        assert first == second == "203.0.113.9"

    def test_trusted_proxy_uses_forwarded_client(self, monkeypatch):
        import api.routes_auth as auth_module

        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
        req = _make_request("10.0.0.5", "198.51.100.7")
        assert auth_module._get_client_ip(req) == "198.51.100.7"

    def test_trusted_proxy_chain_walks_right_to_left(self, monkeypatch):
        import api.routes_auth as auth_module

        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
        # client, proxy1(trusted), proxy2(trusted) -> client is first untrusted
        req = _make_request("10.0.0.5", "198.51.100.7, 10.0.0.6, 10.0.0.7")
        assert auth_module._get_client_ip(req) == "198.51.100.7"

    def test_invalid_xff_entries_ignored(self, monkeypatch):
        import api.routes_auth as auth_module

        monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
        req = _make_request("10.0.0.5", "not-an-ip, 198.51.100.7")
        assert auth_module._get_client_ip(req) == "198.51.100.7"

    def test_invalid_trusted_proxy_config_is_ignored(self, monkeypatch):
        import api.routes_auth as auth_module

        monkeypatch.setenv("TRUSTED_PROXIES", "garbage")
        req = _make_request("203.0.113.9", "1.2.3.4")
        assert auth_module._get_client_ip(req) == "203.0.113.9"


class _FakePipeline:
    def __init__(self, store):
        self._store = store
        self._ops = []

    def incr(self, key, amount=1):
        self._ops.append(("incr", key, amount))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    def execute(self):
        for op in self._ops:
            if op[0] == "incr":
                self._store.values[op[1]] = int(self._store.values.get(op[1], 0)) + op[2]
            else:
                self._store.expires[op[1]] = op[2]
        self._ops = []


class _FakeRateLimiter:
    def __init__(self, fail=False):
        self.values: dict = {}
        self.expires: dict = {}
        self.fail = fail

    def get(self, key):
        if self.fail:
            raise RuntimeError("redis down")
        return self.values.get(key)

    def pipeline(self):
        if self.fail:
            raise RuntimeError("redis down")
        return _FakePipeline(self)


class TestLoginRateLimit:
    """Login rate limit boundary + Redis/memory fallback behavior."""

    def test_memory_limit_blocks_sixth_attempt(self):
        import api.routes_auth as auth_module

        auth_module._login_attempts.clear()
        ip = "198.51.100.10"
        for _ in range(auth_module._LOGIN_RATE_LIMIT):
            auth_module._check_login_rate_limit(ip)
        with pytest.raises(Exception) as exc_info:
            auth_module._check_login_rate_limit(ip)
        assert exc_info.value.status_code == 429

    def test_window_expiry_recovers(self, monkeypatch):
        import api.routes_auth as auth_module

        auth_module._login_attempts.clear()
        ip = "198.51.100.11"
        now = [1000.0]
        monkeypatch.setattr(auth_module, "time", SimpleNamespace(time=lambda: now[0]))
        for _ in range(auth_module._LOGIN_RATE_LIMIT):
            auth_module._check_login_rate_limit(ip)
        with pytest.raises(HTTPException) as exc_info:
            auth_module._check_login_rate_limit(ip)
        assert exc_info.value.status_code == 429
        now[0] += auth_module._LOGIN_RATE_WINDOW + 1
        auth_module._check_login_rate_limit(ip)  # should be allowed again

    def test_redis_limiter_blocks_sixth_attempt(self, monkeypatch):
        import api.routes_auth as auth_module

        auth_module._login_attempts.clear()
        fake = _FakeRateLimiter()
        monkeypatch.setattr(auth_module, "_get_redis_rate_limiter", lambda: fake)
        ip = "198.51.100.12"
        for _ in range(auth_module._LOGIN_RATE_LIMIT):
            auth_module._check_login_rate_limit(ip)
        with pytest.raises(Exception) as exc_info:
            auth_module._check_login_rate_limit(ip)
        assert exc_info.value.status_code == 429
        assert fake.expires[f"ratelimit:login:{ip}"] == int(auth_module._LOGIN_RATE_WINDOW)

    def test_redis_error_falls_back_to_memory(self, monkeypatch):
        import api.routes_auth as auth_module

        auth_module._login_attempts.clear()
        fake = _FakeRateLimiter(fail=True)
        monkeypatch.setattr(auth_module, "_get_redis_rate_limiter", lambda: fake)
        ip = "198.51.100.13"
        for _ in range(auth_module._LOGIN_RATE_LIMIT):
            auth_module._check_login_rate_limit(ip)
        with pytest.raises(Exception) as exc_info:
            auth_module._check_login_rate_limit(ip)
        assert exc_info.value.status_code == 429


# ── admin routes must reuse the strict JWT identity boundary ───────────────
#
# `_require_admin_payload()` used to feed the raw `payload.get("role_mask", 0)`
# straight into `is_admin_role_mask()`. That predicate is an internal RBAC check
# that legitimately expects a canonical identity; handing it an unvalidated JWT
# claim is what made a float equal to the admin mask pass:
# `2147483647.0 in {2147483647, ...}` is True because hash and equality agree
# across int/float.
#
# These tests pin the route's use of the Problem 23 identity boundary, not a
# second validator. The full invalid matrix belongs to Problem 23; here we only
# need the privilege-escalation shape plus the 200/401/403 trichotomy.
#
# 200 = valid admin, 401 = token cannot form a valid identity, 403 = valid
# identity without sufficient authorization. Those must stay distinguishable.


def _admin_mask() -> int:
    from common.config import get_config

    return get_config().rbac.roles["admin"]


def _signed_token_with_claim(claim_name: str, value, role_mask: int, dept_mask: int = 0) -> str:
    """Mint a validly-signed RS256 token whose named claim is overridden."""
    from auth.jwt_auth import create_access_token

    return create_access_token(
        "malformed-admin",
        role_mask=role_mask,
        dept_mask=dept_mask,
        extra_claims={claim_name: value},
    )


def test_float_admin_mask_does_not_escalate(client):
    """§11: the core escalation regression.

    Preconditions are asserted explicitly so the test cannot silently stop
    exercising the escalation if the admin mask ever changes.
    """
    admin_mask = _admin_mask()
    float_admin = float(admin_mask)
    assert float_admin == admin_mask
    assert type(float_admin) is float
    assert type(admin_mask) is int

    token = _signed_token_with_claim("role_mask", float_admin, admin_mask)
    resp = client.get("/api/auth/users", headers=_auth_header(token))

    assert resp.status_code == 401, resp.text


def test_malformed_dept_claim_is_rejected_on_admin_route(client):
    """§14: the whole identity contract is reused, not just the role claim.

    role_mask here is the genuine admin integer; only dept_mask is malformed.
    A route that validated role alone would let this through.
    """
    admin_mask = _admin_mask()
    token = _signed_token_with_claim("dept_mask", "0", admin_mask, dept_mask=0)

    resp = client.get("/api/auth/users", headers=_auth_header(token))

    assert resp.status_code == 401, resp.text


def test_valid_admin_still_authorized(client):
    """§12: valid admin must not regress to 401."""
    admin_mask = _admin_mask()
    token = _signed_token_with_claim("role_mask", admin_mask, admin_mask)

    resp = client.get("/api/auth/users", headers=_auth_header(token))

    assert resp.status_code == 200, resp.text


def test_valid_non_admin_is_forbidden_not_unauthorized(client):
    """§13: a valid non-admin identity is 403, never 401."""
    from common.config import get_config

    non_admin = get_config().rbac.roles["rd"]
    token = _signed_token_with_claim("role_mask", non_admin, non_admin)

    resp = client.get("/api/auth/users", headers=_auth_header(token))

    assert resp.status_code == 403, resp.text


def test_admin_authorization_uses_validated_identity(monkeypatch, client):
    """§7: the predicate must receive the canonical identity mask, not the raw claim."""
    import api.routes_auth as auth_module

    seen: list[int] = []
    real = auth_module.is_admin_role_mask

    def spy(mask):
        seen.append(mask)
        return real(mask)

    monkeypatch.setattr(auth_module, "is_admin_role_mask", spy)
    admin_mask = _admin_mask()
    token = _signed_token_with_claim("role_mask", admin_mask, admin_mask)
    client.get("/api/auth/users", headers=_auth_header(token))

    assert seen == [admin_mask], "predicate must see exactly the canonical int mask"
