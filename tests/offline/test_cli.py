"""Offline CLI subcommand tests."""

from __future__ import annotations

import json
import sys

import pytest

import offline.scheduler as scheduler_module
import offline.snapshot_builder as snapshot_module
from run_offline import _build_parser, main


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
        def run_full_rebuild_cycle(self, epoch=None, seal=False):
            calls.update(epoch=epoch, seal=seal)
            return {"epoch": "epoch_3"}

    monkeypatch.setattr(scheduler_module, "OfflineScheduler", FakeScheduler)
    assert main(["full-rebuild", "--epoch", "epoch_3"]) == 0
    assert calls == {"epoch": "epoch_3", "seal": False}


# ── export-regression-candidates ────────────────────────────────────────────


def _reviewed_negative_feedback(store_path, query="视黄醇可以和维生素 A 一起用吗"):
    from offline.feedback_loop import (
        ACCEPTED,
        FeedbackRecord,
        FeedbackStore,
        make_feedback_id,
    )

    store = FeedbackStore(store_path)
    record = FeedbackRecord(
        feedback_id=make_feedback_id(query, "req-1"),
        query=query,
        answer="不可以，两者会互相抵消。",
        evidence_doc_ids=["doc-wrong-1"],
        request_id="req-1",
        rating=-1.0,
        business_type="ingredient",
    )
    store.add(record)
    store.set_review_status(record.feedback_id, ACCEPTED)
    store.close()
    return record


