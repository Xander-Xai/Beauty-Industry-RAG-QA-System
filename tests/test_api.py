"""
API 端点测试
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from auth.jwt_auth import create_access_token, generate_keypair
from common.auth import require_identity


@pytest.fixture(scope="module")
def client():
    """创建测试客户端"""
    from app import app
    return TestClient(app)


@pytest.fixture(scope="module")
def auth_header():
    """生成 JWT Authorization header 用于认证端点。

    S-C1 修复后，/api/query 等端点需要 JWT 认证。
    使用 PyJWT 直接生成 HS256 token，与 common/auth._decode_jwt 兼容。
    """
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


@pytest.fixture
def rs256_auth_header(tmp_path):
    """Generate an RS256 auth header matching the login/refresh contract."""
    private_path, public_path = generate_keypair(str(tmp_path))
    env = {
        "JWT_PRIVATE_KEY_PATH": private_path,
        "JWT_PUBLIC_KEY_PATH": public_path,
        "JWT_ALGORITHM": "RS256",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "15",
        "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
    }
    previous = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    try:
        token = create_access_token("rs256_user", role_mask=0x01, dept_mask=0x01)
        yield {"Authorization": f"Bearer {token}"}
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        """GET /api/health 返回 200"""
        response = client.get("/api/health")
        assert response.status_code == 200

    def test_health_response_structure(self, client):
        """健康检查响应结构正确"""
        response = client.get("/api/health")
        data = response.json()
        assert "status" in data
        assert "version" in data
        assert "dependencies" in data


class TestQueryEndpoint:
    def test_query_returns_200(self, client, auth_header):
        """POST /api/query 返回 200"""
        response = client.post("/api/query", json={
            "query": "烟酰胺的安全浓度是多少？",
        }, headers=auth_header)
        assert response.status_code == 200

    def test_query_with_session(self, client, auth_header):
        """带 session_id 的查询"""
        response = client.post("/api/query", json={
            "query": "玻色因的功效？",
            "session_id": "test_session_001",
        }, headers=auth_header)
        assert response.status_code == 200
        data = response.json()
        assert "answer" in data
        assert "session_id" in data

    def test_query_empty_fails(self, client, auth_header):
        """空查询返回 422"""
        response = client.post("/api/query", json={"query": ""}, headers=auth_header)
        assert response.status_code == 422

    def test_query_unauthenticated_fails(self, client):
        """S-C1: 无认证请求返回 401（dev_mode=false 时生效）"""
        from common.config import get_config
        if get_config().auth.dev_mode:
            pytest.skip("dev_mode=true 时无认证请求允许通过")
        response = client.post("/api/query", json={
            "query": "测试查询",
        })
        assert response.status_code == 401

    def test_query_with_dev_headers(self, client, auth_header):
        """带 JWT token 的查询"""
        response = client.post("/api/query", json={
            "query": "维生素C的稳定性？",
        }, headers=auth_header)
        assert response.status_code == 200

    def test_require_identity_accepts_rs256_login_tokens(self, rs256_auth_header):
        """业务鉴权依赖应接受登录端点签发的 RS256 access token。"""
        app = FastAPI()

        @app.get("/protected")
        async def protected(identity=Depends(require_identity)):
            return {
                "user_id": identity.user_id,
                "role_mask": identity.user_role_mask,
                "dept_mask": identity.user_dept_mask,
            }

        client = TestClient(app)
        response = client.get("/protected", headers=rs256_auth_header)
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == "rs256_user"
        assert data["role_mask"] == 0x01
        assert data["dept_mask"] == 0x01


class TestStatsEndpoint:
    def test_stats_returns_200(self, client):
        """GET /api/stats 返回 200"""
        response = client.get("/api/stats")
        assert response.status_code == 200

    def test_stats_structure(self, client):
        """统计响应结构"""
        response = client.get("/api/stats")
        data = response.json()
        assert "uptime_seconds" in data
        assert "cache_hit_rate" in data
        assert "rewrite_fallback_rate" in data
        assert "latency_percentiles" in data
