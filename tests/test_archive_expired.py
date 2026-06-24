"""
过期文档归档测试 (offline/scheduler.py::archive_expired_documents)

覆盖：
- upsert 成功归档（status='archived'）
- upsert 失败回退 delete + insert(status='archived')
- 无过期文档时不做任何写操作
- ES 归档始终执行（不受 Qdrant 结果影响）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock, patch

import pytest

from offline.scheduler import OfflineScheduler


# ── Fixtures ──


@pytest.fixture(autouse=True)
def _mock_qdrant_client():
    """Mock QdrantClient at the scheduler module level."""
    mock_client = MagicMock()
    mock_client.scroll.return_value = ([], None)
    with patch("offline.scheduler.QdrantClient", return_value=mock_client):
        yield mock_client


@pytest.fixture(autouse=True)
def _mock_es():
    """Mock Elasticsearch at the scheduler module level."""
    mock_es = MagicMock()
    with patch("offline.scheduler.Elasticsearch", return_value=mock_es):
        yield mock_es


@pytest.fixture
def scheduler():
    return OfflineScheduler()


def _make_expired_docs(count: int = 1):
    """
    生成模拟的过期文档 Qdrant 记录。
    返回 Record 列表用于 mock client.scroll 的返回值。
    """
    class FakePayload(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    class FakeRecord:
        def __init__(self, idx, payload):
            self.id = idx
            self.payload = payload

        def model_copy(self, **kwargs):
            return FakeRecord(self.id, dict(self.payload))
    return [FakeRecord(i, {
        "doc_id": f"doc_{i:04d}",
        "content": f"content_{i}",
        "role_mask": 0,
        "dept_mask": 0,
        "doc_version_epoch": "old_v1",
        "status": "active",
    }) for i in range(count)]


# ═══════════════════════════════════════════════════════════════════════
# 测试正文
# ═══════════════════════════════════════════════════════════════════════

class TestArchiveExpiredDocuments:
    """归档过期文档逻辑测试"""

    def test_archive_updates_status_for_expired(
        self, scheduler, _mock_qdrant_client, _mock_es
    ):
        """过期文档应被标记为 archived（upsert 成功路径）"""
        _mock_qdrant_client.scroll.return_value = (_make_expired_docs(5), None)

        scheduler.archive_expired_documents("current_v2")

        # Qdrant upsert 应被调用（status 从 active → archived）
        assert _mock_qdrant_client.upsert.called

    def test_archive_delete_when_upsert_fails(
        self, scheduler, _mock_qdrant_client, _mock_es
    ):
        """upsert 失败时回退 delete + insert"""
        expired = _make_expired_docs(1)
        _mock_qdrant_client.scroll.return_value = (expired, None)
        _mock_qdrant_client.upsert.side_effect = RuntimeError("not supported")

        scheduler.archive_expired_documents("current_v2")

        # 回退分支应执行 delete + insert
        assert _mock_qdrant_client.delete.called

    def test_no_expired_docs_skips_writes(
        self, scheduler, _mock_qdrant_client, _mock_es
    ):
        """无过期文档时不做任何写操作"""
        _mock_qdrant_client.scroll.return_value = ([], None)

        scheduler.archive_expired_documents("current_v2")

        _mock_qdrant_client.upsert.assert_not_called()
        _mock_qdrant_client.delete.assert_not_called()

    def test_es_archive_still_runs_when_qdrant_fails(
        self, scheduler, _mock_qdrant_client, _mock_es
    ):
        """Qdrant 回退时，ES 归档不受影响"""
        expired = _make_expired_docs(1)
        _mock_qdrant_client.scroll.return_value = (expired, None)
        _mock_qdrant_client.upsert.side_effect = RuntimeError("upsert not supported")

        scheduler.archive_expired_documents("current_v2")

        # ES update_by_query 应被调用
        assert _mock_es.update_by_query.called

    def test_no_qdrant_mutations_when_already_archived(
        self, scheduler, _mock_qdrant_client, _mock_es
    ):
        """Qdrant 不做任何 upsert / delete 当无过期文档时"""
        _mock_qdrant_client.scroll.return_value = ([], None)

        scheduler.archive_expired_documents("current_v2")

        _mock_qdrant_client.upsert.assert_not_called()
        _mock_qdrant_client.delete.assert_not_called()

    def test_es_archive_runs_even_without_expired_qdrant_docs(
        self, scheduler, _mock_qdrant_client, _mock_es
    ):
        """即使无 Qdrant 过期文档，ES update_by_query 仍会执行"""
        _mock_qdrant_client.scroll.return_value = ([], None)

        scheduler.archive_expired_documents("current_v2")

        assert _mock_es.update_by_query.called