def test_export_regression_candidates_writes_queue_and_dataset(tmp_path):
    store_path = tmp_path / "feedback.sqlite3"
    output_dir = tmp_path / "out"
    _reviewed_negative_feedback(store_path)

    assert (
        main(
            [
                "export-regression-candidates",
                "--store-path",
                str(store_path),
                "--output-dir",
                str(output_dir),
            ]
        )
        == 0
    )

    queue = [
        json.loads(line)
        for line in (output_dir / "regression_candidates.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["review_status"] for row in queue] == ["PENDING_REVIEW"]
    assert (output_dir / "regression_dataset.jsonl").read_text(encoding="utf-8") == ""


def test_export_regression_candidates_fails_closed_on_missing_expectation(tmp_path):
    """An approval that skipped the human expectation must stop the export."""
    import sqlite3

    store_path = tmp_path / "feedback.sqlite3"
    output_dir = tmp_path / "out"
    _reviewed_negative_feedback(store_path)
    argv = ["export-regression-candidates", "--store-path", str(store_path), "--output-dir", str(output_dir)]
    assert main(argv) == 0

    with sqlite3.connect(store_path) as connection:
        connection.execute("UPDATE regression_candidates SET review_status = 'accepted'")

    assert main(argv) == 2


def test_export_regression_candidates_status_choices_match_the_module():
    """The parser spells the statuses out to keep `--help` import-free; keep them honest."""
    from offline.regression_candidates import ACCEPTED, CANDIDATE_REVIEW_STATUSES, PENDING_REVIEW, REJECTED

    parser = _build_parser()
    command_action = next(item for item in parser._actions if item.dest == "command")
    export_parser = command_action.choices["export-regression-candidates"]
    status_action = next(item for item in export_parser._actions if item.dest == "status")

    assert set(status_action.choices) == {PENDING_REVIEW, ACCEPTED, REJECTED, "all"}
    assert CANDIDATE_REVIEW_STATUSES == {PENDING_REVIEW, ACCEPTED, REJECTED}


# ── review-source (explicit ingestion trust decision) ────────────────────────


def _trust_config(tmp_path):
    return {
        "knowledge_base": {"data_dir": str(tmp_path / "data")},
        "permission_rules": {"rules": [], "default_role_mask": 0, "default_dept_mask": 0},
        "source_trust": {
            "rules": [{"path_pattern": "**/imports/**", "source_trust": "UNTRUSTED"}],
            "default_source_trust": "MANAGED_INTERNAL",
            "approval_store_path": str(tmp_path / "trust.sqlite3"),
        },
        "offline": {"scheduler": {}},
    }


def _imported_source(tmp_path, name="imports/vendor.txt", text="third-party dossier"):
    data_dir = tmp_path / "data"
    path = data_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _patch_config(monkeypatch, tmp_path):
    import common.config as config_module

    config = _trust_config(tmp_path)
    monkeypatch.setattr(config_module, "get_config_dict", lambda *args, **kwargs: config)
    return config


def test_review_source_records_an_attributed_approval(monkeypatch, tmp_path):
    from offline.source_trust import APPROVAL_APPROVED, TrustRegistry

    config = _patch_config(monkeypatch, tmp_path)
    source = _imported_source(tmp_path)

    assert (
        main(
            [
                "review-source",
                str(source),
                "--approve",
                "--actor",
                "steward@corp",
                "--note",
                "vendor dossier checked",
            ]
        )
        == 0
    )

    registry = TrustRegistry(config["source_trust"]["approval_store_path"])
    decision = registry.get("imports/vendor.txt")
    assert decision.approval_status == APPROVAL_APPROVED
    assert decision.decided_by == "steward@corp"
    assert decision.note == "vendor dossier checked"


def test_review_source_refuses_an_unattributed_approval(monkeypatch, tmp_path, caplog):
    from offline.source_trust import TrustRegistry

    config = _patch_config(monkeypatch, tmp_path)
    source = _imported_source(tmp_path)

    assert main(["review-source", str(source), "--approve"]) == 2

    registry = TrustRegistry(config["source_trust"]["approval_store_path"])
    assert registry.get("imports/vendor.txt") is None


def test_review_source_refuses_an_unknown_source(monkeypatch, tmp_path):
    _patch_config(monkeypatch, tmp_path)
    missing = tmp_path / "data" / "imports" / "absent.txt"
    missing.parent.mkdir(parents=True, exist_ok=True)
    assert main(["review-source", str(missing), "--approve", "--actor", "steward"]) == 2


def test_review_source_requires_a_decision_flag(monkeypatch, tmp_path):
    _patch_config(monkeypatch, tmp_path)
    source = _imported_source(tmp_path)
    assert main(["review-source", str(source), "--actor", "steward"]) == 2


def test_review_source_list_reports_the_quarantine_queue(monkeypatch, tmp_path, caplog):
    _patch_config(monkeypatch, tmp_path)
    _imported_source(tmp_path)
    _imported_source(tmp_path, name="public/guide.txt", text="managed")

    with caplog.at_level("WARNING"):
        assert main(["review-source", "--list"]) == 0

    assert "imports/vendor.txt" in caplog.text
    assert "no approval decision yet" in caplog.text
    assert "public/guide.txt" not in caplog.text


def test_review_source_rejects_a_source(monkeypatch, tmp_path):
    from offline.source_trust import APPROVAL_REJECTED, TrustRegistry

    config = _patch_config(monkeypatch, tmp_path)
    source = _imported_source(tmp_path)

    assert main(["review-source", str(source), "--reject", "--actor", "steward"]) == 0

    registry = TrustRegistry(config["source_trust"]["approval_store_path"])
    assert registry.get("imports/vendor.txt").approval_status == APPROVAL_REJECTED


def test_ingest_trust_choices_match_the_module():
    """The parser spells the levels out to keep `--help` import-free; keep them honest."""
    from offline.source_trust import SOURCE_TRUST_LEVELS

    parser = _build_parser()
    command_action = next(item for item in parser._actions if item.dest == "command")
    ingest_parser = command_action.choices["ingest"]
    trust_action = next(item for item in ingest_parser._actions if item.dest == "source_trust")
    assert set(trust_action.choices) == SOURCE_TRUST_LEVELS


# ── ingest-text must not bypass the quarantine contract ──────────────────────


def _capture_ingest_text_provenance(monkeypatch, config):
    """Patch the TXT service and return a dict the provenance lands in."""
    from offline import text_ingestion as text_ingestion_module

    captured = {}

    class FakeService:
        def ingest(self, source, **kwargs):
            captured.update(kwargs)
            return []

    monkeypatch.setattr(text_ingestion_module, "configured_text_ingestion_service", lambda: FakeService())
    return captured


def test_ingest_text_honors_an_explicit_untrusted_flag(monkeypatch, tmp_path):
    """`ingest-text --source-trust UNTRUSTED` must persist UNTRUSTED, not MANAGED_INTERNAL."""
    _patch_config(monkeypatch, tmp_path)
    captured = _capture_ingest_text_provenance(monkeypatch, tmp_path)
    source = _imported_source(tmp_path)

    assert (
        main(
            [
                "ingest-text",
                str(source),
                "--role-mask",
                "0",
                "--dept-mask",
                "0",
                "--epoch",
                "e1",
                "--source-trust",
                "UNTRUSTED",
            ]
        )
        == 0
    )

    provenance = captured["provenance"]
    assert provenance["source_trust"] == "UNTRUSTED"
    assert provenance["trust_class"] == "UNTRUSTED"
    assert provenance["approval_status"] == "PENDING_REVIEW"


def test_ingest_text_resolves_trust_from_path_rules(monkeypatch, tmp_path):
    """A TXT import matched by an UNTRUSTED rule is quarantined without restating it."""
    _patch_config(monkeypatch, tmp_path)
    captured = _capture_ingest_text_provenance(monkeypatch, tmp_path)
    source = _imported_source(tmp_path)

    assert main(["ingest-text", str(source), "--role-mask", "0", "--dept-mask", "0", "--epoch", "e1"]) == 0

    assert captured["provenance"]["trust_class"] == "UNTRUSTED"


def test_ingest_text_honors_a_recorded_approval(monkeypatch, tmp_path):
    """A ledger approval for these exact bytes makes ingest-text activatable again."""
    from offline.source_trust import APPROVAL_APPROVED, TrustRegistry, file_content_hash

    config = _patch_config(monkeypatch, tmp_path)
    captured = _capture_ingest_text_provenance(monkeypatch, tmp_path)
    source = _imported_source(tmp_path)

    registry = TrustRegistry(config["source_trust"]["approval_store_path"])
    registry.decide(
        "imports/vendor.txt",
        content_hash=file_content_hash(source),
        approval_status=APPROVAL_APPROVED,
        actor="steward",
    )
    registry.close()

    assert (
        main(
            [
                "ingest-text",
                str(source),
                "--source-id",
                "imports/vendor.txt",
                "--role-mask",
                "0",
                "--dept-mask",
                "0",
                "--epoch",
                "e1",
            ]
        )
        == 0
    )

    assert captured["provenance"]["trust_class"] == "APPROVED_EXTERNAL"
    assert captured["provenance"]["approval_actor"] == "steward"


def test_review_source_refuses_a_managed_source(monkeypatch, tmp_path):
    """A managed source needs no decision, so a stored one would be silently ignored."""
    from offline.source_trust import TrustRegistry

    config = _patch_config(monkeypatch, tmp_path)
    source = _imported_source(tmp_path, name="public/guide.txt", text="managed")

    assert main(["review-source", str(source), "--approve", "--actor", "steward"]) == 2

    registry = TrustRegistry(config["source_trust"]["approval_store_path"])
    assert registry.get("public/guide.txt") is None


def test_review_source_accepts_an_out_of_tree_import_with_source_id(monkeypatch, tmp_path):
    """An out-of-tree source staged with `--source-id` must be reviewable, not stuck in quarantine."""
    from offline.source_trust import APPROVAL_APPROVED, TrustRegistry

    config = _patch_config(monkeypatch, tmp_path)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside" / "vendor.txt"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("out-of-tree dossier", encoding="utf-8")

    assert (
        main(
            [
                "review-source",
                str(outside),
                "--source-id",
                "imports/vendor.txt",
                "--approve",
                "--actor",
                "steward",
            ]
        )
        == 0
    )

    registry = TrustRegistry(config["source_trust"]["approval_store_path"])
    assert registry.get("imports/vendor.txt").approval_status == APPROVAL_APPROVED
