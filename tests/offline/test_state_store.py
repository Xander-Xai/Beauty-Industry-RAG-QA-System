"""Incremental source state detection tests."""

from __future__ import annotations

from offline.state_store import MODIFIED, NEW, UNCHANGED, SourceState, StateStore


def _state(source_id="a", content_hash="h1", size=10, mtime_ns=1, status="active"):
    return SourceState(
        source_id=source_id,
        relative_path=f"{source_id}.txt",
        file_size=size,
        mtime_ns=mtime_ns,
        content_hash=content_hash,
        document_type="txt",
        last_successful_epoch="epoch_1",
        last_processed_at="2026-01-01T00:00:00",
        status=status,
    )


def test_new_unchanged_modified_deleted_detection(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    store.upsert(_state("keep", "hash-keep"))
    store.upsert(_state("gone", "hash-gone"))

    changes = store.diff(
        {
            "keep": {"content_hash": "hash-keep"},
            "changed": {"content_hash": "hash-new"},
        }
    )
    assert changes.new == ["changed"]
    assert changes.modified == []
    assert changes.unchanged == ["keep"]
    assert changes.deleted == ["gone"]


def test_content_change_with_same_mtime_is_modified(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    store.upsert(_state("doc", "old-hash", size=5, mtime_ns=999))
    # Same size and mtime (mtime preserved), different content hash -> MODIFIED.
    assert store.classify("doc", content_hash="new-hash") == MODIFIED


def test_mtime_change_with_same_content_is_unchanged(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    store.upsert(_state("doc", "same-hash", size=5, mtime_ns=111))
    # mtime differs but the authoritative content hash matches -> UNCHANGED.
    assert store.classify("doc", content_hash="same-hash") == UNCHANGED


def test_archived_source_resurfaces_as_new(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    store.upsert(_state("doc", "hash", status="archived"))
    assert store.classify("doc", content_hash="hash") == NEW


def test_mark_deleted_archives_and_persists(tmp_path):
    path = tmp_path / "state.sqlite3"
    with StateStore(path) as store:
        store.upsert(_state("doc", "hash"))
        store.mark_deleted("doc")
    reopened = StateStore(path)
    assert reopened.get("doc").status == "archived"
    assert "doc" not in reopened.active_source_ids()
    reopened.close()


def test_state_isolation_by_source_id(tmp_path):
    store = StateStore(tmp_path / "state.sqlite3")
    store.upsert(_state("a", "ha"))
    store.upsert(_state("b", "hb"))
    assert store.classify("a", content_hash="ha") == UNCHANGED
    assert store.classify("b", content_hash="hb") == UNCHANGED
    assert store.classify("c", content_hash="hc") == NEW
