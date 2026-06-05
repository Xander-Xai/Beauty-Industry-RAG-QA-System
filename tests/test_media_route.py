"""
Tests for GET /api/media/{doc_id} endpoint (GAP-16)

Covers:
- Allowed access: presigned URL returned with correct structure
- Denied access: 403 when user lacks document permissions
- Not found: 404 for missing document
- Archived document: 404 when status is 'archived'
- MinIO unavailable: 503 when storage service is down
- MinIO URL generation failure: 503 when presigned URL cannot be generated
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app import app
from api.dependencies import get_identity, RequestIdentity


# ── Helpers ─────────────────────────────────────────────────────────────────


def _milvus_doc(doc_id="doc_001", role_mask=0, dept_mask=0, status="active"):
    """Return a Milvus query result list for a given doc."""
    return [{
        "role_mask": role_mask,
        "dept_mask": dept_mask,
        "status": status,
    }]


def _empty_milvus_result():
    """Return empty Milvus query result (doc not found)."""
    return []


def _mock_minio(available=True, url="https://minio.example.com/signed/test"):
    """Create a mock MinioClient."""
    mock = MagicMock()
    mock.is_available = available
    mock.get_presigned_url.return_value = url
    return mock


def _make_identity(user_id="test_user", user_role_mask=0, user_dept_mask=0):
    """Create a RequestIdentity for dependency override."""
    return RequestIdentity(
        user_id=user_id,
        user_role_mask=user_role_mask,
        user_dept_mask=user_dept_mask,
    )


def _setup_mock_pymilvus(query_result):
    """
    Create a mock pymilvus module whose Collection.query returns query_result.

    Returns (mock_pymilvus_module, mock_collection_instance) so callers can
    configure the collection mock further if needed.
    """
    mock_collection = MagicMock()
    mock_collection.query.return_value = query_result

    mock_pymilvus = MagicMock()
    mock_pymilvus.Collection.return_value = mock_collection

    return mock_pymilvus, mock_collection


# ── Allowed access ──────────────────────────────────────────────────────────


class TestMediaEndpointAllowed:
    """GET /api/media/{doc_id} returns presigned URL when user has access."""

    @patch("common.minio_client.get_minio_client")
    def test_public_doc_returns_200_with_url(self, mock_get_minio):
        """Public document (role_mask=0): presigned URL returned for any user."""
        app.dependency_overrides[get_identity] = lambda: _make_identity(
            user_id="any_user", user_role_mask=0, user_dept_mask=0
        )
        mock_pymilvus, _ = _setup_mock_pymilvus(
            _milvus_doc(doc_id="pub_doc", role_mask=0, dept_mask=0, status="active")
        )
        mock_get_minio.return_value = _mock_minio(
            available=True,
            url="https://minio.example.com/signed/pub_doc",
        )

        client = TestClient(app)
        with patch.dict(sys.modules, {"pymilvus": mock_pymilvus}):
            resp = client.get("/api/media/pub_doc")

        assert resp.status_code == 200
        data = resp.json()
        assert data["doc_id"] == "pub_doc"
        assert data["url"] == "https://minio.example.com/signed/pub_doc"
        assert data["expires_in_seconds"] > 0
        app.dependency_overrides.clear()

    @patch("common.minio_client.get_minio_client")
    def test_rd_user_can_access_rd_doc(self, mock_get_minio):
        """RD user (role_mask=1) can access RD-restricted document."""
        app.dependency_overrides[get_identity] = lambda: _make_identity(
            user_id="rd_user", user_role_mask=1, user_dept_mask=1
        )
        mock_pymilvus, _ = _setup_mock_pymilvus(
            _milvus_doc(doc_id="rd_doc", role_mask=1, dept_mask=1, status="active")
        )
        mock_get_minio.return_value = _mock_minio(
            available=True,
            url="https://minio.example.com/signed/rd_doc",
        )

        client = TestClient(app)
        with patch.dict(sys.modules, {"pymilvus": mock_pymilvus}):
            resp = client.get("/api/media/rd_doc")

        assert resp.status_code == 200
        data = resp.json()
        assert data["doc_id"] == "rd_doc"
        assert "minio.example.com" in data["url"]
        app.dependency_overrides.clear()

    @patch("common.minio_client.get_minio_client")
    def test_admin_bypasses_all_restrictions(self, mock_get_minio):
        """Admin (super_admin_mask) can access any document regardless of doc masks."""
        app.dependency_overrides[get_identity] = lambda: _make_identity(
            user_id="admin", user_role_mask=4294967295, user_dept_mask=0
        )
        mock_pymilvus, _ = _setup_mock_pymilvus(
            _milvus_doc(doc_id="secret_doc", role_mask=8, dept_mask=8, status="active")
        )
        mock_get_minio.return_value = _mock_minio(
            available=True,
            url="https://minio.example.com/signed/secret_doc",
        )

        client = TestClient(app)
        with patch.dict(sys.modules, {"pymilvus": mock_pymilvus}):
            resp = client.get("/api/media/secret_doc")

        assert resp.status_code == 200
        data = resp.json()
        assert data["doc_id"] == "secret_doc"
        app.dependency_overrides.clear()

    @patch("common.minio_client.get_minio_client")
    def test_dept_restricted_doc_accessible_by_matching_dept(self, mock_get_minio):
        """Document with only dept restriction: user with matching dept can access."""
        app.dependency_overrides[get_identity] = lambda: _make_identity(
            user_id="reg_user", user_role_mask=0, user_dept_mask=4
        )
        mock_pymilvus, _ = _setup_mock_pymilvus(
            _milvus_doc(doc_id="dept_doc", role_mask=0, dept_mask=4, status="active")
        )
        mock_get_minio.return_value = _mock_minio(
            available=True,
            url="https://minio.example.com/signed/dept_doc",
        )

        client = TestClient(app)
        with patch.dict(sys.modules, {"pymilvus": mock_pymilvus}):
            resp = client.get("/api/media/dept_doc")

        assert resp.status_code == 200
        data = resp.json()
        assert data["doc_id"] == "dept_doc"
        app.dependency_overrides.clear()


# ── Permission denied ───────────────────────────────────────────────────────


class TestMediaEndpointDenied:
    """GET /api/media/{doc_id} returns 403 when user lacks permissions."""

    def test_denied_returns_403(self):
        """User without matching role/dept gets 403."""
        app.dependency_overrides[get_identity] = lambda: _make_identity(
            user_id="rd_user", user_role_mask=1, user_dept_mask=1
        )
        mock_pymilvus, _ = _setup_mock_pymilvus(
            _milvus_doc(doc_id="sales_doc", role_mask=8, dept_mask=8, status="active")
        )

        client = TestClient(app)
        with patch.dict(sys.modules, {"pymilvus": mock_pymilvus}):
            resp = client.get("/api/media/sales_doc")

        assert resp.status_code == 403
        data = resp.json()
        assert data["detail"]["error"] == "permission_denied"
        app.dependency_overrides.clear()

    def test_dept_mismatch_returns_403(self):
        """User with correct role but wrong dept gets 403."""
        app.dependency_overrides[get_identity] = lambda: _make_identity(
            user_id="reg_user", user_role_mask=4, user_dept_mask=4
        )
        mock_pymilvus, _ = _setup_mock_pymilvus(
            _milvus_doc(doc_id="quality_doc", role_mask=2, dept_mask=2, status="active")
        )

        client = TestClient(app)
        with patch.dict(sys.modules, {"pymilvus": mock_pymilvus}):
            resp = client.get("/api/media/quality_doc")

        assert resp.status_code == 403
        data = resp.json()
        assert data["detail"]["error"] == "permission_denied"
        app.dependency_overrides.clear()


# ── Not found ───────────────────────────────────────────────────────────────


class TestMediaEndpointNotFound:
    """GET /api/media/{doc_id} returns 404 for missing or archived documents."""

    def test_not_found_returns_404(self):
        """Missing doc_id returns 404 with 'not_found' error."""
        app.dependency_overrides[get_identity] = lambda: _make_identity()
        mock_pymilvus, _ = _setup_mock_pymilvus(_empty_milvus_result())

        client = TestClient(app)
        with patch.dict(sys.modules, {"pymilvus": mock_pymilvus}):
            resp = client.get("/api/media/nonexistent_doc")

        assert resp.status_code == 404
        data = resp.json()
        assert data["detail"]["error"] == "not_found"
        app.dependency_overrides.clear()

    def test_archived_returns_404(self):
        """Archived document returns 404 with 'archived' error."""
        app.dependency_overrides[get_identity] = lambda: _make_identity()
        mock_pymilvus, _ = _setup_mock_pymilvus(
            _milvus_doc(doc_id="old_doc", role_mask=0, dept_mask=0, status="archived")
        )

        client = TestClient(app)
        with patch.dict(sys.modules, {"pymilvus": mock_pymilvus}):
            resp = client.get("/api/media/old_doc")

        assert resp.status_code == 404
        data = resp.json()
        assert data["detail"]["error"] == "archived"
        app.dependency_overrides.clear()

    def test_milvus_query_exception_treated_as_not_found(self):
        """Milvus connection failure is treated as document not found (404)."""
        app.dependency_overrides[get_identity] = lambda: _make_identity()
        mock_collection = MagicMock()
        mock_collection.query.side_effect = RuntimeError("Milvus connection refused")

        mock_pymilvus = MagicMock()
        mock_pymilvus.Collection.return_value = mock_collection

        client = TestClient(app)
        with patch.dict(sys.modules, {"pymilvus": mock_pymilvus}):
            resp = client.get("/api/media/any_doc")

        assert resp.status_code == 404
        data = resp.json()
        assert data["detail"]["error"] == "not_found"
        app.dependency_overrides.clear()


# ── MinIO unavailable ───────────────────────────────────────────────────────


class TestMediaEndpointMinioUnavailable:
    """GET /api/media/{doc_id} returns 503 when MinIO is unavailable."""

    @patch("common.minio_client.get_minio_client")
    def test_minio_unavailable_returns_503(self, mock_get_minio):
        """MinIO client reports unavailable -> 503."""
        app.dependency_overrides[get_identity] = lambda: _make_identity()
        mock_pymilvus, _ = _setup_mock_pymilvus(
            _milvus_doc(doc_id="doc_001", role_mask=0, dept_mask=0, status="active")
        )
        mock_get_minio.return_value = _mock_minio(available=False)

        client = TestClient(app)
        with patch.dict(sys.modules, {"pymilvus": mock_pymilvus}):
            resp = client.get("/api/media/doc_001")

        assert resp.status_code == 503
        data = resp.json()
        assert data["detail"]["error"] == "storage_unavailable"
        app.dependency_overrides.clear()

    @patch("common.minio_client.get_minio_client")
    def test_minio_url_generation_failure_returns_503(self, mock_get_minio):
        """MinIO available but presigned URL generation returns empty string -> 503."""
        app.dependency_overrides[get_identity] = lambda: _make_identity()
        mock_pymilvus, _ = _setup_mock_pymilvus(
            _milvus_doc(doc_id="doc_002", role_mask=0, dept_mask=0, status="active")
        )
        mock_get_minio.return_value = _mock_minio(available=True, url="")

        client = TestClient(app)
        with patch.dict(sys.modules, {"pymilvus": mock_pymilvus}):
            resp = client.get("/api/media/doc_002")

        assert resp.status_code == 503
        data = resp.json()
        assert data["detail"]["error"] == "url_generation_failed"
        app.dependency_overrides.clear()


# ── URL structure validation ────────────────────────────────────────────────


class TestMediaEndpointUrlStructure:
    """Validate response JSON structure of successful media requests."""

    @patch("common.minio_client.get_minio_client")
    def test_response_contains_all_required_fields(self, mock_get_minio):
        """Successful response must contain doc_id, url, and expires_in_seconds."""
        app.dependency_overrides[get_identity] = lambda: _make_identity()
        mock_pymilvus, _ = _setup_mock_pymilvus(
            _milvus_doc(doc_id="structured_doc", role_mask=0, dept_mask=0, status="active")
        )
        mock_get_minio.return_value = _mock_minio(
            available=True,
            url="https://minio.example.com/signed/structured_doc",
        )

        client = TestClient(app)
        with patch.dict(sys.modules, {"pymilvus": mock_pymilvus}):
            resp = client.get("/api/media/structured_doc")

        assert resp.status_code == 200
        data = resp.json()
        assert "doc_id" in data
        assert "url" in data
        assert "expires_in_seconds" in data
        assert isinstance(data["doc_id"], str)
        assert isinstance(data["url"], str)
        assert isinstance(data["expires_in_seconds"], int)
        app.dependency_overrides.clear()

    @patch("common.minio_client.get_minio_client")
    def test_expires_in_seconds_matches_config(self, mock_get_minio):
        """expires_in_seconds should match MINIO_URL_TTL from config (60s default)."""
        app.dependency_overrides[get_identity] = lambda: _make_identity()
        mock_pymilvus, _ = _setup_mock_pymilvus(
            _milvus_doc(doc_id="ttl_doc", role_mask=0, dept_mask=0, status="active")
        )
        mock_get_minio.return_value = _mock_minio(
            available=True,
            url="https://minio.example.com/signed/ttl_doc",
        )

        client = TestClient(app)
        with patch.dict(sys.modules, {"pymilvus": mock_pymilvus}):
            resp = client.get("/api/media/ttl_doc")

        assert resp.status_code == 200
        data = resp.json()
        assert data["expires_in_seconds"] == 60
        app.dependency_overrides.clear()
