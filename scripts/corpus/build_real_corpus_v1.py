#!/usr/bin/env python3
"""Build the real corpus V1 chunk set from the committed official source records.

The offline pipeline's own :class:`offline.document_processor.DocumentProcessor`
does the parsing and chunking, so ``doc_id`` / ``chunk_id`` are produced by the
same code path a deployed rebuild would use. The only thing this tool adds is the
source bundle: it materializes one UTF-8 ``.txt`` per logical document, runs the
processor over it, and writes a manifest that binds every chunk back to its
official source locator.

Nothing here invents text. The inputs are the extracted official records under
``corpus/real_v1/sources``; the output is the deterministic chunk view of them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from offline.document_processor import DocumentProcessor  # noqa: E402
from offline.source_trust import (  # noqa: E402
    APPROVAL_NOT_REQUIRED,
    TRUST_MANAGED_INTERNAL,
    SourceTrustRecord,
)

DEFAULT_EPOCH = "real-v1-20261019"
SOURCE_FILES = ("regulation_727.jsonl", "used_ingredients_2021.jsonl")
_DATA_CLASS = "PUBLIC_OFFICIAL_DOCUMENT"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def load_source_records(sources_dir: Path) -> list[dict]:
    records: list[dict] = []
    for name in SOURCE_FILES:
        path = sources_dir / name
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            record["_source_file"] = name
            record["_source_line"] = line_number
            records.append(record)
    if not records:
        raise ValueError(f"no source records under {sources_dir}")
    return records


def _safe_filename(logical_id: str) -> str:
    return logical_id.replace("/", "__").replace(":", "_") + ".txt"


def _provenance(logical_id: str) -> dict:
    return SourceTrustRecord(
        source_id=logical_id,
        source_trust=TRUST_MANAGED_INTERNAL,
        approval_status=APPROVAL_NOT_REQUIRED,
    ).to_payload()


def build_corpus(
    sources_dir: Path,
    generated_dir: Path,
    *,
    epoch: str = DEFAULT_EPOCH,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    generated_at: str | None = None,
) -> tuple[list[dict], dict]:
    """Return ``(chunk_records, manifest)``. Deterministic for fixed inputs."""
    records = load_source_records(sources_dir)
    generated_dir.mkdir(parents=True, exist_ok=True)
    processor = DocumentProcessor(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        max_document_bytes=131_072,
        max_chunks=256,
        source_root=generated_dir,
    )

    chunks: list[dict] = []
    documents: list[dict] = []
    for record in records:
        logical_id = str(record["logical_id"])
        text = str(record["text"])
        document_path = generated_dir / _safe_filename(logical_id)
        document_path.write_text(text, encoding="utf-8")

        processed = processor.process(document_path, source_id=logical_id)
        units = processor.build_chunks(
            processed,
            role_mask=0,
            dept_mask=0,
            doc_version_epoch=epoch,
            provenance=_provenance(logical_id),
        )
        chunk_ids: list[str] = []
        for unit in units:
            chunk_ids.append(unit.chunk_id)
            chunks.append(
                {
                    "_data_class": _DATA_CLASS,
                    "_carries_real_regulatory_claims": True,
                    "corpus_version": epoch,
                    "logical_id": logical_id,
                    "doc_id": unit.doc_id,
                    "chunk_id": unit.chunk_id,
                    "chunk_index": unit.chunk_index,
                    "content_hash": unit.content_hash,
                    "text": unit.text,
                    "source_url": record.get("source_url", ""),
                    "source_document": record.get("source_document", ""),
                    "locator": record.get("locator", ""),
                    "license_basis": record.get("license_basis", ""),
                    "record_type": record.get("record_type", ""),
                }
            )
        documents.append(
            {
                "logical_id": logical_id,
                "record_type": record.get("record_type", ""),
                "locator": record.get("locator", ""),
                "source_url": record.get("source_url", ""),
                "source_document": record.get("source_document", ""),
                "license_basis": record.get("license_basis", ""),
                "source_file": record.get("_source_file", ""),
                "source_line": record.get("_source_line", 0),
                "doc_id": processed.doc_id,
                "content_hash": processed.content_hash,
                "chunk_count": len(chunk_ids),
                "chunk_ids": chunk_ids,
                "text_sha256": sha256_text(text),
            }
        )

    corpus_sha256 = hashlib.sha256()
    for item in sorted(documents, key=lambda entry: entry["doc_id"]):
        corpus_sha256.update(item["doc_id"].encode("utf-8"))
        corpus_sha256.update(b"\x1f")
        corpus_sha256.update(item["content_hash"].encode("utf-8"))
        corpus_sha256.update(b"\x1e")

    manifest = {
        "schema_version": "real-corpus/1",
        "corpus_version": epoch,
        "data_class": _DATA_CLASS,
        "carries_real_regulatory_claims": True,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "builder": "scripts/corpus/build_real_corpus_v1.py",
        "chunking": {"chunk_size": chunk_size, "chunk_overlap": chunk_overlap},
        "counts": {"documents": len(documents), "chunks": len(chunks)},
        "corpus_sha256_local": corpus_sha256.hexdigest(),
        "source_files": {name: sha256_file(sources_dir / name) for name in SOURCE_FILES},
        "documents": documents,
    }
    return chunks, manifest


def write_outputs(out_dir: Path, chunks: Iterable[dict], manifest: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False, sort_keys=True) + "\n")
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sources-dir", default="corpus/real_v1/sources")
    parser.add_argument("--out-dir", default="corpus/real_v1")
    parser.add_argument("--generated-dir", default="corpus/real_v1/.generated/documents")
    parser.add_argument("--epoch", default=DEFAULT_EPOCH)
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--chunk-overlap", type=int, default=50)
    args = parser.parse_args(argv)

    chunks, manifest = build_corpus(
        Path(args.sources_dir),
        Path(args.generated_dir),
        epoch=args.epoch,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )
    write_outputs(Path(args.out_dir), chunks, manifest)
    print(f"documents: {manifest['counts']['documents']}")
    print(f"chunks:    {manifest['counts']['chunks']}")
    print(f"corpus_version: {manifest['corpus_version']}")
    print(f"corpus_sha256_local: {manifest['corpus_sha256_local']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
