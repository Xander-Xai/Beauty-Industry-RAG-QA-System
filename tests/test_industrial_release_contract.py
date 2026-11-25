"""Negative release cases, with synthetic reviewed records only."""

import hashlib
import json
from datetime import date
from types import SimpleNamespace

import pytest

from common.release_contract import load_manifest, validate_industrial_startup
from core.exceptions import InfrastructureError


def test_manifest_rejects_tampered_expired_or_mis_scoped_evidence(tmp_path):
    content = "synthetic reviewed text"
    digest = hashlib.sha256(content.encode()).hexdigest()
    record = {
        "record_id": "chunk-1",
        "doc_id": "doc-1",
        "content_sha256": digest,
        "role_mask": 1,
        "dept_mask": 2,
        "valid_from": "2026-01-01",
        "valid_until": "2026-12-31",
        "source_ref": "synthetic-source",
        "review_ref": "synthetic-review",
    }
    raw = json.dumps(
        {
            "schema": "rag-release/v1",
            "epoch": "reviewed-v1",
            "approval_ref": "synthetic-approval",
            "valid_from": "2026-01-01",
            "valid_until": "2026-12-31",
            "records": [record],
        }
    ).encode()
    path = tmp_path / "manifest.json"
    path.write_bytes(raw)
    manifest = load_manifest(str(path), hashlib.sha256(raw).hexdigest())
    meta = {
        "chunk_id": "chunk-1",
        "status": "active",
        "doc_version_epoch": "reviewed-v1",
        "role_mask": 1,
        "dept_mask": 2,
    }
    candidate = SimpleNamespace(doc_id="doc-1", content=content, metadata=meta)
    today = date(2026, 11, 25)
    assert manifest.accepts(candidate, today)
    assert not manifest.accepts(candidate, date(2027, 1, 1))
    for field, value in (("role_mask", 0), ("doc_version_epoch", "old"), ("chunk_id", "unknown")):
        modified = SimpleNamespace(doc_id="doc-1", content=content, metadata={**meta, field: value})
        assert not manifest.accepts(modified, today)
    assert not manifest.accepts(SimpleNamespace(doc_id="doc-1", content="changed", metadata=meta), today)
    with pytest.raises(ValueError):
        load_manifest(str(path), "0" * 64)


def test_industrial_boot_rejects_development_identity(monkeypatch):
    monkeypatch.setenv("RAG_RELEASE_PROFILE", "industrial")
    with pytest.raises(RuntimeError):
        validate_industrial_startup({"deployment_mode": "development", "auth": {"dev_mode": True}})


def test_industrial_session_cannot_fallback_to_process_memory(monkeypatch):
    from core import pipeline_context

    monkeypatch.setenv("RAG_RELEASE_PROFILE", "industrial")
    monkeypatch.setattr(pipeline_context, "_get_redis_client", lambda: None)
    with pytest.raises(InfrastructureError):
        pipeline_context.SessionState.get_or_create("session-1", "owner-1", access_scope="test-scope")


def test_release_contract_imports_before_pipeline_in_clean_process():
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, "-c", "import common.release_contract; import core.pipeline"],
        check=True,
        timeout=60,
    )
