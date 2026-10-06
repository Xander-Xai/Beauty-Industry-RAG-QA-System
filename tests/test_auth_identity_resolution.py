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
# permission claims. Token issuance is a separate concern and is deliberately
# NOT exercised here: the tokens below are signed directly with the test
# private key, exactly as a different issuer or an attacker holding the key
# would produce them. That keeps issuer ownership and receiver validation as
# two independently testable defences — see tests/test_jwt_auth.py for the
# issuance-side reserved-claim contract.
#
# Only the receiving boundary is verified. Nothing here claims JWT security is
# solved or that RBAC is fully secure.


_INVALID_ROLE_CLAIMS = ["1", 1.0, True, False, -1, 0x100000000, None]
_INVALID_DEPT_CLAIMS = ["2", 2.0, True, False, -1, 0x100000000, None]


def _rs256_token_with_claim(monkeypatch, tmp_path, claim_name, value):
    """Sign an RS256 access token whose named permission claim is malformed.

    Deliberately does NOT go through ``create_access_token()``. That issuer
    refuses to let ``extra_claims`` shadow the claims it owns, which is the
    correct production behaviour but would make this test unable to state its
    own premise: a *validly signed token carrying malformed claims*.

    Signing directly is exactly how such a token would arrive — from another
    issuer, a compromised path, or anyone holding the key. It also keeps this
    receiver-side assertion from silently degrading into a restatement of the
    issuance-side guard.
    """
    import jwt as pyjwt

    from auth.jwt_auth import get_jwt_config

    _set_rs256_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    reload_config()

    config = get_jwt_config()
    with open(config.private_key_path) as handle:
        private_key = handle.read()

    now = int(time.time())
    payload = {
        "sub": "bad_mask_user",
        "role_mask": 1,
        "dept_mask": 2,
        "iat": now,
        "exp": now + 900,
        "type": "access",
    }
    payload[claim_name] = value

    return pyjwt.encode(payload, private_key, algorithm=config.algorithm)


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


# ── AUTH_DEV_MODE header permission masks ────────────────────────────────
#
# `AUTH_DEV_MODE` is a second identity source, so a mask arriving in
# `X-Role-Mask` / `X-Dept-Mask` is the same kind of value as a JWT permission
# claim and is held to the same canonical uint32 contract, by the same
# validator. This branch previously read `int(request.headers.get(...))`:
#
#   * a mask outside [0, 2**32-1] was accepted, so `-1` reached UserIdentity
#     and `-1 & mask` overlaps every document mask;
#   * a non-integer header raised an unhandled ValueError and returned HTTP 500
#     instead of an authorization decision.
#
# Only the receiving boundary is verified here. `AUTH_DEV_MODE=true` is not a
# production posture and nothing below claims production identity validation.


_MASK_HEADERS = ["X-Role-Mask", "X-Dept-Mask"]
_MASK_RESPONSE_FIELDS = {"X-Role-Mask": "role_mask", "X-Dept-Mask": "dept_mask"}

#: Valid canonical uint32 masks, spanning the whole accepted range.
_VALID_DEV_MASKS = ["0", "1", "2", "4", "7", "2147483647", "4294967295"]

#: Malformed headers that *decode* to an integer, so a value exists to be judged.
#: Each is out of the canonical uint32 range.
_OUT_OF_RANGE_DEV_MASKS = [
    "-1",
    "-4294967295",
    "4294967296",  # 2**32
    "4294967297",  # 2**32 + 1
    "1099511627776",  # 2**40
    "340282366920938463463374607431768211456",  # 2**128
]

#: Malformed headers that do *not* represent an integer at all, so they must be
#: refused before any value exists. `"1.0"` and `"1e3"` are the float-like
#: strings a truncating parser would turn into mask 1; `"+7"` is deliberately
#: absent because it decodes to the valid integer 7.
_UNPARSABLE_DEV_MASKS = [
    "1.0",
    "1e3",
    "abc",
    "",
    "   ",
    "0x10",
    "+-1",
    "1,0",
    "NaN",
    "inf",
]


class _RequestStub:
    """Minimal header-mapping stand-in for the identity-ingress helpers."""

    def __init__(self, headers: dict):
        self.headers = headers


@pytest.fixture
def _dev_mode(monkeypatch):
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    reload_config()


