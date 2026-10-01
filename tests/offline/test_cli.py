"""Offline CLI subcommand tests."""

from __future__ import annotations

import sys

import pytest

import offline.scheduler as scheduler_module
import offline.snapshot_builder as snapshot_module
from run_offline import main


def test_no_command_returns_2(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_offline.py"])
    assert main() == 2


def test_unimplemented_historical_mode_exits(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_offline.py", "--mode", "full"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_seal_epoch_validates_by_default(monkeypatch):
    calls = {}

    class FakeBuilder:
        def seal_epoch(self, epoch, *, validate=True):
            calls["seal_epoch"] = (epoch, validate)

    monkeypatch.setattr(snapshot_module, "configured_snapshot_builder", lambda: FakeBuilder())
    assert main(["seal-epoch", "--epoch", "phase_1"]) == 0
    assert calls == {"seal_epoch": ("phase_1", True)}


def test_seal_epoch_skip_validation(monkeypatch):
    calls = {}

    class FakeBuilder:
        def seal(self, epoch):
            calls["seal"] = epoch

    monkeypatch.setattr(snapshot_module, "configured_snapshot_builder", lambda: FakeBuilder())
    assert main(["seal-epoch", "--epoch", "phase_1", "--skip-validation"]) == 0
    assert calls == {"seal": "phase_1"}


class _FakeCollectionClient:
    def __init__(self, existing):
        self.existing = set(existing)
        self.deleted = []
        self.ensured = []

    def collection_exists(self, name):
        return name in self.existing

    def delete_collection(self, name):
        self.deleted.append(name)
        self.existing.discard(name)


class _FakeQdrantWriter:
    def __init__(self, name, client):
        self.collection_name = name
        self.client = client
        self.dimension = 8

    def ensure_collection(self):
        self.client.ensured.append(self.collection_name)


class _FakeESWriter:
    def __init__(self):
        self.calls = []

    def ensure_index(self, *, recreate=False, confirm=False):
        self.calls.append((recreate, confirm))


class _FakeIndexBuilder:
    def __init__(self, existing=()):
        self.text_writer = _FakeQdrantWriter("rag_text_768", _FakeCollectionClient(existing))
        self.image_writer = _FakeQdrantWriter("rag_image_512", _FakeCollectionClient(existing))
        self.es_writer = _FakeESWriter()


def test_create_index_ensures_without_deleting(monkeypatch):
    builder = _FakeIndexBuilder(existing=("rag_text_768", "rag_image_512"))
    monkeypatch.setattr(snapshot_module, "configured_snapshot_builder", lambda: builder)
    assert main(["create-index"]) == 0
    assert builder.text_writer.client.deleted == []
    assert builder.text_writer.client.ensured == ["rag_text_768"]
    assert builder.es_writer.calls == [(False, False)]


def test_create_index_recreate_requires_yes(monkeypatch):
    builder = _FakeIndexBuilder(existing=("rag_text_768",))
    monkeypatch.setattr(snapshot_module, "configured_snapshot_builder", lambda: builder)
    assert main(["create-index", "--recreate"]) == 2
    assert builder.text_writer.client.deleted == []


def test_create_index_recreate_deletes_qdrant_and_es(monkeypatch):
    builder = _FakeIndexBuilder(existing=("rag_text_768", "rag_image_512"))
    monkeypatch.setattr(snapshot_module, "configured_snapshot_builder", lambda: builder)
    assert main(["create-index", "--recreate", "--yes"]) == 0
    assert set(builder.text_writer.client.deleted) == {"rag_text_768"}
    assert set(builder.image_writer.client.deleted) == {"rag_image_512"}
    assert builder.es_writer.calls == [(True, True)]


def test_ingest_command_uses_snapshot_builder(monkeypatch, tmp_path):
    calls = {}

    class FakeBuilder:
        def ingest_source(self, source, epoch):
            calls["source"] = source
            calls["epoch"] = epoch
            return (3, 1)

    monkeypatch.setattr(snapshot_module, "configured_snapshot_builder", lambda: FakeBuilder())
    source = tmp_path / "guide.txt"
    source.write_text("content", encoding="utf-8")
    assert (
        main(
            [
                "ingest",
                str(source),
                "--role-mask",
                "2",
                "--dept-mask",
                "4",
                "--epoch",
                "epoch_1",
                "--source-id",
                "guide.txt",
            ]
        )
        == 0
    )
    assert calls["epoch"] == "epoch_1"
    assert calls["source"].role_mask == 2 and calls["source"].dept_mask == 4
    assert calls["source"].document_type == "text"
    assert calls["source"].source_id == "guide.txt"


def test_incremental_build_command_delegates(monkeypatch):
    calls = {}

    class FakeScheduler:
        def run_incremental_cycle(self, **kwargs):
            calls.update(kwargs)
            return {"epoch": "epoch_2"}

    monkeypatch.setattr(scheduler_module, "OfflineScheduler", FakeScheduler)
    assert main(["incremental-build", "--from-epoch", "epoch_1", "--to-epoch", "epoch_2", "--seal"]) == 0
    assert calls == {"from_epoch": "epoch_1", "to_epoch": "epoch_2", "seal": True}


def test_full_rebuild_command_delegates(monkeypatch):
    calls = {}

    class FakeScheduler:
        def run_full_rebuild_cycle(self, **kwargs):
            calls.update(kwargs)
            return {"epoch": "epoch_3"}

    monkeypatch.setattr(scheduler_module, "OfflineScheduler", FakeScheduler)
    assert main(["full-rebuild", "--epoch", "epoch_3"]) == 0
    assert calls == {"epoch": "epoch_3", "seal": False}
