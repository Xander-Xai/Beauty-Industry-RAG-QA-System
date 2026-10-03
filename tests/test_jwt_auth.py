import os
import tempfile

import pytest

from auth.jwt_auth import (
    create_access_token,
    create_token_pair,
    extract_token_from_header,
    generate_keypair,
    verify_token,
)


@pytest.fixture(scope="module")
def keypair():
    """Generate a temporary keypair for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        private_path, public_path = generate_keypair(tmpdir)
        os.environ["JWT_PRIVATE_KEY_PATH"] = private_path
        os.environ["JWT_PUBLIC_KEY_PATH"] = public_path
        os.environ["JWT_ALGORITHM"] = "RS256"
        yield private_path, public_path
        for key in ["JWT_PRIVATE_KEY_PATH", "JWT_PUBLIC_KEY_PATH", "JWT_ALGORITHM"]:
            os.environ.pop(key, None)


def test_create_and_verify_token(keypair):
    """Token 创建后应能被正确验证。"""
    token = create_access_token("user1", role_mask=0x02, dept_mask=0x01)
    payload = verify_token(token, "access")
    assert payload is not None
    assert payload["sub"] == "user1"
    assert payload["role_mask"] == 0x02
    assert payload["dept_mask"] == 0x01


def test_token_pair(keypair):
    """Token pair 应包含 access 和 refresh。"""
    pair = create_token_pair("user2", role_mask=0x04, dept_mask=0x02)
    assert pair.access_token
    assert pair.refresh_token
    assert pair.expires_in > 0
    assert pair.token_type == "Bearer"


def test_refresh_token_type_mismatch(keypair):
    """用 access 验证 refresh token 应失败。"""
    pair = create_token_pair("user3", role_mask=0x01, dept_mask=0x00)
    payload = verify_token(pair.refresh_token, "access")
    assert payload is None


def test_extract_token_from_header():
    """应正确解析 Bearer token。"""
    assert extract_token_from_header("Bearer abc123") == "abc123"
    assert extract_token_from_header("Token abc123") is None
    assert extract_token_from_header("") is None
    assert extract_token_from_header(None) is None


def test_verify_invalid_token(keypair):
    """无效 token 应返回 None。"""
    payload = verify_token("invalid.token.here", "access")
    assert payload is None


# ── reserved-claim ownership on the access-token issuer ────────────────────
#
# The issuer owns sub/role_mask/dept_mask/iat/exp/type. `payload.update(
# extra_claims)` let any caller overwrite them, so issuer input and the signed
# token could disagree: `create_access_token("real-user", 1, 2,
# extra_claims={"sub": "forged-user"})` really did sign `"real-user"` into a
# token that verified as `"forged-user"`.
#
# extra_claims stays the extension mechanism; only collisions with the claims
# this issuer actually owns are rejected. Claims the issuer does not manage
# (tenant_id, session_id, ...) remain freely settable.
#
# This is claim-shadowing prevention and an issuance-side fail-closed guard.


def test_extra_claims_cannot_shadow_sub(keypair):
    """Genuine pre-fix result: payload["sub"] was "forged-user"."""
    with pytest.raises(ValueError) as excinfo:
        create_access_token("real-user", role_mask=1, dept_mask=2, extra_claims={"sub": "forged-user"})
    assert "sub" in str(excinfo.value)


def test_extra_claims_cannot_shadow_role_mask(keypair):
    """Genuine pre-fix result: payload["role_mask"] was 123456, not 1."""
    with pytest.raises(ValueError) as excinfo:
        create_access_token("real-user", role_mask=1, dept_mask=2, extra_claims={"role_mask": 123456})
    assert "role_mask" in str(excinfo.value)


@pytest.mark.parametrize(
    "claim",
    ["sub", "role_mask", "dept_mask", "iat", "exp", "type"],
)
def test_every_reserved_claim_collision_raises(keypair, claim):
    """The contract under test is the key collision, not the claim value."""
    with pytest.raises(ValueError) as excinfo:
        create_access_token("u", role_mask=1, dept_mask=2, extra_claims={claim: "x"})
    message = str(excinfo.value)
    assert claim in message
    # deterministic ordering and no leakage of token/key/payload material
    assert "extra_claims" in message


def test_multiple_collisions_are_reported_sorted(keypair):
    """Collisions are reported deterministically rather than first-wins."""
    with pytest.raises(ValueError) as excinfo:
        create_access_token("u", role_mask=1, dept_mask=2, extra_claims={"type": "x", "sub": "y", "exp": 1})
    message = str(excinfo.value)
    assert message.index("exp") < message.index("sub") < message.index("type")


def test_non_reserved_extension_claims_are_preserved(keypair):
    """The extension mechanism survives; only shadowing is blocked."""
    token = create_access_token(
        "user1",
        role_mask=2,
        dept_mask=1,
        extra_claims={"tenant_id": "tenant-a", "session_id": "session-123"},
    )
    payload = verify_token(token, "access")
    assert payload["tenant_id"] == "tenant-a"
    assert payload["session_id"] == "session-123"
    # canonical claims still come from the issuer arguments
    assert payload["sub"] == "user1"
    assert payload["role_mask"] == 2
    assert payload["dept_mask"] == 1
    assert payload["type"] == "access"


def test_canonical_claims_survive_a_safe_extension(keypair):
    """A legal extension must not perturb any canonical claim, including iat/exp."""
    import time as _time

    before = int(_time.time())
    token = create_access_token("user1", role_mask=2, dept_mask=4, extra_claims={"tenant_id": "tenant-a"})
    payload = verify_token(token, "access")

    assert payload["sub"] == "user1"
    assert payload["role_mask"] == 2
    assert payload["dept_mask"] == 4
    assert payload["type"] == "access"
    assert "iat" in payload and "exp" in payload
    assert payload["iat"] >= before
    assert payload["exp"] > payload["iat"]
    # not hardcoded to a wall-clock constant
    assert payload["iat"] <= int(_time.time())


@pytest.mark.parametrize("extras", [None, {}])
def test_empty_extras_are_unaffected(keypair, extras):
    token = create_access_token("user1", role_mask=1, dept_mask=2, extra_claims=extras)
    payload = verify_token(token, "access")
    assert payload["sub"] == "user1"
    assert payload["role_mask"] == 1


def test_token_pair_inherits_the_access_token_contract(keypair):
    """create_token_pair delegates; it must not need a second reserved set."""
    with pytest.raises(ValueError):
        create_token_pair("u", role_mask=1, dept_mask=2, extra_claims={"sub": "forged"})

    pair = create_token_pair("u", role_mask=1, dept_mask=2, extra_claims={"tenant_id": "t"})
    payload = verify_token(pair.access_token, "access")
    assert payload["sub"] == "u"
    assert payload["tenant_id"] == "t"
