"""Offline CLI subcommand tests."""

from __future__ import annotations

import sys

import pytest

import offline.scheduler as scheduler_module
import offline.snapshot_builder as snapshot_module
import offline.text_ingestion as text_module
from run_offline import main


def test_no_command_returns_2(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_offline.py"])
    assert main() == 2


def test_unimplemented_historical_mode_exits(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_offline.py", "--mode", "full"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_seal_epoch_prefers_validation(monkeypatch):
    calls = {}

    class FakeService:
        class _Writer:
            def seal_epoch(self, epoch):
                calls["writer"] = epoch

        writer = _Writer()

        def validate_and_seal(self, epoch):
            calls["validated"] = epoch

    monkeypatch.setattr(text_module, "configured_text_ingestion_service", lambda: FakeService())
    assert main(["seal-epoch", "--epoch", "phase_1"]) == 0
    assert calls == {"validated": "phase_1"}


def test_seal_epoch_falls_back_to_writer(monkeypatch):
    calls = {}

    class FakeService:
        class _Writer:
            def seal_epoch(self, epoch):
                calls["writer"] = epoch

        writer = _Writer()

    monkeypatch.setattr(text_module, "configured_text_ingestion_service", lambda: FakeService())
    assert main(["seal-epoch", "--epoch", "phase_1"]) == 0
    assert calls == {"writer": "phase_1"}


def test_seal_epoch_skip_validation(monkeypatch):
    calls = {}

    class FakeService:
        class _Writer:
            def seal_epoch(self, epoch):
                calls["writer"] = epoch

        writer = _Writer()

        def validate_and_seal(self, epoch):  # pragma: no cover - must not be called
            calls["validated"] = epoch

    monkeypatch.setattr(text_module, "configured_text_ingestion_service", lambda: FakeService())
    assert main(["seal-epoch", "--epoch", "phase_1", "--skip-validation"]) == 0
    assert calls == {"writer": "phase_1"}


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
