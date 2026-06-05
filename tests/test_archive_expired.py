"""
过期文档归档测试 (offline/scheduler.py::archive_expired_documents)

覆盖：
- upsert 成功归档（status='archived'）
- upsert 失败回退 delete + insert(status='archived')
- 无过期文档时不做任何写操作
- ES 归档始终执行（不受 Milvus 结果影响）
"""

import sys
import os
import types
import pytest

# 在导入 offline.scheduler 之前，将 pymilvus / elasticsearch 注入 sys.modules，
# 因为 scheduler 在函数内部 local import 这两个模块。
_pymilvus_mock = types.ModuleType("pymilvus")
_pymilvus_mock.Collection = None
_pymilvus_mock.connections = None
sys.modules.setdefault("pymilvus", _pymilvus_mock)

_elasticsearch_mock = types.ModuleType("elasticsearch")
_elasticsearch_mock.Elasticsearch = None
sys.modules.setdefault("elasticsearch", _elasticsearch_mock)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock
from offline.scheduler import OfflineScheduler


# ── Fixtures ──

@pytest.fixture(autouse=True)
def _mock_pymilvus(monkeypatch):
    """在每次测试中为 pymilvus 提供 mock 对象"""
    mock_collection = MagicMock()
    mock_connections = MagicMock()
    monkeypatch.setattr("pymilvus.Collection", lambda name: mock_collection)
    monkeypatch.setattr("pymilvus.connections", mock_connections)
    return mock_collection


@pytest.fixture(autouse=True)
def _mock_es(monkeypatch):
    """在每次测试中为 elasticsearch 提供 mock 对象"""
    mock_es = MagicMock()
    monkeypatch.setattr(
        "elasticsearch.Elasticsearch", lambda hosts: mock_es
    )
    return mock_es


@pytest.fixture
def scheduler():
    return OfflineScheduler()


# ── Helper data ──

def _make_expired_docs(n=2):
    """生成模拟的 Milvus 过期文档查询结果"""
    docs = []
    for i in range(n):
        docs.append({
            "id": 1000 + i,
            "doc_id": f"doc_{i}",
            "content": f"content_{i}",
            "embedding": [0.1] * 768,
            "doc_type": "regulation",
            "embedding_type": "text",
            "role_mask": 4,
            "dept_mask": 4,
            "doc_version_epoch": "20240101_00",
            "status": "active",
        })
    return docs


def _make_es_hits(n=2):
    """生成模拟的 ES 查询结果"""
    hits = []
    for i in range(n):
        hits.append({
            "_id": f"es_{i}",
            "_source": {
                "content": f"content_{i}",
                "doc_id": f"doc_{i}",
                "doc_version_epoch": "20240101_00",
                "status": "active",
            },
        })
    return hits


# ── Tests ──

class TestArchiveExpiredUpsertSuccess:
    """upsert 正常工作时，直接通过 upsert 完成归档"""

    def test_upsert_called_with_archived_status(
        self, scheduler, _mock_pymilvus, _mock_es
    ):
        """upsert 被调用，且每条记录 status 已更新为 'archived'"""
        expired = _make_expired_docs(2)
        _mock_pymilvus.query.return_value = expired

        scheduler.archive_expired_documents("20250601_00")

        # Milvus upsert
        _mock_pymilvus.upsert.assert_called_once()
        upsert_arg = _mock_pymilvus.upsert.call_args[0][0]
        assert len(upsert_arg) == 2
        for row in upsert_arg:
            assert row["status"] == "archived"

        # Milvus delete 不应被调用
        _mock_pymilvus.delete.assert_not_called()
        # Milvus insert 不应被调用
        _mock_pymilvus.insert.assert_not_called()

    def test_es_update_by_query_called(
        self, scheduler, _mock_pymilvus, _mock_es
    ):
        """ES 归档 update_by_query 始终被调用"""
        _mock_pymilvus.query.return_value = _make_expired_docs(1)

        scheduler.archive_expired_documents("20250601_00")

        _mock_es.update_by_query.assert_called_once()
        call_kwargs = _mock_es.update_by_query.call_args
        body = call_kwargs[1]["body"] if "body" in call_kwargs[1] else call_kwargs[0][1]
        assert body["script"]["source"] == "ctx._source.status = 'archived'"


class TestArchiveExpiredUpsertFallback:
    """upsert 失败时，回退为 delete + insert(status='archived')"""

    def test_fallback_to_delete_and_insert(
        self, scheduler, _mock_pymilvus, _mock_es
    ):
        """upsert 抛出异常后，调用 delete 并以 insert(status='archived') 写回"""
        expired = _make_expired_docs(2)
        _mock_pymilvus.query.return_value = expired
        _mock_pymilvus.upsert.side_effect = RuntimeError("upsert not supported")

        scheduler.archive_expired_documents("20250601_00")

        # delete 应被调用，过滤条件包含所有过期 id
        _mock_pymilvus.delete.assert_called_once()
        delete_filter = _mock_pymilvus.delete.call_args[0][0]
        assert "1000" in delete_filter
        assert "1001" in delete_filter

        # insert 应被调用，status 为 archived
        _mock_pymilvus.insert.assert_called_once()
        insert_arg = _mock_pymilvus.insert.call_args[0][0]
        assert len(insert_arg) == 2
        for row in insert_arg:
            assert row["status"] == "archived"

    def test_es_still_archives_on_fallback(
        self, scheduler, _mock_pymilvus, _mock_es
    ):
        """Milvus 回退时，ES 归档不受影响"""
        _mock_pymilvus.query.return_value = _make_expired_docs(1)
        _mock_pymilvus.upsert.side_effect = Exception("mock failure")

        scheduler.archive_expired_documents("20250601_00")

        _mock_es.update_by_query.assert_called_once()

    def test_fallback_preserves_original_fields(
        self, scheduler, _mock_pymilvus, _mock_es
    ):
        """回退 insert 的数据保留原始字段值（仅 status 变为 archived）"""
        expired = _make_expired_docs(1)
        _mock_pymilvus.query.return_value = expired
        _mock_pymilvus.upsert.side_effect = RuntimeError("not supported")

        scheduler.archive_expired_documents("20250601_00")

        insert_arg = _mock_pymilvus.insert.call_args[0][0]
        row = insert_arg[0]
        assert row["doc_id"] == "doc_0"
        assert row["content"] == "content_0"
        assert row["role_mask"] == 4
        assert row["status"] == "archived"


class TestArchiveExpiredNoDocs:
    """没有过期文档时，不执行任何写操作"""

    def test_no_milvus_mutations(
        self, scheduler, _mock_pymilvus, _mock_es
    ):
        """Milvus 不做任何 upsert / delete / insert"""
        _mock_pymilvus.query.return_value = []

        scheduler.archive_expired_documents("20250601_00")

        _mock_pymilvus.upsert.assert_not_called()
        _mock_pymilvus.delete.assert_not_called()
        _mock_pymilvus.insert.assert_not_called()

    def test_es_still_runs(
        self, scheduler, _mock_pymilvus, _mock_es
    ):
        """即使无 Milvus 过期文档，ES update_by_query 仍会执行"""
        _mock_pymilvus.query.return_value = []

        scheduler.archive_expired_documents("20250601_00")

        _mock_es.update_by_query.assert_called_once()
