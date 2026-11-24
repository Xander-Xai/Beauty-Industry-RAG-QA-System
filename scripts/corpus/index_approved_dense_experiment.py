"""Build a fresh, isolated Qdrant experiment from an approved ES corpus.

Default: read-only preflight. Never download weights, modify ES, delete a
collection, switch aliases, or activate an epoch. A failed write leaves the
experimental collection for operator inspection; use a new name to retry.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from benchmarks.backends import _looks_like_model_dir
from benchmarks.source_approval import gather_source_approval_evidence
from benchmarks.vector_corpus_parity import _read_es, _read_vectors, compare_payloads
from offline.text_ingestion import BGETextEmbedder, QdrantTextWriter, TextChunk
from scripts.corpus.index_real_corpus_v1_es import load_chunks

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def validate_targets(config: dict, confirmation: str | None, *, write: bool) -> tuple[str, str]:
    index = config["elasticsearch"]["index"]
    collection = config["embedding"]["text"]["collection"]
    if not re.fullmatch(r"cosmetics_docs_exp_[a-z0-9_]+", index):
        raise ValueError("isolated ES experiment index required")
    if not re.fullmatch(r"rag_text_exp_[a-z0-9_]+", collection):
        raise ValueError("isolated Qdrant experiment collection required")
    if config["qdrant"]["host"] not in _LOOPBACK:
        raise ValueError("loopback Qdrant required")
    if urlsplit(config["elasticsearch"]["host"]).hostname not in _LOOPBACK:
        raise ValueError("loopback ES required")
    if write and confirmation != collection:
        raise ValueError("explicit collection confirmation required")
    return index, collection


def prepare_chunks(rows: list[dict], records: list[dict], approval: dict, epoch: str) -> list[TextChunk]:
    """Require exact source chunk identities and text before embedding."""
    compare_payloads(rows, rows, approval)
    source = {(row["doc_id"], row["chunk_id"]): row for row in records}
    if len(source) != len(records) or len(source) != len(rows):
        raise ValueError("chunk manifest population mismatch")
    chunks = []
    for row in rows:
        record = source.get((row["doc_id"], row["chunk_id"]))
        if (
            record is None
            or record["text"] != row["content"]
            or record["corpus_version"] != epoch
            or row["doc_version_epoch"] != epoch
            or record["chunk_index"] != row["chunk_index"]
            or row["status"] != "active"
        ):
            raise ValueError("chunk manifest identity/text/epoch mismatch")
        chunks.append(
            TextChunk(
                doc_id=row["doc_id"],
                chunk_id=row["chunk_id"],
                source_path=row["source_path"],
                text=row["content"],
                chunk_index=row["chunk_index"],
                content_hash=record["content_hash"],
                role_mask=row["role_mask"],
                dept_mask=row["dept_mask"],
                status=row["status"],
                doc_version_epoch=epoch,
                metadata=row.get("metadata") or {},
                provenance=row["provenance"],
            )
        )
    return chunks


def run(config: dict, args: argparse.Namespace) -> dict:
    index, collection = validate_targets(config, args.confirm_collection, write=args.write)
    text_config = config["embedding"]["text"]
    model_path = Path(text_config["model_path"]).resolve()
    weights_ready, weights_detail = _looks_like_model_dir(model_path)
    if args.write and not weights_ready:
        raise ValueError("local BGE model assets incomplete")
    from models.embedding_service import EmbeddingService
    from retrieval.bm25_retriever import BM25Retriever

    es = BM25Retriever()
    if not es.enabled or es.es_client is None:
        raise ValueError("ES unavailable")
    approval = gather_source_approval_evidence(config=config, client=es.es_client)
    rows = _read_es(es.es_client, index)
    chunks = prepare_chunks(rows, load_chunks(args.chunks), approval, config["knowledge_version_epoch"])
    vectors = EmbeddingService().qdrant_client
    try:
        if vectors.collection_exists(collection):
            raise ValueError("collection already exists; choose a fresh experiment name")
        result = {
            "index": index,
            "collection": collection,
            "document_count": len(chunks),
            "local_model_assets_present": weights_ready,
            "local_model_assets_detail": weights_detail,
            "source_approval_verified": True,
            "experiment_only": True,
            "benchmark_executed": False,
        }
        if not args.write:
            return {**result, "status": "READY" if weights_ready else "BLOCKED_MODEL_ASSETS", "written": False}
        # Explicitly offline; this path accepts local model assets only.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        dimension = int(text_config["dimension"])
        embedder = BGETextEmbedder(str(model_path), dimension, model_revision=text_config.get("model_revision"))
        embeddings = embedder.embed_texts([chunk.text for chunk in chunks])
        from qdrant_client.http.models import Distance, VectorParams

        # Exclusive creation: do not use ensure_collection as an ownership check.
        created = vectors.create_collection(
            collection_name=collection, vectors_config=VectorParams(size=dimension, distance=Distance.COSINE)
        )
        if created is not True:
            raise ValueError("fresh collection creation not confirmed")
        writer = QdrantTextWriter(vectors, collection, dimension, embedding_version=embedder.embedding_version)
        writer.upsert(chunks, embeddings)
        receipt = compare_payloads(_read_es(es.es_client, index), _read_vectors(vectors, collection), approval)
        if receipt != compare_payloads(rows, rows, approval):
            raise ValueError("ES payload changed during build")
        return {**result, "status": "BUILT", "written": True, "parity": receipt}
    finally:
        vectors.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--confirm-collection")
    args = parser.parse_args(argv)
    from common.config import get_config_dict

    try:
        result = run(get_config_dict(), args)
    except Exception as exc:
        # No credentials, document bodies or external server error text.
        print(json.dumps({"status": "BLOCKED_OR_FAILED", "error_type": type(exc).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result["status"] == "BLOCKED_MODEL_ASSETS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
