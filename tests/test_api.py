"""
API 端点测试
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient


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
    import jwt as _jwt
    import time
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
        """S-C1: 无认证请求返回 401"""
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
