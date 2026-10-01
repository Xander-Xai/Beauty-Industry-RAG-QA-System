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

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from common.auth import require_identity
from common.models import UserIdentity

# ── Helpers ─────────────────────────────────────────────────────────────────


class _FakeQdrantRecord:
    """模拟 Qdrant 查询返回的 Record 对象"""

    def __init__(self, doc_id, role_mask=0, dept_mask=0, status="active", epoch="default"):
        self.payload = {
            "role_mask": role_mask,
            "dept_mask": dept_mask,
            "status": status,
            "doc_version_epoch": epoch,
        }


def _setup_qdrant_scroll(*records):
    """创建 mock QdrantClient，其 scroll 返回指定的 records"""
    mock_client = MagicMock()
    mock_client.scroll.return_value = (list(records), None)
    return mock_client


def _mock_minio(available=True, url="https://minio.example.com/signed/test"):
    """Create a mock MinioClient."""
    mock = MagicMock()
    mock.is_available = available
    mock.get_presigned_url.return_value = url
    return mock


def _make_identity(user_id="test_user", user_role_mask=0, user_dept_mask=0):
    """Create a UserIdentity for dependency override."""
    return UserIdentity(
        user_id=user_id,
        user_role_mask=user_role_mask,
        user_dept_mask=user_dept_mask,
    )


# ── Allowed access ──────────────────────────────────────────────────────────


class TestMediaEndpointAllowed:
    """GET /api/media/{doc_id} returns presigned URL when user has access."""

    @patch("api.routes.QdrantClient")
    @patch("common.minio_client.get_minio_client")
    def test_public_doc_returns_200_with_url(self, mock_get_minio, mock_qdrant):
        """Public document (role_mask=0): presigned URL returned for any user."""
        app.dependency_overrides[require_identity] = lambda: _make_identity(
            user_id="any_user", user_role_mask=0, user_dept_mask=0
        )
        mock_qdrant.return_value = _setup_qdrant_scroll(
            _FakeQdrantRecord(doc_id="pub_doc", role_mask=0, dept_mask=0, status="active")
        )
        mock_get_minio.return_value = _mock_minio(
            available=True,
            url="https://minio.example.com/signed/pub_doc",
        )

        client = TestClient(app)
        resp = client.get("/api/media/pub_doc")

        assert resp.status_code == 200
        data = resp.json()
        assert data["doc_id"] == "pub_doc"
        assert data["url"] == "https://minio.example.com/signed/pub_doc"
        assert data["expires_in_seconds"] > 0
        media_filter = mock_qdrant.return_value.scroll.call_args.kwargs["scroll_filter"]
        filter_values = {(condition.key, condition.match.value) for condition in media_filter.must}
        assert filter_values == {("doc_id", "pub_doc"), ("status", "active")}
        assert media_filter.should[0].key == "doc_version_epoch"
        assert media_filter.should[0].match.value == "default"
        assert media_filter.should[1].is_empty.key == "doc_version_epoch"
        app.dependency_overrides.clear()

    @patch("api.routes.QdrantClient")
    @patch("common.minio_client.get_minio_client")
    def test_rd_user_can_access_rd_doc(self, mock_get_minio, mock_qdrant):
        """RD user (role_mask=1) can access RD-restricted document."""
        app.dependency_overrides[require_identity] = lambda: _make_identity(
            user_id="rd_user", user_role_mask=1, user_dept_mask=1
        )
        mock_qdrant.return_value = _setup_qdrant_scroll(
            _FakeQdrantRecord(doc_id="rd_doc", role_mask=1, dept_mask=1, status="active")
        )
        mock_get_minio.return_value = _mock_minio(
            available=True,
            url="https://minio.example.com/signed/rd_doc",
        )

        client = TestClient(app)
        resp = client.get("/api/media/rd_doc")

        assert resp.status_code == 200
        data = resp.json()
        assert data["doc_id"] == "rd_doc"
        assert "minio.example.com" in data["url"]
        app.dependency_overrides.clear()

    @patch("api.routes.QdrantClient")
    @patch("common.minio_client.get_minio_client")
    def test_admin_bypasses_all_restrictions(self, mock_get_minio, mock_qdrant):
        """Configured admin role can access any document regardless of doc masks."""
        app.dependency_overrides[require_identity] = lambda: _make_identity(
            user_id="admin", user_role_mask=2147483647, user_dept_mask=0
        )
        mock_qdrant.return_value = _setup_qdrant_scroll(
            _FakeQdrantRecord(doc_id="secret_doc", role_mask=8, dept_mask=8, status="active")
        )
        mock_get_minio.return_value = _mock_minio(
            available=True,
            url="https://minio.example.com/signed/secret_doc",
        )

        client = TestClient(app)
        resp = client.get("/api/media/secret_doc")

        assert resp.status_code == 200
        data = resp.json()
        assert data["doc_id"] == "secret_doc"
        app.dependency_overrides.clear()

    @patch("api.routes.QdrantClient")
    @patch("common.minio_client.get_minio_client")
    def test_admin_can_access_dept_only_doc(self, mock_get_minio, mock_qdrant):
        """Configured admin role should bypass dept-only restrictions too."""
        app.dependency_overrides[require_identity] = lambda: _make_identity(
            user_id="admin", user_role_mask=2147483647, user_dept_mask=0
        )
        mock_qdrant.return_value = _setup_qdrant_scroll(
            _FakeQdrantRecord(doc_id="dept_only_doc", role_mask=0, dept_mask=8, status="active")
        )
        mock_get_minio.return_value = _mock_minio(
            available=True,
            url="https://minio.example.com/signed/dept_only_doc",
        )

        client = TestClient(app)
        resp = client.get("/api/media/dept_only_doc")

        assert resp.status_code == 200
        assert resp.json()["doc_id"] == "dept_only_doc"
        app.dependency_overrides.clear()

    @patch("api.routes.QdrantClient")
    @patch("common.minio_client.get_minio_client")
    def test_dept_restricted_doc_accessible_by_matching_dept(self, mock_get_minio, mock_qdrant):
        """Document with only dept restriction: user with matching dept can access."""
        app.dependency_overrides[require_identity] = lambda: _make_identity(
            user_id="reg_user", user_role_mask=0, user_dept_mask=4
        )
        mock_qdrant.return_value = _setup_qdrant_scroll(
            _FakeQdrantRecord(doc_id="dept_doc", role_mask=0, dept_mask=4, status="active")
        )
        mock_get_minio.return_value = _mock_minio(
            available=True,
            url="https://minio.example.com/signed/dept_doc",
        )

        client = TestClient(app)
        resp = client.get("/api/media/dept_doc")

        assert resp.status_code == 200
        data = resp.json()
        assert data["doc_id"] == "dept_doc"
        app.dependency_overrides.clear()