@pytest.mark.parametrize("header", _MASK_HEADERS)
@pytest.mark.parametrize("mask", _VALID_DEV_MASKS, ids=repr)
def test_valid_uint32_dev_header_mask_is_accepted(_dev_mode, header, mask):
    """The whole canonical uint32 range, both bounds included, is a dev-mode identity."""
    resp = TestClient(_protected_app()).get(
        "/protected",
        headers={"X-User-ID": "dev_user", header: mask},
    )

    assert resp.status_code == 200, f"{header}: {mask!r} must authenticate"
    body = resp.json()
    assert body[_MASK_RESPONSE_FIELDS[header]] == int(mask)
    assert body["user_id"] == "dev_user"


@pytest.mark.parametrize("header", _MASK_HEADERS)
@pytest.mark.parametrize("mask", _OUT_OF_RANGE_DEV_MASKS + _UNPARSABLE_DEV_MASKS, ids=repr)
def test_malformed_dev_header_mask_fails_closed(_dev_mode, header, mask):
    """A header the uint32 contract rejects yields the anonymous zero-mask identity.

    Two properties in one assertion: the claimed permission never survives, and
    the rejection is an authorization decision rather than a server error. The
    pre-fix behaviour was 200-with-an-out-of-range-mask for the numeric cases and
    HTTP 500 for the unparsable ones.
    """
    resp = TestClient(_protected_app()).get(
        "/protected",
        headers={"X-User-ID": "dev_user", header: mask},
    )

    assert resp.status_code == 200, f"{header}: {mask!r} must fail closed, not error out"
    assert resp.json() == {"user_id": "anonymous", "role_mask": 0, "dept_mask": 0}


@pytest.mark.parametrize("header", _MASK_HEADERS)
def test_absent_dev_header_mask_keeps_the_documented_zero_default(_dev_mode, header):
    """An absent header is the pre-existing default of mask 0, itself a valid uint32.

    Pinned explicitly so the absent case cannot be silently redefined into a
    rejection: dev mode has always defaulted an omitted mask to 0, and 0 already
    fails closed because zero masks clear no restricted document.
    """
    other = "X-Dept-Mask" if header == "X-Role-Mask" else "X-Role-Mask"
    resp = TestClient(_protected_app()).get(
        "/protected",
        headers={"X-User-ID": "dev_user", other: "3"},
    )

    assert resp.status_code == 200
    body = resp.json()
    expected = {"role_mask": 3, "dept_mask": 3}
    expected[_MASK_RESPONSE_FIELDS[header]] = 0
    assert body == {"user_id": "dev_user", **expected}


