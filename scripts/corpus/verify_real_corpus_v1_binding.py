#!/usr/bin/env python3
"""Verify that the committed corpus, its candidates and a live ES index agree.

This is a read-only consistency check for the "is the benchmark bound to the
corpus it claims" question. It writes nothing and deletes nothing.

It answers six independent questions and reports each one separately, because a
single "OK" would hide which link broke:

1. **Source bytes** — do ``corpus/real_v1/sources/*.jsonl`` still hash to the
   values recorded in ``manifest.json``?
2. **Local corpus** — does rebuilding the manifest digest from ``chunks.jsonl``
   reproduce ``corpus_sha256_local``?
3. **Candidate binding** — do the 27 candidates use the same ``corpus_version``
   and ``corpus_sha256`` as the committed fingerprint?
4. **Live index identity** — does the live index's content fingerprint equal the
   committed ``es_fingerprint.json`` fingerprint?
5. **Live index content** — does the live index contain the expected epoch and
   chunk count, and can every annotated ``(doc_id, chunk_id)`` be resolved?
6. **Trust state** — what provenance classes does the live index actually carry?
   An index whose documents are ``MANAGED_INTERNAL`` cannot be the output of the
   current indexer, which declares external documents ``UNTRUSTED``.
7. **Configured epoch** — does the retrieval configuration filter on the epoch the
   index actually contains? ``knowledge_version_epoch`` defaults to ``default``,
   which would make BM25 return zero rows against a ``real-v1-*`` index.

Exit codes: ``0`` every check passed, ``1`` at least one check failed,
``2`` the live index could not be inspected (reported as BLOCKED, not as a pass).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

STATUS_OK = "PASS"
STATUS_FAIL = "FAIL"
STATUS_BLOCKED = "BLOCKED"
STATUS_WARN = "WARN"


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def local_corpus_digest(chunks: list[dict]) -> str:
    """Reproduce ``build_real_corpus_v1.py``'s digest from the chunk view."""
    digest = hashlib.sha256()
    seen = {(str(chunk["doc_id"]), str(chunk.get("content_hash", ""))) for chunk in chunks}
    for doc_id, content_hash in sorted(seen):
        digest.update(doc_id.encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(content_hash.encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()


def check_source_bytes(corpus_dir: Path, manifest: dict) -> dict:
    expected = manifest.get("source_files") or {}
    mismatches = {}
    for name, recorded in sorted(expected.items()):
        path = corpus_dir / "sources" / name
        if not path.is_file():
            mismatches[name] = "missing"
        elif _sha256_file(path) != recorded:
            mismatches[name] = f"sha256 {_sha256_file(path)} != recorded {recorded}"
    return {
        "check": "source_bytes",
        "status": STATUS_OK if not mismatches else STATUS_FAIL,
        "detail": mismatches or f"{len(expected)} source file(s) match the manifest",
    }


def check_local_corpus(manifest: dict, chunks: list[dict]) -> dict:
    recorded = manifest.get("corpus_sha256_local")
    actual = local_corpus_digest(chunks)
    counts = manifest.get("counts") or {}
    count_ok = counts.get("chunks") == len(chunks) and counts.get("documents") == len(
        {str(chunk["logical_id"]) for chunk in chunks}
    )
    ok = actual == recorded and count_ok
    return {
        "check": "local_corpus",
        "status": STATUS_OK if ok else STATUS_FAIL,
        "detail": {
            "recorded": recorded,
            "recomputed": actual,
            "counts_recorded": counts,
            "chunks_actual": len(chunks),
            "documents_actual": len({str(chunk["logical_id"]) for chunk in chunks}),
        },
    }


def check_candidates(fingerprint: dict, candidates: list[dict]) -> dict:
    expected_version = fingerprint.get("corpus_version")
    expected_sha = (fingerprint.get("fingerprint") or {}).get("content_sha256")
    problems = []
    for candidate in candidates:
        if candidate.get("corpus_version") != expected_version:
            problems.append(f"{candidate.get('sample_id')}: corpus_version {candidate.get('corpus_version')!r}")
        if candidate.get("corpus_sha256") != expected_sha:
            problems.append(f"{candidate.get('sample_id')}: corpus_sha256 mismatch")
    return {
        "check": "candidate_binding",
        "status": STATUS_OK if not problems else STATUS_FAIL,
        "detail": problems or f"{len(candidates)} candidate(s) bound to {expected_version} / {expected_sha}",
    }


def _client(host: str):
    from elasticsearch import Elasticsearch

    return Elasticsearch(host, request_timeout=10)


def check_index_identity(client, index: str, fingerprint: dict) -> dict:
    from benchmarks.corpus import fingerprint_elasticsearch

    expected = (fingerprint.get("fingerprint") or {}).get("content_sha256")
    live = fingerprint_elasticsearch(client, index)
    if not live.reachable:
        return {"check": "index_identity", "status": STATUS_BLOCKED, "detail": live.error}
    ok = live.content_sha256 == expected
    return {
        "check": "index_identity",
        "status": STATUS_OK if ok else STATUS_FAIL,
        "detail": {
            "expected": expected,
            "live": live.content_sha256,
            "document_count": live.document_count,
            "sampled_documents": live.sampled_documents,
        },
    }


def check_index_content(client, index: str, manifest: dict, candidates: list[dict]) -> dict:
    epoch = manifest.get("corpus_version")
    expected_chunks = (manifest.get("counts") or {}).get("chunks")
    try:
        count = int(client.count(index=index).get("count", 0))
        epoch_count = int(
            client.count(
                index=index,
                query={"term": {"doc_version_epoch": epoch}},
            ).get("count", 0)
        )
    except Exception as exc:  # noqa: BLE001
        return {"check": "index_content", "status": STATUS_BLOCKED, "detail": f"{type(exc).__name__}: {exc}"}

    unresolved = []
    for candidate in candidates:
        for annotation in candidate.get("annotations") or []:
            response = client.count(
                index=index,
                query={
                    "bool": {
                        "must": [
                            {"term": {"doc_id": annotation["doc_id"]}},
                            {"term": {"chunk_id": annotation["chunk_id"]}},
                            {"term": {"doc_version_epoch": epoch}},
                        ]
                    }
                },
            )
            if int(response.get("count", 0)) < 1:
                unresolved.append(f"{candidate['sample_id']}:{annotation['chunk_id']}")

    ok = count == expected_chunks and epoch_count == expected_chunks and not unresolved
    return {
        "check": "index_content",
        "status": STATUS_OK if ok else STATUS_FAIL,
        "detail": {
            "index_document_count": count,
            "expected_chunks": expected_chunks,
            f"documents_in_epoch_{epoch}": epoch_count,
            "unresolved_annotations": unresolved,
        },
    }


def check_trust_state(client, index: str) -> dict:
    try:
        response = client.search(
            index=index,
            body={
                "size": 0,
                "aggs": {
                    "trust": {"terms": {"field": "provenance.trust_class", "size": 10}},
                    "epochs": {"terms": {"field": "doc_version_epoch", "size": 20}},
                },
            },
        )
    except Exception as exc:  # noqa: BLE001
        return {"check": "trust_state", "status": STATUS_BLOCKED, "detail": f"{type(exc).__name__}: {exc}"}
    aggregations = response.get("aggregations") or {}
    trust = {
        str(bucket["key"]): int(bucket["doc_count"])
        for bucket in (aggregations.get("trust") or {}).get("buckets", [])
    }
    epochs = [str(bucket["key"]) for bucket in (aggregations.get("epochs") or {}).get("buckets", [])]
    # The current indexer declares external documents UNTRUSTED, so a live index
    # whose documents are all MANAGED_INTERNAL was produced by the previous
    # (unsafe) code path and its provenance must not be trusted.
    if not trust:
        status = STATUS_FAIL
        detail = "no provenance.trust_class on any document; the index predates the trust contract"
    elif set(trust) == {"MANAGED_INTERNAL"}:
        status = STATUS_FAIL
        detail = (
            "all documents are MANAGED_INTERNAL, which the current indexer cannot produce for "
            "external official documents; this index was written by the previous code path and "
            "must be rebuilt after a named review. It is not evidence that the sources were approved."
        )
    elif "UNTRUSTED" in trust:
        status = STATUS_WARN
        detail = "index contains UNTRUSTED documents; it is a staging index, not an activatable snapshot"
    else:
        status = STATUS_OK
        detail = "index carries approved external provenance"
    return {
        "check": "trust_state",
        "status": status,
        "detail": {"trust_class_counts": trust, "epochs": epochs, "verdict": detail},
    }


def check_config_epoch(manifest: dict, config_path: str | None = None) -> dict:
    """Compare the retrieval epoch filter with the corpus epoch.

    ``retrieval/bm25_retriever.py`` filters on ``knowledge_version_epoch``. If the
    configured value is not the epoch present in the index, every BM25 query
    returns nothing for a reason unrelated to retrieval quality. This is a
    configuration check, not a corpus check, so it is reported separately.
    """
    path = Path(config_path or "config.json")
    if not path.is_file():
        return {"check": "config_epoch", "status": STATUS_BLOCKED, "detail": f"config not found: {path}"}
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"check": "config_epoch", "status": STATUS_BLOCKED, "detail": f"config unreadable: {exc}"}
    configured = str(config.get("knowledge_version_epoch") or "")
    expected = str(manifest.get("corpus_version") or "")
    ok = configured == expected
    return {
        "check": "config_epoch",
        "status": STATUS_OK if ok else STATUS_FAIL,
        "detail": {
            "config_path": str(path),
            "knowledge_version_epoch": configured,
            "corpus_version": expected,
            "verdict": (
                "retrieval filters on the epoch present in the index"
                if ok
                else "retrieval filters on a different epoch; BM25 would return zero rows against this index"
            ),
        },
    }


def run(args: argparse.Namespace) -> int:
    corpus_dir = Path(args.corpus_dir)
    manifest = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    fingerprint = json.loads((corpus_dir / "es_fingerprint.json").read_text(encoding="utf-8"))
    chunks = _load_jsonl(corpus_dir / "chunks.jsonl")
    candidates = _load_jsonl(Path(args.candidates))

    results = [
        check_source_bytes(corpus_dir, manifest),
        check_local_corpus(manifest, chunks),
        check_candidates(fingerprint, candidates),
        check_config_epoch(manifest, args.config),
    ]

    client = _client(args.host)
    try:
        if not bool(client.indices.exists(index=args.index)):
            results.append(
                {"check": "index_identity", "status": STATUS_BLOCKED, "detail": f"index {args.index!r} does not exist"}
            )
            results.append(
                {"check": "index_content", "status": STATUS_BLOCKED, "detail": f"index {args.index!r} does not exist"}
            )
            results.append(
                {"check": "trust_state", "status": STATUS_BLOCKED, "detail": f"index {args.index!r} does not exist"}
            )
        else:
            results.append(check_index_identity(client, args.index, fingerprint))
            results.append(check_index_content(client, args.index, manifest, candidates))
            results.append(check_trust_state(client, args.index))
    except Exception as exc:  # noqa: BLE001 - an unreachable index is BLOCKED, not a pass
        for name in ("index_identity", "index_content", "trust_state"):
            results.append({"check": name, "status": STATUS_BLOCKED, "detail": f"{type(exc).__name__}: {exc}"})

    payload = {
        "schema_version": "real-corpus-binding-verification/1",
        "corpus_dir": str(corpus_dir),
        "index": args.index,
        "host": args.host,
        "corpus_version": manifest.get("corpus_version"),
        "checks": results,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))

    if any(result["status"] == STATUS_BLOCKED for result in results):
        return 2
    if any(result["status"] == STATUS_FAIL for result in results):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus-dir", default="corpus/real_v1")
    parser.add_argument("--candidates", default="tests/evaluation/golden_set_v1/candidates_v1.jsonl")
    parser.add_argument("--index", default="cosmetics_docs")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--host", default="http://127.0.0.1:9200")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