# ── Permission denied ───────────────────────────────────────────────────────


class TestMediaEndpointDenied:
    """GET /api/media/{doc_id} returns 403 when user lacks permissions."""

    @patch("api.routes.QdrantClient")
    def test_denied_returns_403(self, mock_qdrant):
        """User without matching role/dept gets 403."""
        app.dependency_overrides[require_identity] = lambda: _make_identity(
            user_id="rd_user", user_role_mask=1, user_dept_mask=1
        )
        mock_qdrant.return_value = _setup_qdrant_scroll(
            _FakeQdrantRecord(doc_id="sales_doc", role_mask=8, dept_mask=8, status="active")
        )

        client = TestClient(app)
        resp = client.get("/api/media/sales_doc")

        assert resp.status_code == 403
        data = resp.json()
        assert data["detail"]["error"] == "permission_denied"
        app.dependency_overrides.clear()

    @patch("api.routes.QdrantClient")
    def test_dept_mismatch_returns_403(self, mock_qdrant):
        """User with correct role but wrong dept gets 403."""
        app.dependency_overrides[require_identity] = lambda: _make_identity(
            user_id="reg_user", user_role_mask=4, user_dept_mask=4
        )
        mock_qdrant.return_value = _setup_qdrant_scroll(
            _FakeQdrantRecord(doc_id="quality_doc", role_mask=2, dept_mask=2, status="active")
        )

        client = TestClient(app)
        resp = client.get("/api/media/quality_doc")

        assert resp.status_code == 403
        data = resp.json()
        assert data["detail"]["error"] == "permission_denied"
        app.dependency_overrides.clear()


# ── Not found ───────────────────────────────────────────────────────────────


