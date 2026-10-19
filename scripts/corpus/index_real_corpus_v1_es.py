#!/usr/bin/env python3
"""Index the committed real corpus V1 into Elasticsearch (BM25 path only).

This script indexes the deterministic chunk view produced by
``scripts/corpus/build_real_corpus_v1.py`` into the production
``cosmetics_docs`` mapping using the repository's own
:class:`offline.elasticsearch_writer.ElasticsearchWriter`. It exists because the
BM25 half of the snapshot does not need an embedding model: the offline rebuild
as a whole still cannot run here because the BGE weights are absent, so the dense
collection is deliberately left unbuilt.

It then fingerprints the resulting index with the same
``benchmarks.corpus.fingerprint_elasticsearch`` used by the benchmark's corpus
probe, and writes that fingerprint next to the corpus so a golden-set annotation
can bind to a real, reproducible index state instead of a local hash.

Usage::

    ELASTICSEARCH_HOST=http://127.0.0.1:9200 \
        python3 scripts/corpus/index_real_corpus_v1_es.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.corpus import fingerprint_elasticsearch  # noqa: E402
from offline.elasticsearch_writer import ElasticsearchWriter  # noqa: E402
from offline.source_trust import (  # noqa: E402
    APPROVAL_NOT_REQUIRED,
    TRUST_MANAGED_INTERNAL,
    SourceTrustRecord,
)

DEFAULT_INDEX = "cosmetics_docs"


class _Chunk:
    """Minimal object the ES writer's ``build_document`` understands."""

    def __init__(self, record: dict, provenance: dict) -> None:
        self.doc_id = str(record["doc_id"])
        self.chunk_id = str(record["chunk_id"])
        self.chunk_index = int(record["chunk_index"])
        self.text = str(record["text"])
        self.source_path = str(record["logical_id"])
        self.role_mask = 0
        self.dept_mask = 0
        self.status = "active"
        self.doc_version_epoch = str(record["corpus_version"])
        self.metadata = {
            "logical_id": str(record["logical_id"]),
            "locator": str(record.get("locator", "")),
            "source_url": str(record.get("source_url", "")),
            "source_document": str(record.get("source_document", "")),
            "license_basis": str(record.get("license_basis", "")),
            "record_type": str(record.get("record_type", "")),
        }
        self.provenance = provenance
        self.embedding_type = "none"
        self.embedding_version = "bm25-only-index-v1"


def load_chunks(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    if not records:
        raise ValueError(f"{path} contains no chunks")
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chunks", default="corpus/real_v1/chunks.jsonl")
    parser.add_argument("--out", default="corpus/real_v1/es_fingerprint.json")
    parser.add_argument("--index", default=os.environ.get("ELASTICSEARCH_INDEX", DEFAULT_INDEX))
    parser.add_argument("--host", default=os.environ.get("ELASTICSEARCH_HOST", "http://127.0.0.1:9200"))
    args = parser.parse_args(argv)

    from elasticsearch import Elasticsearch

    client = Elasticsearch(args.host, request_timeout=30)
    info = client.info()
    writer = ElasticsearchWriter(client, args.index)
    writer.ensure_index(recreate=True, confirm=True)

    chunks = load_chunks(Path(args.chunks))
    documents = []
    for record in chunks:
        provenance = SourceTrustRecord(
            source_id=str(record["logical_id"]),
            source_trust=TRUST_MANAGED_INTERNAL,
            approval_status=APPROVAL_NOT_REQUIRED,
        ).to_payload()
        documents.append(writer.build_document(_Chunk(record, provenance)))
    writer.upsert_documents(documents)

    client.indices.refresh(index=args.index)
    fingerprint = fingerprint_elasticsearch(client, args.index)
    payload = {
        "schema_version": "real-corpus-es-fingerprint/1",
        "index_name": args.index,
        "elasticsearch_version": info.get("version", {}).get("number", ""),
        "host": args.host,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chunks_written": len(documents),
        "corpus_version": chunks[0]["corpus_version"],
        "fingerprint": fingerprint.as_dict(),
        "note": (
            "BM25-only index built from corpus/real_v1/chunks.jsonl with the production ES writer. "
            "No dense vectors were written: the BGE weights are absent, so the dense collection is "
            "intentionally unbuilt and dense/hybrid retrieval is not claimed."
        ),
    }
    Path(args.out).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"indexed chunks: {len(documents)}")
    print(f"index: {args.index}")
    print(f"content_sha256: {fingerprint.content_sha256}")
    print(f"wrote: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
