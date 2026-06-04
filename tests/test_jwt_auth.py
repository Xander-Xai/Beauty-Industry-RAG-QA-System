import os
import tempfile
import pytest
from unittest.mock import patch
from auth.jwt_auth import (
    generate_keypair, create_token_pair, create_access_token, verify_token,
    extract_token_from_header, JWTConfig, get_jwt_config,
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
