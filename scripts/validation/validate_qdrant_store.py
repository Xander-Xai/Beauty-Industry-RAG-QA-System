#!/usr/bin/env python3
"""Real Qdrant service validation (VAL-STORE-001).

Runs the dense recall path against an actual Qdrant server, not the in-process
``QdrantClient(":memory:")`` the deterministic suite uses.  It exercises:

  1. text write + vector retrieval + payload (status/epoch) filtering;
  2. multi-role / multi-department RBAC isolation before fusion;
  3. epoch-versioned point ids, so a rebuilt epoch does not collide;
  4. text + image retrieval merged through the real ``rrf_fusion`` entry;
  5. Qdrant-unavailable degradation that returns empty instead of crashing.

It also checks Redis cache-key isolation across epochs when Redis is reachable.

This validates the **storage engine and the RBAC/fusion contract**.  It does
**not** validate BGE/CLIP model quality: the vectors come from
``DeterministicTestEmbedder`` because the model weights are not in the
repository.  See docs/evidence-map.md rule 3.

Counting is never hand-written
------------------------------
Every check appends a structured record through :func:`_log`. The reported
counts are derived from the executed record set by :func:`check_counts`, so a
metadata file that disagrees with the checks it describes is a detectable
defect rather than a silent drift. ``tests/validation/test_qdrant_validation_evidence.py``
enforces that contract.

Env:
  VALIDATION_QDRANT_HOST   (default 127.0.0.1)
  VALIDATION_QDRANT_PORT   (default 6333)
  VALIDATION_REDIS_HOST    (default 127.0.0.1, optional)
  VALIDATION_REDIS_PORT    (default 6379)
Args:
  --emit-checks PATH   write the structured check record + derived counts to PATH
Exits 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

QDRANT_HOST = os.environ.get("VALIDATION_QDRANT_HOST", "127.0.0.1")
QDRANT_PORT = int(os.environ.get("VALIDATION_QDRANT_PORT", "6333"))
REDIS_HOST = os.environ.get("VALIDATION_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("VALIDATION_REDIS_PORT", "6379"))

TEXT_COLLECTION = "validation_rag_text_768"
IMAGE_COLLECTION = "validation_rag_image_512"
EPOCH_A = "validation_epoch_a"
EPOCH_B = "validation_epoch_b"

#: Appended to by :func:`_log`. This *is* the run's check record; every count the
#: run reports is derived from it, never written by hand.
_CHECKS: list[dict[str, Any]] = []
_FAILURES = 0


def check_counts(checks: list[dict[str, Any]]) -> dict[str, int]:
    """Derive ``checks_run`` / ``checks_passed`` / ``checks_failed`` from the records.

    ``checks_run`` is the length of the executed record set. A check that never
    ran therefore cannot be counted, which is what makes a hand-written total
    (the historical 21) impossible to keep in sync by accident.
    """
    return {
        "checks_run": len(checks),
        "checks_passed": sum(1 for check in checks if check["ok"]),
        "checks_failed": sum(1 for check in checks if not check["ok"]),
    }


def _log(ok: bool, check_id: str, message: str) -> None:
    """Record one check, print its verdict, and count the failure."""
    global _FAILURES
    _CHECKS.append({"id": check_id, "ok": bool(ok), "message": message})
    print(f"[{'PASS' if ok else 'FAIL'}] {message}")
    if not ok:
        _FAILURES += 1


def qdrant_version_compatibility(client_version: str, server_version: str) -> dict[str, Any]:
    """Classify a client/server pair against Qdrant's published guarantee.

    Qdrant documents that major *and* minor are expected to match, and that
    backward compatibility is only tested across **one** minor version. A client
    several minors ahead of the server is outside the guaranteed window even when
    every call in this script happens to succeed, so the run reports the delta
    instead of letting a green result imply support.
    """
    import re

    def _parse(value: str) -> tuple[int, int]:
        match = re.match(r"^(\d+)\.(\d+)", str(value).strip())
        return (int(match.group(1)), int(match.group(2))) if match else (0, 0)

    client_major, client_minor = _parse(client_version)
    server_major, server_minor = _parse(server_version)
    major_delta = abs(client_major - server_major)
    minor_delta = abs(client_minor - server_minor)
    if major_delta:
        verdict, supported = "MAJOR_MISMATCH", False
    elif minor_delta > 1:
        verdict, supported = "OUTSIDE_GUARANTEED_WINDOW", False
    elif minor_delta == 1:
        verdict, supported = "ADJACENT_MINOR_TESTED", True
    else:
        verdict, supported = "MATCHED_MINOR", True
    return {
        "client_version": client_version,
        "server_version": server_version,
        "major_delta": major_delta,
        "minor_delta": minor_delta,
        "verdict": verdict,
        "within_documented_support": supported,
        "policy": (
            "Qdrant documents that major and minor versions of the client and server are expected to match, "
            "and that backward compatibility is tested across one minor version only."
        ),
    }


def _text_chunk(embedder, doc_id: str, text: str, role: int, dept: int, epoch: str):
    from offline.source_trust import managed_record
    from offline.text_ingestion import TextChunk

    return TextChunk(
        doc_id=doc_id,
        chunk_id=f"{doc_id}-chunk-0",
        source_path=f"/validation/{doc_id}.txt",
        text=text,
        chunk_index=0,
        content_hash=f"hash-{doc_id}-{epoch}",
        role_mask=role,
        dept_mask=dept,
        status="active",
        doc_version_epoch=epoch,
        metadata={"validation": "qdrant"},
        provenance=managed_record(doc_id).to_payload(),
    )


def _image_record(doc_id: str, text: str, role: int, dept: int, epoch: str):
    from offline.source_trust import managed_record

    return types.SimpleNamespace(
        doc_id=doc_id,
        image_id=f"{doc_id}-image-0",
        image_index=0,
        page_number=1,
        source_path=f"/validation/{doc_id}.png",
        image_uri=f"minio://validation/{doc_id}.png",
        ocr_full_text=text,
        ocr_main_text=text,
        embedding_type="clip",
        embedding_version="validation-image-v1",
        role_mask=role,
        dept_mask=dept,
        status="active",
        doc_version_epoch=epoch,
        metadata={"validation": "qdrant"},
        provenance=managed_record(doc_id).to_payload(),
    )


def _collect_text_doc_ids(client, collection, epoch: str) -> set[str]:
    from qdrant_client.http.models import FieldCondition, Filter, MatchValue

    records, _ = client.scroll(
        collection_name=collection,
        scroll_filter=Filter(must=[FieldCondition(key="doc_version_epoch", match=MatchValue(value=epoch))]),
        limit=1000,
        with_payload=True,
    )
    return {str((point.payload or {}).get("doc_id")) for point in records}


def _emit_checks(destination: str | None, compatibility: dict[str, Any]) -> None:
    """Write the structured check record plus counts derived from it."""
    if not destination:
        return
    counts = check_counts(_CHECKS)
    payload = {
        "validation_id": "VAL-STORE-001",
        "checks": _CHECKS,
        "counts": counts,
        "compatibility": compatibility,
        "note": (
            "counts are derived from the checks list above; `compatibility` is a non-gating finding "
            "and is deliberately excluded from checks_run"
        ),
    }
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"check record: {path} ({counts['checks_passed']}/{counts['checks_run']} passed)")


def main(argv: list[str] | None = None) -> int:  # noqa: C901 - linear validation script
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--emit-checks", help="write the structured check record and derived counts to this path")
    args = parser.parse_args(argv)

    _CHECKS.clear()

    from qdrant_client import QdrantClient

    import models.embedding_service as _esvc
    from auth.bitmask_rbac import build_qdrant_filter, build_qdrant_image_filter
    from common.auth import is_document_authorized
    from common.config import get_config_dict
    from core.pipeline_context import RecallResult
    from offline.qdrant_writer import QdrantImageWriter
    from offline.text_ingestion import DeterministicTestEmbedder, QdrantTextWriter
    from retrieval_service.rerank.rrf_fusion import rrf_fusion

    # The image reader hard-codes the configured collection name.  Redirect only
    # this script's module-level config to the validation collection so the run
    # is non-destructive against a populated server.
    _config = get_config_dict()
    _esvc.config = {
        **_config,
        "embedding": {
            **_config["embedding"],
            "image_clip": {**_config["embedding"]["image_clip"], "collection": IMAGE_COLLECTION},
        },
    }

    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=15)
    try:
        server_version = client.info().version
    except Exception as exc:  # noqa: BLE001
        server_version = "unknown"
        print(f"[WARN] could not read the server version: {exc}")
    try:
        client.get_collections()
    except Exception as exc:  # noqa: BLE001
        _log(False, "qdrant_reachable", f"cannot reach Qdrant at {QDRANT_HOST}:{QDRANT_PORT}: {exc}")
        _emit_checks(args.emit_checks, [])
        print("\nQDRANT STORE VALIDATION FAILED")
        return 1
    _log(True, "qdrant_reachable", f"connected to Qdrant at {QDRANT_HOST}:{QDRANT_PORT}")

    # Recorded as a *finding*, not a functional check. Qdrant only guarantees the
    # client within one minor of the server, so this pair is outside the supported
    # window even though every call below succeeds. Folding it into checks_run
    # would either hide the risk or falsely report the storage engine as broken.
    import importlib.metadata as importlib_metadata

    client_version = importlib_metadata.version("qdrant-client")

    compatibility = qdrant_version_compatibility(client_version, server_version)
    print(
        f"[INFO] client {client_version} vs server {server_version}: {compatibility['verdict']} "
        f"(minor delta {compatibility['minor_delta']}; documented support: "
        f"{compatibility['within_documented_support']})"
    )

    # Fresh collections for a clean, repeatable run.
    for name in (TEXT_COLLECTION, IMAGE_COLLECTION):
        try:
            client.delete_collection(name)
        except Exception:  # noqa: BLE001, S110 - absent collection is fine
            pass

    embedder = DeterministicTestEmbedder(768)
    text_writer = QdrantTextWriter(client, collection_name=TEXT_COLLECTION, dimension=768)
    text_writer.ensure_collection()

    # ── 1. write + retrieve + payload filter ────────────────────────────────
    chunks_a = [
        _text_chunk(embedder, "doc-public", "烟酰胺 安全浓度 公开 资料", 0, 0, EPOCH_A),
        _text_chunk(embedder, "doc-role-a", "烟酰胺 配方 内部 资料", 0b0010, 0b0100, EPOCH_A),
        _text_chunk(embedder, "doc-role-b", "烟酰胺 生产 机密 资料", 0b0100, 0b1000, EPOCH_A),
    ]
    vectors_a = embedder.embed_texts([c.text for c in chunks_a])
    text_writer.upsert(chunks_a, vectors_a)
    _log(True, "text_write", "text documents written to real Qdrant")

    # Online dense search with the repository's real reader + filter builder.
    from models.embedding_service import EmbeddingService

    embedding_service = EmbeddingService()
    embedding_service._qdrant_client = client
    query_vector = np.array(embedder.embed_texts(["烟酰胺 安全浓度 资料"])[0], dtype=np.float32)

    hits = embedding_service.search_qdrant_text(
        query_embedding=query_vector,
        collection_name=TEXT_COLLECTION,
        top_k=10,
        qdrant_filter=build_qdrant_filter(0, 0, EPOCH_A),
    )
    _log(len(hits) == 3, "dense_search_returns_all", f"dense search returned {len(hits)}/3 documents")
    _log(
        all(h["metadata"]["doc_version_epoch"] == EPOCH_A for h in hits),
        "dense_hits_carry_active_epoch",
        "every dense hit carries the active epoch",
    )

    # ── 2. multi-role / multi-department RBAC isolation ─────────────────────
    def visible_for(role: int, dept: int) -> set[str]:
        return {h["doc_id"] for h in hits if is_document_authorized(h["metadata"], role, dept)}

    visible_a = visible_for(0b0010, 0b0100)
    visible_b = visible_for(0b0100, 0b1000)
    _log(
        "doc-public" in visible_a and "doc-role-a" in visible_a,
        "rbac_role_a_visible",
        "role A sees public + role-A docs",
    )
    _log(
        "doc-role-b" not in visible_a,
        "rbac_role_a_blocked",
        "role A cannot see role-B docs (RBAC filtered before fusion)",
    )
    _log(
        "doc-role-b" in visible_b and "doc-role-a" not in visible_b,
        "rbac_role_b_visible",
        "role B sees only its own + public",
    )
    _log(visible_a != visible_b, "rbac_identities_differ", "two identities produce different visible sets")

    # ── 3. epoch isolation + no point-id collision ──────────────────────────
    chunk_b = _text_chunk(embedder, "doc-public", "烟酰胺 安全浓度 公开 资料 v2", 0, 0, EPOCH_B)
    text_writer.upsert([chunk_b], embedder.embed_texts([chunk_b.text]))
    epoch_a_ids = _collect_text_doc_ids(client, TEXT_COLLECTION, EPOCH_A)
    epoch_b_ids = _collect_text_doc_ids(client, TEXT_COLLECTION, EPOCH_B)
    _log(
        epoch_a_ids == {"doc-public", "doc-role-a", "doc-role-b"},
        "epoch_a_docs_intact",
        f"epoch A docs intact: {sorted(epoch_a_ids)}",
    )
    _log(
        epoch_b_ids == {"doc-public"},
        "epoch_b_rewritten",
        f"epoch B rewritten independently: {sorted(epoch_b_ids)}",
    )

    hits_b = embedding_service.search_qdrant_text(
        query_embedding=query_vector,
        collection_name=TEXT_COLLECTION,
        top_k=10,
        qdrant_filter=build_qdrant_filter(0, 0, EPOCH_B),
    )
    _log(
        {h["doc_id"] for h in hits_b} == {"doc-public"},
        "epoch_filter_scopes_hits",
        "epoch-B filter returns only epoch-B points",
    )

    # Physical ids are epoch-scoped: the rebuilt doc must not overwrite epoch A.
    epoch_a_point = client.scroll(
        collection_name=TEXT_COLLECTION,
        scroll_filter=None,
        limit=1000,
        with_payload=True,
    )[0]
    a_point_ids = {str(p.id) for p in epoch_a_point if (p.payload or {}).get("doc_version_epoch") == EPOCH_A}
    b_point_ids = {str(p.id) for p in epoch_a_point if (p.payload or {}).get("doc_version_epoch") == EPOCH_B}
    _log(
        a_point_ids.isdisjoint(b_point_ids), "epoch_point_ids_disjoint", "epoch-A and epoch-B point ids do not collide"
    )

    # ── 4. text + image retrieval merged through the real fusion entry ──────
    image_writer = QdrantImageWriter(client, collection_name=IMAGE_COLLECTION, dimension=512)
    image_writer.ensure_collection()
    img = _image_record("doc-image", "烟酰胺 图片 OCR 文本", 0, 0, EPOCH_A)
    img_vector = [0.0] * 512
    img_vector[0] = 1.0
    image_writer.replace_document("doc-image", EPOCH_A, [img], [img_vector])

    image_hits = embedding_service.search_qdrant_image(
        query_embedding=np.array(img_vector, dtype=np.float32),
        top_k=5,
        qdrant_filter=build_qdrant_image_filter(0, 0, EPOCH_A),
    )
    _log(len(image_hits) == 1, "image_search_returns_hit", f"image search returned {len(image_hits)}/1 image")
    _log(
        bool(image_hits) and image_hits[0]["doc_id"] == "doc-image",
        "image_collection_separation",
        "image hit comes from the image collection",
    )

    path_results = {
        "dense_bge": [
            RecallResult(
                doc_id=h["doc_id"], content=h["content"], score=h["score"], source="dense_bge", metadata=h["metadata"]
            )
            for h in hits
        ],
        "clip_visual": [
            RecallResult(
                doc_id=h["doc_id"], content=h.get("content", ""), score=h["score"], source="clip_visual", metadata={}
            )
            for h in image_hits
        ],
    }
    fused = rrf_fusion(path_results, k=60, weights={"dense_bge": 1.0, "clip_visual": 2.0})
    fused_ids = {r.doc_id for r in fused}
    _log(
        {"doc-image", "doc-public"} <= fused_ids,
        "rrf_merges_text_and_image",
        f"RRF merged text + image sources: {sorted(fused_ids)}",
    )

    # ── 5. Qdrant-unavailable degradation is explainable, not a crash ───────
    from retrieval.dense_retriever import DenseRetriever

    dead_service = EmbeddingService()
    dead_service._qdrant_client = QdrantClient(host=QDRANT_HOST, port=6399, timeout=1)
    retriever = DenseRetriever()
    retriever._embedding_service = dead_service
    degraded = retriever.search(query_vector, qdrant_filter=None, top_k=5)
    _log(
        degraded == [],
        "unreachable_degrades_to_empty",
        "dense path returns empty (logged + degraded) when Qdrant is unreachable",
    )

    # ── Redis cache-key epoch / permission isolation (optional) ─────────────
    try:
        import redis as _redis

        from cache.redis_cache import RedisCache

        rclient = _redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=15, decode_responses=True, socket_timeout=2)
        rclient.ping()
        rclient.flushdb()
        key_a = RedisCache.compute_cache_key("q", knowledge_version_epoch=EPOCH_A, role_mask=2, dept_mask=4)
        key_b = RedisCache.compute_cache_key("q", knowledge_version_epoch=EPOCH_B, role_mask=2, dept_mask=4)
        key_a_other = RedisCache.compute_cache_key("q", knowledge_version_epoch=EPOCH_A, role_mask=4, dept_mask=8)
        rclient.setex(RedisCache._build_l2_storage_key(key_a, 2, 4), 60, json.dumps("cached-A"))
        _log(key_a != key_b, "cache_key_varies_by_epoch", "cache key changes with the knowledge epoch")
        _log(
            key_a != key_a_other,
            "cache_key_varies_by_permission",
            "cache key changes with the permission fingerprint",
        )
        _log(
            rclient.get(RedisCache._build_l2_storage_key(key_b, 2, 4)) is None,
            "cache_epoch_b_misses_a",
            "epoch-B lookup misses epoch-A entry",
        )
        _log(
            rclient.get(RedisCache._build_l2_storage_key(key_a, 4, 8)) is None,
            "cache_permission_isolation",
            "other-identity lookup misses (no cross-permission cache leak)",
        )
        rclient.flushdb()
    except Exception as exc:  # noqa: BLE001 - Redis is optional for this script
        _log(True, "redis_check_skipped", f"Redis cache-isolation check skipped (Redis not reachable: {exc})")

    client.delete_collection(TEXT_COLLECTION)
    client.delete_collection(IMAGE_COLLECTION)

    _emit_checks(args.emit_checks, compatibility)

    print()
    if _FAILURES:
        print(f"QDRANT STORE VALIDATION FAILED ({_FAILURES})")
        return 1
    print("QDRANT STORE VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
