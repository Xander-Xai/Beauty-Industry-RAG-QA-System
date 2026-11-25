"""The reviewed release manifest must gate the real online retrieval chain.

Unit coverage of `ReleaseManifest.accepts` is not enough: the manifest is only
meaningful if the online path actually consults it. These tests drive
`ParallelRecallManager.execute()` — the single fusion entry point — and the
session scope fingerprint, so a future refactor that drops the call fails here
instead of silently admitting unreviewed evidence in industrial mode.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

APPROVED_CONTENT = "reviewed regulation text"
UNREVIEWED_CONTENT = "unreviewed draft text"
EPOCH = "reviewed-v1"


def _write_manifest(tmp_path, records):
    raw = json.dumps(
        {
            "schema": "rag-release/v1",
            "epoch": EPOCH,
            "approval_ref": "synthetic-approval",
            "valid_from": "2020-01-01",
            "valid_until": "2099-12-31",
            "records": records,
        }
    ).encode()
    path = tmp_path / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return str(path), hashlib.sha256(raw).hexdigest()


def _record(record_id, doc_id, content, role_mask=0, dept_mask=0):
    return {
        "record_id": record_id,
        "doc_id": doc_id,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "role_mask": role_mask,
        "dept_mask": dept_mask,
        "valid_from": "2020-01-01",
        "valid_until": "2099-12-31",
        "source_ref": "synthetic-source",
        "review_ref": "synthetic-review",
    }


def _manager_with(dense_results):
    from retrieval.parallel_recall import ParallelRecallManager

    class DenseRetriever:
        def __init__(self):
            self.qdrant_filter = None

        def search(self, query_embedding, qdrant_filter, top_k):
            self.qdrant_filter = qdrant_filter
            return dense_results

    manager = ParallelRecallManager.__new__(ParallelRecallManager)
    manager._dense_retriever = DenseRetriever()
    manager._bm25_retriever = None
    manager._clip_retriever = None
    manager._rewrite_variants_retriever = None
    manager.max_workers = 2
    manager._recall_clip = lambda query, qdrant_filter, top_k: []
    return manager


def _use_epoch(monkeypatch, epoch):
    """Point the online chain at the released epoch the manifest was approved for."""
    import retrieval.parallel_recall as parallel_recall

    monkeypatch.setitem(parallel_recall.config, "knowledge_version_epoch", epoch)


def _run(manager):
    return manager.execute(
        query="query",
        query_embedding=np.array([0.1]),
        user_role_mask=0,
        user_dept_mask=0,
        use_clip=False,
        clip_top_k=0,
        top_k_per_path={
            "dense_bge": {"enabled": True, "top_k": 5},
            "bm25_es": {"enabled": False, "top_k": 0},
            "clip_visual": {"enabled": False, "top_k": 0},
            "rewrite_variants": {"enabled": False, "top_k": 0},
        },
    )


def test_release_manifest_gates_the_online_recall_chain(tmp_path, monkeypatch):
    """Only evidence matching the reviewed manifest may enter the online chain."""
    manifest_path, digest = _write_manifest(
        tmp_path,
        [_record("chunk-approved", "doc-approved", APPROVED_CONTENT)],
    )
    monkeypatch.setenv("RAG_RELEASE_PROFILE", "industrial")
    monkeypatch.setenv("RAG_RELEASE_MANIFEST", manifest_path)
    monkeypatch.setenv("RAG_RELEASE_MANIFEST_SHA256", digest)
    _use_epoch(monkeypatch, EPOCH)

    manager = _manager_with(
        [
            {
                "doc_id": "doc-approved",
                "content": APPROVED_CONTENT,
                "score": 1.0,
                "metadata": {
                    "chunk_id": "chunk-approved",
                    "status": "active",
                    "doc_version_epoch": EPOCH,
                    "role_mask": 0,
                    "dept_mask": 0,
                },
            },
            {
                "doc_id": "doc-unreviewed",
                "content": UNREVIEWED_CONTENT,
                "score": 0.9,
                "metadata": {
                    "chunk_id": "chunk-unreviewed",
                    "status": "active",
                    "doc_version_epoch": EPOCH,
                    "role_mask": 0,
                    "dept_mask": 0,
                },
            },
        ]
    )

    results, _agreement = _run(manager)

    assert [result.doc_id for result in results] == ["doc-approved"]


def test_release_manifest_rejects_tampered_content_of_an_approved_chunk(tmp_path, monkeypatch):
    """A reviewed digest must not admit edited content under the same identity."""
    manifest_path, digest = _write_manifest(
        tmp_path,
        [_record("chunk-approved", "doc-approved", APPROVED_CONTENT)],
    )
    monkeypatch.setenv("RAG_RELEASE_PROFILE", "industrial")
    monkeypatch.setenv("RAG_RELEASE_MANIFEST", manifest_path)
    monkeypatch.setenv("RAG_RELEASE_MANIFEST_SHA256", digest)
    _use_epoch(monkeypatch, EPOCH)

    manager = _manager_with(
        [
            {
                "doc_id": "doc-approved",
                "content": f"{APPROVED_CONTENT} (edited after review)",
                "score": 1.0,
                "metadata": {
                    "chunk_id": "chunk-approved",
                    "status": "active",
                    "doc_version_epoch": EPOCH,
                    "role_mask": 0,
                    "dept_mask": 0,
                },
            }
        ]
    )

    results, _agreement = _run(manager)

    assert results == []


def test_release_manifest_rejects_permission_widening(tmp_path, monkeypatch):
    """The manifest permission masks are the reviewed grant, not a hint."""
    manifest_path, digest = _write_manifest(
        tmp_path,
        [_record("chunk-approved", "doc-approved", APPROVED_CONTENT, role_mask=0, dept_mask=0)],
    )
    monkeypatch.setenv("RAG_RELEASE_PROFILE", "industrial")
    monkeypatch.setenv("RAG_RELEASE_MANIFEST", manifest_path)
    monkeypatch.setenv("RAG_RELEASE_MANIFEST_SHA256", digest)
    _use_epoch(monkeypatch, EPOCH)

    manager = _manager_with(
        [
            {
                "doc_id": "doc-approved",
                "content": APPROVED_CONTENT,
                "score": 1.0,
                "metadata": {
                    "chunk_id": "chunk-approved",
                    "status": "active",
                    "doc_version_epoch": EPOCH,
                    "role_mask": 0xFFFFFFFF,
                    "dept_mask": 0xFFFFFFFF,
                },
            }
        ]
    )

    results, _agreement = _run(manager)

    assert results == []


def test_release_manifest_expires_with_its_applicability_window(tmp_path, monkeypatch):
    """An expired manifest must not keep serving evidence."""
    from core.exceptions import InfrastructureError

    manifest_path, digest = _write_manifest(
        tmp_path,
        [_record("chunk-approved", "doc-approved", APPROVED_CONTENT)],
    )
    monkeypatch.setenv("RAG_RELEASE_PROFILE", "industrial")
    monkeypatch.setenv("RAG_RELEASE_MANIFEST", manifest_path)
    monkeypatch.setenv("RAG_RELEASE_MANIFEST_SHA256", digest)
    _use_epoch(monkeypatch, EPOCH)

    manager = _manager_with(
        [
            {
                "doc_id": "doc-approved",
                "content": APPROVED_CONTENT,
                "score": 1.0,
                "metadata": {
                    "chunk_id": "chunk-approved",
                    "status": "active",
                    "doc_version_epoch": EPOCH,
                    "role_mask": 0,
                    "dept_mask": 0,
                },
            }
        ]
    )

    # Move "today" past the manifest validity window.
    import common.release_contract as release_contract

    future = release_contract.china_today() + timedelta(days=365 * 100)
    monkeypatch.setattr(release_contract, "china_today", lambda: future)

    # An expired release fails closed at the service boundary: the request is
    # refused rather than served with unverified evidence.
    with pytest.raises(InfrastructureError):
        _run(manager)


def test_default_profile_does_not_apply_the_release_manifest(tmp_path, monkeypatch):
    """Outside the industrial profile the manifest must not silently filter evidence."""
    manifest_path, digest = _write_manifest(
        tmp_path,
        [_record("chunk-approved", "doc-approved", APPROVED_CONTENT)],
    )
    monkeypatch.delenv("RAG_RELEASE_PROFILE", raising=False)
    monkeypatch.setenv("RAG_RELEASE_MANIFEST", manifest_path)
    monkeypatch.setenv("RAG_RELEASE_MANIFEST_SHA256", digest)
    _use_epoch(monkeypatch, EPOCH)

    manager = _manager_with(
        [
            {
                "doc_id": "doc-unreviewed",
                "content": UNREVIEWED_CONTENT,
                "score": 1.0,
                "metadata": {
                    "chunk_id": "chunk-unreviewed",
                    "status": "active",
                    "doc_version_epoch": EPOCH,
                    "role_mask": 0,
                    "dept_mask": 0,
                },
            }
        ]
    )

    results, _agreement = _run(manager)

    assert [result.doc_id for result in results] == ["doc-unreviewed"]


def test_session_scope_is_bound_to_the_release_digest(tmp_path, monkeypatch):
    """A different reviewed release must not reuse the previous session scope."""
    from core.pipeline_context import SessionState

    manifest_path, digest = _write_manifest(
        tmp_path,
        [_record("chunk-approved", "doc-approved", APPROVED_CONTENT)],
    )
    monkeypatch.setenv("RAG_RELEASE_PROFILE", "industrial")
    monkeypatch.setenv("RAG_RELEASE_MANIFEST", manifest_path)
    monkeypatch.setenv("RAG_RELEASE_MANIFEST_SHA256", digest)

    first = SessionState.scope_fingerprint(0, 0, EPOCH)

    # A different reviewed release (different approval reference -> different digest)
    # must not reuse the previous session scope.
    other_records = [_record("chunk-approved", "doc-approved", APPROVED_CONTENT)]
    other_records[0]["review_ref"] = "synthetic-review-second"
    other_path, other_digest = _write_manifest(tmp_path / "other", other_records)
    monkeypatch.setenv("RAG_RELEASE_MANIFEST", other_path)
    monkeypatch.setenv("RAG_RELEASE_MANIFEST_SHA256", other_digest)

    second = SessionState.scope_fingerprint(0, 0, EPOCH)

    assert first != second


def test_pipeline_refuses_to_release_evidence_that_failed_the_review(tmp_path, monkeypatch):
    """The post-generation re-check must withdraw an answer built on stale evidence."""
    from core.pipeline import OnlineRAGPipeline
    from core.pipeline_context import RequestContext, RerankResult
    from tests.test_pipeline_ordering import _setup_pipeline_mocks

    manifest_path, digest = _write_manifest(
        tmp_path,
        [_record("chunk-approved", "doc-approved", APPROVED_CONTENT)],
    )
    monkeypatch.setenv("RAG_RELEASE_PROFILE", "industrial")
    monkeypatch.setenv("RAG_RELEASE_MANIFEST", manifest_path)
    monkeypatch.setenv("RAG_RELEASE_MANIFEST_SHA256", digest)

    import core.pipeline as pipeline_module

    monkeypatch.setitem(pipeline_module.config, "knowledge_version_epoch", EPOCH)

    pipeline = OnlineRAGPipeline()
    _setup_pipeline_mocks(pipeline)
    pipeline._complexity_evaluator = MagicMock()
    pipeline._complexity_evaluator.evaluate.return_value = False

    # Reranking returns a chunk that is NOT in the reviewed manifest.
    pipeline._cross_encoder_ensemble.rerank.return_value = [
        RerankResult(
            doc_id="doc-stale",
            content="stale evidence",
            source="dense_bge",
            ce_score_ensemble=0.99,
            metadata={
                "chunk_id": "chunk-stale",
                "status": "active",
                "doc_version_epoch": EPOCH,
                "role_mask": 0,
                "dept_mask": 0,
            },
        )
    ]

    ctx = RequestContext(user_input="query", user_id="test_user")
    with patch("core.pipeline.log_audit_event"):
        pipeline.process(ctx)

    assert ctx.final_response == "知识证据已失效，请重新查询。"
    assert ctx.evidence_locked_doc_ids == []


def test_pipeline_releases_evidence_that_passed_the_review(tmp_path, monkeypatch):
    """A reviewed chunk must survive the post-generation re-check."""
    from core.pipeline import OnlineRAGPipeline
    from core.pipeline_context import RequestContext, RerankResult
    from tests.test_pipeline_ordering import _setup_pipeline_mocks

    manifest_path, digest = _write_manifest(
        tmp_path,
        [_record("chunk-approved", "doc-approved", APPROVED_CONTENT)],
    )
    monkeypatch.setenv("RAG_RELEASE_PROFILE", "industrial")
    monkeypatch.setenv("RAG_RELEASE_MANIFEST", manifest_path)
    monkeypatch.setenv("RAG_RELEASE_MANIFEST_SHA256", digest)

    import core.pipeline as pipeline_module

    monkeypatch.setitem(pipeline_module.config, "knowledge_version_epoch", EPOCH)

    pipeline = OnlineRAGPipeline()
    _setup_pipeline_mocks(pipeline)
    pipeline._complexity_evaluator = MagicMock()
    pipeline._complexity_evaluator.evaluate.return_value = False

    pipeline._cross_encoder_ensemble.rerank.return_value = [
        RerankResult(
            doc_id="doc-approved",
            content=APPROVED_CONTENT,
            source="dense_bge",
            ce_score_ensemble=0.99,
            metadata={
                "chunk_id": "chunk-approved",
                "status": "active",
                "doc_version_epoch": EPOCH,
                "role_mask": 0,
                "dept_mask": 0,
            },
        )
    ]

    ctx = RequestContext(user_input="query", user_id="test_user")
    with patch("core.pipeline.log_audit_event"):
        pipeline.process(ctx)

    assert ctx.final_response != "知识证据已失效，请重新查询。"