def test_both_masks_valid_at_the_upper_bound_together(_dev_mode):
    """2**32-1 on both headers at once is still exactly one valid identity."""
    resp = TestClient(_protected_app()).get(
        "/protected",
        headers={"X-User-ID": "dev_user", "X-Role-Mask": "4294967295", "X-Dept-Mask": "4294967295"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"user_id": "dev_user", "role_mask": 0xFFFFFFFF, "dept_mask": 0xFFFFFFFF}


def test_one_valid_and_one_malformed_mask_fails_closed(_dev_mode):
    """Both masks are validated, so a good header must not rescue a bad one."""
    resp = TestClient(_protected_app()).get(
        "/protected",
        headers={"X-User-ID": "dev_user", "X-Role-Mask": "4", "X-Dept-Mask": "-1"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"user_id": "anonymous", "role_mask": 0, "dept_mask": 0}


def test_valid_dev_header_happy_path_is_unregressed(_dev_mode):
    """The pre-existing happy path still produces the caller-named identity."""
    resp = TestClient(_protected_app()).get(
        "/protected",
        headers={"X-User-ID": "dev_user", "X-Role-Mask": "1", "X-Dept-Mask": "2"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"user_id": "dev_user", "role_mask": 1, "dept_mask": 2}


def test_dev_header_masks_are_validated_before_useridentity_construction(_dev_mode):
    """No Pydantic laundering: the ingress helper raises instead of coercing.

    `UserIdentity`'s mask fields are plain `int``, so Pydantic will happily
    accept a malformed value -- which is precisely why the check has to happen at
    ingress. This asserts both halves of that argument: the model really would
    launder the value, and the dev-header path really does refuse it before the
    model is reached.
    """
    from common.auth import _identity_from_dev_headers
    from common.models import UserIdentity

    # The laundering this contract exists to prevent, asserted so the test below
    # cannot pass by accident if the model ever became strict.
    assert UserIdentity(user_role_mask="1").user_role_mask == 1
    assert UserIdentity(user_role_mask="-1").user_role_mask == -1
    assert UserIdentity(user_role_mask=1.0).user_role_mask == 1

    with pytest.raises(ValueError, match=r"within \[0, 4294967295\]"):
        _identity_from_dev_headers(_RequestStub({"X-Role-Mask": "-1", "X-Dept-Mask": "2"}))

    identity = _identity_from_dev_headers(_RequestStub({"X-Role-Mask": "1", "X-Dept-Mask": "2"}))
    assert (identity.user_role_mask, identity.user_dept_mask) == (1, 2)


@pytest.mark.parametrize("mask", _VALID_DEV_MASKS, ids=repr)
def test_dev_and_jwt_paths_accept_exactly_the_same_masks(mask, _dev_mode):
    """One contract, two ingress paths: the accepted set is identical."""
    from common.auth import _identity_from_dev_headers, _identity_from_jwt

    value = int(mask)
    dev_identity = _identity_from_dev_headers(_RequestStub({"X-Role-Mask": mask, "X-Dept-Mask": mask}))
    jwt_identity = _identity_from_jwt({"sub": "u", "role_mask": value, "dept_mask": value})

    assert (dev_identity.user_role_mask, dev_identity.user_dept_mask) == (value, value)
    assert (jwt_identity.user_role_mask, jwt_identity.user_dept_mask) == (value, value)


@pytest.mark.parametrize("mask", _OUT_OF_RANGE_DEV_MASKS, ids=repr)
def test_dev_and_jwt_paths_reject_the_same_out_of_range_values(mask, _dev_mode):
    """A decoded header is judged by the JWT path's range rule, verbatim.

    Compared on the decoded integer because a JWT claim cannot be a string: the
    shared value class has to reach the same verdict on both ingress paths.
    """
    from common.auth import _identity_from_dev_headers, _identity_from_jwt

    value = int(mask)

    with pytest.raises(ValueError, match=r"within \[0, 4294967295\]"):
        _identity_from_dev_headers(_RequestStub({"X-Role-Mask": mask, "X-Dept-Mask": mask}))

    with pytest.raises(ValueError, match=r"within \[0, 4294967295\]"):
        _identity_from_jwt({"sub": "u", "role_mask": value, "dept_mask": value})


@pytest.mark.parametrize("mask", _UNPARSABLE_DEV_MASKS, ids=repr)
def test_unparsable_dev_header_mask_is_refused_before_any_value_exists(mask, _dev_mode):
    """A header that is not an integer never reaches the range rule at all."""
    from common.auth import _identity_from_dev_headers

    with pytest.raises(ValueError, match="base-10 integer"):
        _identity_from_dev_headers(_RequestStub({"X-Role-Mask": mask, "X-Dept-Mask": mask}))


def test_dev_header_ingress_calls_the_canonical_validator(monkeypatch, _dev_mode):
    """Structural proof that there is one validator, not two look-alike rule sets.

    Replaces the module's validator with a sentinel and asserts the dev-header
    ingress actually calls it. A second, copied range check inside the dev-header
    branch would leave this sentinel uncalled and fail.
    """
    import common.auth as auth_module

    calls = []
    original = auth_module._validate_permission_mask_claim

    def _spy(value, source):
        calls.append((value, source))
        return original(value, source)

    monkeypatch.setattr(auth_module, "_validate_permission_mask_claim", _spy)

    identity = auth_module._identity_from_dev_headers(_RequestStub({"X-Role-Mask": "5", "X-Dept-Mask": "6"}))
    assert (identity.user_role_mask, identity.user_dept_mask) == (5, 6)
    assert calls == [(5, "X-Role-Mask"), (6, "X-Dept-Mask")]

    calls.clear()
    with pytest.raises(ValueError):
        auth_module._identity_from_dev_headers(_RequestStub({"X-Role-Mask": "4294967296", "X-Dept-Mask": "6"}))
    assert calls == [(0x100000000, "X-Role-Mask")]