class TestMediaEndpointNotFound:
    """GET /api/media/{doc_id} returns 404 for missing or archived documents."""

    @patch("api.routes.QdrantClient")
    def test_not_found_returns_404(self, mock_qdrant):
        """Missing doc_id returns 404 with 'not_found' error."""
        app.dependency_overrides[require_identity] = lambda: _make_identity()
        mock_qdrant.return_value = _setup_qdrant_scroll()  # empty scroll

        client = TestClient(app)
        resp = client.get("/api/media/nonexistent_doc")

        assert resp.status_code == 404
        data = resp.json()
        assert data["detail"]["error"] == "not_found"
        app.dependency_overrides.clear()

    @patch("api.routes.QdrantClient")
    def test_archived_returns_404(self, mock_qdrant):
        """Archived document returns 404 with 'archived' error."""
        app.dependency_overrides[require_identity] = lambda: _make_identity()
        mock_qdrant.return_value = _setup_qdrant_scroll(
            _FakeQdrantRecord(doc_id="old_doc", role_mask=0, dept_mask=0, status="archived")
        )

        client = TestClient(app)
        resp = client.get("/api/media/old_doc")

        assert resp.status_code == 404
        data = resp.json()
        assert data["detail"]["error"] == "archived"
        app.dependency_overrides.clear()

    @patch("api.routes.QdrantClient")
    def test_qdrant_exception_treated_as_not_found(self, mock_qdrant):
        """Qdrant connection failure is treated as document not found (404)."""
        app.dependency_overrides[require_identity] = lambda: _make_identity()
        mock_qdrant.side_effect = RuntimeError("Qdrant connection refused")

        client = TestClient(app)
        resp = client.get("/api/media/any_doc")

        assert resp.status_code == 404
        data = resp.json()
        assert data["detail"]["error"] == "not_found"
        app.dependency_overrides.clear()


# ── MinIO unavailable ───────────────────────────────────────────────────────


class TestMediaEndpointMinioUnavailable:
    """GET /api/media/{doc_id} returns 503 when MinIO is unavailable."""

    @patch("api.routes.QdrantClient")
    @patch("common.minio_client.get_minio_client")
    def test_minio_unavailable_returns_503(self, mock_get_minio, mock_qdrant):
        """MinIO client reports unavailable -> 503."""
        app.dependency_overrides[require_identity] = lambda: _make_identity()
        mock_qdrant.return_value = _setup_qdrant_scroll(
            _FakeQdrantRecord(doc_id="doc_001", role_mask=0, dept_mask=0, status="active")
        )
        mock_get_minio.return_value = _mock_minio(available=False)

        client = TestClient(app)
        resp = client.get("/api/media/doc_001")

        assert resp.status_code == 503
        data = resp.json()
        assert data["detail"]["error"] == "storage_unavailable"
        app.dependency_overrides.clear()

    @patch("api.routes.QdrantClient")
    @patch("common.minio_client.get_minio_client")
    def test_minio_url_generation_failure_returns_503(self, mock_get_minio, mock_qdrant):
        """MinIO available but presigned URL generation returns empty string -> 503."""
        app.dependency_overrides[require_identity] = lambda: _make_identity()
        mock_qdrant.return_value = _setup_qdrant_scroll(
            _FakeQdrantRecord(doc_id="doc_002", role_mask=0, dept_mask=0, status="active")
        )
        mock_get_minio.return_value = _mock_minio(available=True, url="")

        client = TestClient(app)
        resp = client.get("/api/media/doc_002")

        assert resp.status_code == 503
        data = resp.json()
        assert data["detail"]["error"] == "url_generation_failed"
        app.dependency_overrides.clear()


# ── URL structure validation ────────────────────────────────────────────────


class TestMediaEndpointUrlStructure:
    """Validate response JSON structure of successful media requests."""

    @patch("api.routes.QdrantClient")
    @patch("common.minio_client.get_minio_client")
    def test_response_contains_all_required_fields(self, mock_get_minio, mock_qdrant):
        """Successful response must contain doc_id, url, and expires_in_seconds."""
        app.dependency_overrides[require_identity] = lambda: _make_identity()
        mock_qdrant.return_value = _setup_qdrant_scroll(
            _FakeQdrantRecord(doc_id="structured_doc", role_mask=0, dept_mask=0, status="active")
        )
        mock_get_minio.return_value = _mock_minio(
            available=True,
            url="https://minio.example.com/signed/structured_doc",
        )

        client = TestClient(app)
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

    @patch("api.routes.QdrantClient")
    @patch("common.minio_client.get_minio_client")
    def test_expires_in_seconds_matches_config(self, mock_get_minio, mock_qdrant):
        """expires_in_seconds should match MINIO_URL_TTL from config (60s default)."""
        app.dependency_overrides[require_identity] = lambda: _make_identity()
        mock_qdrant.return_value = _setup_qdrant_scroll(
            _FakeQdrantRecord(doc_id="ttl_doc", role_mask=0, dept_mask=0, status="active")
        )
        mock_get_minio.return_value = _mock_minio(
            available=True,
            url="https://minio.example.com/signed/ttl_doc",
        )

        client = TestClient(app)
        resp = client.get("/api/media/ttl_doc")

        assert resp.status_code == 200
        data = resp.json()
        assert data["expires_in_seconds"] == 60
        app.dependency_overrides.clear()
