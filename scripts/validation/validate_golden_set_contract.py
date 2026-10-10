#!/usr/bin/env python3
"""Validate a golden set against the v2 contract, fail closed.

Exits non-zero when any sample is ``INVALID``, so an evaluation pipeline can gate
on it and cannot silently publish a percentage from a degraded dataset. With
``--resolve-qdrant`` / ``--resolve-es`` it also proves every annotated
``(doc_id, chunk_id)`` exists in the named ``corpus_version`` — the clause that
distinguishes a retrieval *failure* from a chunk/epoch *alignment* failure.

Usage:
    python3 scripts/validation/validate_golden_set_contract.py --dataset tests/evaluation/golden_set.jsonl
    python3 scripts/validation/validate_golden_set_contract.py --dataset tests/evaluation/golden_set_v2.jsonl \
        --resolve-qdrant --resolve-es

Without ``--resolve-*`` the identifier clause is reported as ``UNRESOLVED``
(needs a real index), not silently passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from benchmarks.golden_set_contract import (  # noqa: E402
    DatasetContractError,
    dataset_sha256,
    elasticsearch_resolver,
    load_contract_rows,
    qdrant_resolver,
    report_to_json,
    validate_dataset,
)

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_DATASET_ERROR = 2
EXIT_BLOCKED = 3


def _build_resolver(args: argparse.Namespace, corpus_version: str):
    if not args.resolve_qdrant and not args.resolve_es:
        return None
    from common.config import get_config_dict

    config = get_config_dict()
    resolvers = []
    if args.resolve_qdrant:
        from qdrant_client import QdrantClient

        qdrant = config.get("qdrant", {})
        collection = (config.get("embedding", {}).get("text", {}) or {}).get("collection") or "rag_text_768"
        client = QdrantClient(host=qdrant.get("host", "qdrant"), port=qdrant.get("port", 6333), timeout=10)
        try:
            client.get_collection(collection)
        except Exception as exc:  # noqa: BLE001
            print(f"BLOCKED: Qdrant collection {collection} not reachable: {exc}", file=sys.stderr)
            raise SystemExit(EXIT_BLOCKED) from exc
        resolvers.append(qdrant_resolver(client, collection))
    if args.resolve_es:
        import os

        from elasticsearch import Elasticsearch

        es = config.get("elasticsearch", {})
        username = os.environ.get("ELASTICSEARCH_USERNAME") or es.get("username", "")
        password = os.environ.get("ELASTICSEARCH_PASSWORD") or es.get("password", "")
        kwargs = {"hosts": [es.get("host", "http://localhost:9200")]}
        if username and password:
            kwargs["basic_auth"] = (username, password)
        clients = Elasticsearch(**kwargs)
        index = es.get("index", "cosmetics_docs")
        if not clients.indices.exists(index=index):
            print(f"BLOCKED: Elasticsearch index {index} does not exist", file=sys.stderr)
            raise SystemExit(EXIT_BLOCKED)
        resolvers.append(elasticsearch_resolver(clients, index))

    def _any(doc_id: str, chunk_id: str, version: str) -> bool:
        return any(resolve(doc_id, chunk_id, version) for resolve in resolvers)

    return _any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a golden set against the v2 contract")
    parser.add_argument("--dataset", default="tests/evaluation/golden_set.jsonl")
    parser.add_argument("--min-valid-fraction", type=float, default=1.0)
    parser.add_argument("--resolve-qdrant", action="store_true")
    parser.add_argument("--resolve-es", action="store_true")
    parser.add_argument("--json", action="store_true", help="emit the full report as JSON")
    args = parser.parse_args(argv)

    path = Path(args.dataset)
    if not path.is_file():
        print(f"dataset not found: {path}", file=sys.stderr)
        return EXIT_DATASET_ERROR

    try:
        rows = load_contract_rows(path)
    except DatasetContractError as exc:
        print(f"dataset error: {exc}", file=sys.stderr)
        return EXIT_DATASET_ERROR

    resolver = _build_resolver(args, "")
    resolution = (
        "identifier resolution: ON" if resolver else "identifier resolution: OFF (pass --resolve-qdrant/--resolve-es)"
    )
    report = validate_dataset(rows, resolver=resolver, min_valid_fraction=args.min_valid_fraction)

    if args.json:
        print(report_to_json(report))
    else:
        print(f"dataset: {path}")
        print(f"sha256:  {dataset_sha256(path)}")
        print(f"contract: {report.contract_version}")
        print(f"{resolution}")
        print(f"total:   {report.total}")
        print(f"valid:   {report.valid_count}")
        print(f"invalid: {report.invalid_count}")
        print(f"coverage: {json.dumps(report.coverage, ensure_ascii=False)}")
        if report.invalid_reasons():
            print(f"invalid reasons: {json.dumps(report.invalid_reasons(), ensure_ascii=False)}")

    if report.invalid_count:
        preview = "; ".join(
            f"{verdict.sample_id}: {','.join(verdict.reasons)}"
            for verdict in report.verdicts
            if verdict.status == "INVALID"
        )
        print(
            f"\n{report.invalid_count}/{report.total} samples are INVALID under {report.contract_version}. "
            "This dataset is not attributable and must not be scored. First samples -> "
            f"{preview[:600]}",
            file=sys.stderr,
        )
        return EXIT_INVALID

    if report.valid_fraction < args.min_valid_fraction:
        print(
            f"valid fraction {report.valid_fraction:.3f} < required {args.min_valid_fraction:.3f}",
            file=sys.stderr,
        )
        return EXIT_INVALID

    print(f"\nOK: all {report.valid_count} samples satisfy {report.contract_version}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
