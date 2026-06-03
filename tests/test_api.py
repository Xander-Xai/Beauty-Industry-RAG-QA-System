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
    def test_query_returns_200(self, client):
        """POST /api/query 返回 200"""
        response = client.post("/api/query", json={
            "query": "烟酰胺的安全浓度是多少？",
        })
        assert response.status_code == 200

    def test_query_with_session(self, client):
        """带 session_id 的查询"""
        response = client.post("/api/query", json={
            "query": "玻色因的功效？",
            "session_id": "test_session_001",
        })
        assert response.status_code == 200
        data = response.json()
        assert "answer" in data
        assert "session_id" in data

    def test_query_empty_fails(self, client):
        """空查询返回 422"""
        response = client.post("/api/query", json={"query": ""})
        assert response.status_code == 422

    def test_query_with_identity_headers(self, client):
        """带身份头的查询"""
        response = client.post("/api/query", json={
            "query": "维生素C的稳定性？",
        }, headers={
            "X-User-ID": "user_rd_001",
            "X-Role-Mask": "1",
            "X-Dept-Mask": "1",
        })
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
