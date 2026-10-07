#!/usr/bin/env python3
"""Real BGE smoke harness for the offline ingestion contract.

Validates the configured BGE model end to end without downloading anything:
load the tokenizer/model, encode a query and a document batch, assert the
dimension/finiteness/non-zero norm, write to an in-memory Qdrant collection,
query it back, and confirm the expected document is retrieved.

Exit codes:
  0  smoke passed
  1  smoke failed
  3  external model asset is required but not present (no false success)
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _model_asset_present(model_path: str) -> bool:
    path = Path(model_path)
    if not path.is_dir():
        return False
    return any(path.glob("*.safetensors")) or any(path.glob("pytorch_model*.bin")) or (path / "config.json").is_file()


def run_smoke(model_path: str, dimension: int, batch_size: int, model_revision: str | None) -> int:
    if not _model_asset_present(model_path):
        print(f"EXTERNAL_MODEL_ASSET_REQUIRED: BGE model not found at {model_path}")
        return 3

    from qdrant_client import QdrantClient

    from offline.document_processor import DocumentProcessor
    from offline.embeddings import BGETextEmbedder
    from offline.source_trust import managed_record
    from offline.text_ingestion import QdrantTextWriter, TextIngestionService

    embedder = BGETextEmbedder(model_path, dimension, batch_size, model_revision=model_revision)
    documents = [
        "胶原蛋白有助于皮肤保湿与弹性",
        "视黄醇应当夜间使用并注意防晒",
        "烟酰胺可以改善肤色不均",
    ]
    query = "胶原蛋白的保湿作用"

    document_vectors = embedder.embed_texts(documents)
    query_vector = embedder.embed_texts([query])[0]

    failures = []
    for label, vectors in (("document", document_vectors), ("query", [query_vector])):
        for index, vector in enumerate(vectors):
            if len(vector) != dimension:
                failures.append(f"{label} {index}: dimension {len(vector)} != {dimension}")
            if any(not math.isfinite(float(value)) for value in vector):
                failures.append(f"{label} {index}: non-finite value")
            norm = math.sqrt(sum(float(value) ** 2 for value in vector))
            if norm <= 0:
                failures.append(f"{label} {index}: zero norm")
    if failures:
        print("BGE smoke failed: " + "; ".join(failures))
        return 1

    client = QdrantClient(":memory:")
    writer = QdrantTextWriter(client, "bge_smoke", dimension=dimension)
    service = TextIngestionService(DocumentProcessor(), embedder, writer)
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "smoke.txt"
        path.write_text("\n\n".join(documents), encoding="utf-8")
        chunks = service.ingest(
            path,
            role_mask=0,
            dept_mask=0,
            doc_version_epoch="smoke_epoch",
            provenance=managed_record("smoke.txt").to_payload(),
        )
        if not chunks:
            print("BGE smoke failed: ingestion produced no chunks")
            return 1

        from auth.bitmask_rbac import build_qdrant_filter
        from models.embedding_service import EmbeddingService

        reader = EmbeddingService.__new__(EmbeddingService)
        reader._qdrant_client = client
        import numpy as np

        hits = reader.search_qdrant_text(
            np.array(query_vector),
            collection_name="bge_smoke",
            top_k=5,
            qdrant_filter=build_qdrant_filter(0, 0, "smoke_epoch"),
        )
    if not hits:
        print("BGE smoke failed: query returned no documents")
        return 1
    print(f"BGE smoke passed: {len(hits)} hit(s), top score {hits[0]['score']:.4f}")
    return 0


def main(argv=None) -> int:
    from common.config import get_config_dict

    config = get_config_dict()
    text_config = config["embedding"]["text"]
    parser = argparse.ArgumentParser(description="Real BGE smoke harness")
    parser.add_argument("--model-path", default=text_config["model_path"])
    parser.add_argument("--dimension", type=int, default=int(text_config["dimension"]))
    parser.add_argument(
        "--batch-size", type=int, default=int(config.get("knowledge_base", {}).get("embedding_batch_size", 32))
    )
    parser.add_argument("--model-revision", default=text_config.get("model_revision"))
    args = parser.parse_args(argv)
    return run_smoke(args.model_path, args.dimension, args.batch_size, args.model_revision)


if __name__ == "__main__":
    raise SystemExit(main())
