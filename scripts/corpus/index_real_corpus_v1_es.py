#!/usr/bin/env python3
"""Index the committed real corpus V1 into Elasticsearch (BM25 path only).

This script indexes the deterministic chunk view produced by
``scripts/corpus/build_real_corpus_v1.py`` into Elasticsearch using the
repository's own :class:`offline.elasticsearch_writer.ElasticsearchWriter`.

Safety contract
---------------
The script used to call ``ensure_index(recreate=True, confirm=True)``
unconditionally, so every invocation destroyed whatever ``cosmetics_docs``
already contained. That is unacceptable for a production index name. The
current contract is fail-closed:

* **No destructive default.** A missing index is created. An existing, non-empty
  index is never deleted unless the operator passes both ``--recreate`` and
  ``--confirm-index <exact index name>``.
* **Explicit append.** Adding a second epoch to an existing index requires
  ``--append-epoch``; an epoch already present in the index is refused because
  a partial upsert would leave stale documents behind.
* **Profile isolation.** ``--profile production`` only accepts the configured
  production index name. ``--profile experiment`` only accepts an index whose
  name starts with ``cosmetics_docs_exp_``, and never writes the committed
  ``es_fingerprint.json`` unless an explicit ``--out`` is supplied.
* **Preflight evidence.** ``--check-only`` inspects the index name, epoch
  buckets, document count, trust-class counts and content fingerprint and exits
  without writing anything. A conflict is reported with those values, not with
  a generic error.
* **Source trust is not bypassed.** Every chunk is declared ``UNTRUSTED``
  (external official document, not managed internal content) and resolved
  through the existing :class:`offline.source_trust.TrustRegistry`. A production
  index requires an explicit approval decision bound to the reviewed bytes. An
  experiment index may stage unapproved sources only with
  ``--staging-unapproved``, and records that fact in the fingerprint payload.

Usage::

    # read-only preflight
    ELASTICSEARCH_HOST=http://127.0.0.1:9200 \
        python3 scripts/corpus/index_real_corpus_v1_es.py --check-only

    # build an isolated experiment index from unapproved (staged) sources
    ELASTICSEARCH_HOST=http://127.0.0.1:9200 \
        python3 scripts/corpus/index_real_corpus_v1_es.py \
        --profile experiment --index cosmetics_docs_exp_v1 \
        --staging-unapproved --out artifacts/corpus/cosmetics_docs_exp_v1.json

    # rebuild the production index after a named reviewer approved the sources
    ELASTICSEARCH_HOST=http://127.0.0.1:9200 \
        python3 scripts/corpus/index_real_corpus_v1_es.py \
        --profile production --recreate --confirm-index cosmetics_docs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.corpus import fingerprint_elasticsearch  # noqa: E402
from offline.elasticsearch_writer import ElasticsearchWriter  # noqa: E402
from offline.source_trust import (  # noqa: E402
    TRUST_UNTRUSTED,
    SourceTrustRecord,
    TrustRegistry,
    effective_trust_class,
)

DEFAULT_INDEX = "cosmetics_docs"
EXPERIMENT_INDEX_PREFIX = "cosmetics_docs_exp_"
PROFILE_PRODUCTION = "production"
PROFILE_EXPERIMENT = "experiment"
PROFILES = (PROFILE_PRODUCTION, PROFILE_EXPERIMENT)

#: The fingerprint committed for the production corpus. An experiment run must
#: never overwrite it: that would make an isolated staging index look like the
#: evidence for the production corpus.
COMMITTED_FINGERPRINT = "corpus/real_v1/es_fingerprint.json"

#: External official documents are imported, not produced inside the managed
#: data root, so their provenance claim is UNTRUSTED. A human decision moves them
#: to APPROVED_EXTERNAL; nothing in this script may do that.
DECLARED_SOURCE_TRUST = TRUST_UNTRUSTED


class IndexSafetyError(RuntimeError):
    """Raised when an index write would destroy or mix existing content."""


@dataclass(frozen=True)
class IndexState:
    """Read-only snapshot of one index, used for preflight and conflict reports."""

    index_name: str
    exists: bool
    document_count: int = 0
    epochs: tuple[str, ...] = ()
    trust_classes: dict[str, int] | None = None
    content_sha256: str | None = None
    fingerprint_error: str | None = None

    def as_dict(self) -> dict:
        return {
            "index_name": self.index_name,
            "exists": self.exists,
            "document_count": self.document_count,
            "epochs": list(self.epochs),
            "trust_classes": dict(self.trust_classes or {}),
            "content_sha256": self.content_sha256,
            "fingerprint_error": self.fingerprint_error,
        }


@dataclass(frozen=True)
class IndexPlan:
    """What a run intends to do, decided before any write occurs."""

    action: str
    index_name: str
    profile: str
    reason: str

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "index_name": self.index_name,
            "profile": self.profile,
            "reason": self.reason,
        }


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


def resolve_profile(index_name: str, profile: str) -> str:
    """Validate the profile/index-name boundary and return the profile.

    Production may only touch the configured production index. An experiment
    must use a recognisably isolated name, so an accidental production write is
    not one typo away from an experiment command.
    """
    if profile not in PROFILES:
        raise IndexSafetyError(f"unknown profile {profile!r}; expected one of {PROFILES}")
    if profile == PROFILE_PRODUCTION:
        if index_name != DEFAULT_INDEX:
            raise IndexSafetyError(
                f"profile 'production' only accepts the production index {DEFAULT_INDEX!r}, got {index_name!r}; "
                f"use --profile experiment for an isolated index"
            )
        return profile
    if index_name == DEFAULT_INDEX or not index_name.startswith(EXPERIMENT_INDEX_PREFIX):
        raise IndexSafetyError(
            f"profile 'experiment' requires an index named {EXPERIMENT_INDEX_PREFIX}<suffix> and must not be "
            f"{DEFAULT_INDEX!r}; got {index_name!r}"
        )
    return profile


def inspect_index(client, index_name: str) -> IndexState:
    """Return a read-only snapshot of ``index_name``; never writes or deletes."""
    exists = bool(client.indices.exists(index=index_name))
    if not exists:
        return IndexState(index_name=index_name, exists=False)

    try:
        count = int(client.count(index=index_name).get("count", 0))
    except Exception as exc:  # noqa: BLE001 - a probe must report, not raise
        return IndexState(
            index_name=index_name,
            exists=True,
            fingerprint_error=f"count failed: {type(exc).__name__}: {exc}",
        )

    epochs: tuple[str, ...] = ()
    trust_classes: dict[str, int] = {}
    try:
        response = client.search(
            index=index_name,
            body={
                "size": 0,
                "aggs": {
                    "epochs": {"terms": {"field": "doc_version_epoch", "size": 50}},
                    "trust": {"terms": {"field": "provenance.trust_class", "size": 10}},
                },
            },
        )
        aggregations = response.get("aggregations", {}) or {}
        epochs = tuple(
            str(bucket.get("key")) for bucket in (aggregations.get("epochs", {}) or {}).get("buckets", []) or ()
        )
        trust_classes = {
            str(bucket.get("key")): int(bucket.get("doc_count", 0))
            for bucket in (aggregations.get("trust", {}) or {}).get("buckets", []) or ()
        }
    except Exception as exc:  # noqa: BLE001
        return IndexState(
            index_name=index_name,
            exists=True,
            document_count=count,
            fingerprint_error=f"aggregation failed: {type(exc).__name__}: {exc}",
        )

    fingerprint = fingerprint_elasticsearch(client, index_name)
    return IndexState(
        index_name=index_name,
        exists=True,
        document_count=count,
        epochs=epochs,
        trust_classes=trust_classes,
        content_sha256=fingerprint.content_sha256,
        fingerprint_error=fingerprint.error,
    )


def plan_index_write(
    state: IndexState,
    *,
    profile: str,
    target_epoch: str,
    recreate: bool = False,
    confirm_index: str | None = None,
    append_epoch: bool = False,
) -> IndexPlan:
    """Decide what to do with an existing index, refusing destructive defaults.

    The returned action is one of ``create``, ``index_empty``, ``recreate`` or
    ``append_epoch``. Every refusal names the evidence an operator needs to make
    a different decision.
    """
    resolve_profile(state.index_name, profile)

    if not state.exists:
        return IndexPlan("create", state.index_name, profile, "index does not exist; safe to create")
    if state.document_count == 0:
        return IndexPlan("index_empty", state.index_name, profile, "index exists but contains no documents")

    if recreate:
        if confirm_index != state.index_name:
            raise IndexSafetyError(
                f"refusing to recreate {state.index_name!r}: --recreate requires "
                f"--confirm-index {state.index_name!r} (got {confirm_index!r})"
            )
        return IndexPlan(
            "recreate",
            state.index_name,
            profile,
            f"explicit destructive rebuild confirmed; existing documents={state.document_count}, "
            f"epochs={list(state.epochs)}, content_sha256={state.content_sha256}",
        )

    if append_epoch:
        if target_epoch in state.epochs:
            raise IndexSafetyError(
                f"refusing to append epoch {target_epoch!r} to {state.index_name!r}: it already exists "
                f"(epochs={list(state.epochs)}); a partial upsert would leave stale documents. "
                f"Use --recreate --confirm-index {state.index_name!r} for a clean rebuild."
            )
        return IndexPlan(
            "append_epoch",
            state.index_name,
            profile,
            f"explicit epoch coexistence; existing documents={state.document_count}, epochs={list(state.epochs)}",
        )

    raise IndexSafetyError(
        f"refusing to write into existing non-empty index {state.index_name!r}: "
        f"documents={state.document_count}, epochs={list(state.epochs)}, "
        f"trust_classes={state.trust_classes or {}}, content_sha256={state.content_sha256}. "
        "Nothing was deleted. Choose one explicit action: "
        f"--append-epoch (new epoch), or --recreate --confirm-index {state.index_name!r} (destructive rebuild)."
    )


def load_source_files(sources_dir: Path) -> dict[str, dict]:
    """Return ``source_id -> {path, content_hash, records}`` for each source bundle.

    Approvals are bound to the bytes a reviewer can actually open, so the unit is
    the committed source file, not a single logical document extracted from it.
    That is also the unit the existing ``run_offline.py review-source`` command
    hashes, which keeps this script on the existing approval flow instead of
    inventing a parallel one.
    """
    files: dict[str, dict] = {}
    for path in sorted(sources_dir.glob("*.jsonl")):
        try:
            relative = path.resolve().relative_to(REPO_ROOT).as_posix()
        except ValueError:
            relative = path.name
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        if not records:
            raise IndexSafetyError(f"{path} contains no source records")
        files[relative] = {
            "path": path,
            "content_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
            "records": records,
        }
    if not files:
        raise IndexSafetyError(f"no source files under {sources_dir}")
    return files


def resolve_provenance(
    sources: dict[str, dict],
    chunks: list[dict],
    *,
    registry: TrustRegistry,
) -> tuple[list[dict], dict[str, dict]]:
    """Resolve source provenance through the existing approval ledger.

    The declared trust is always ``UNTRUSTED`` because these are external
    official documents. ``TrustRegistry.resolve`` only returns an activatable
    class when a stored decision matches the exact source-file hash; this
    function never invents an approval.

    Returns ``(chunks_with_provenance, provenance_by_source_id)``.
    """
    known = {str(record["logical_id"]) for source in sources.values() for record in source["records"]}
    unknown = sorted({str(chunk["logical_id"]) for chunk in chunks} - known)
    if unknown:
        raise IndexSafetyError(f"chunks reference logical ids with no source record: {unknown[:5]}")

    provenance_by_source_id: dict[str, dict] = {}
    logical_to_source: dict[str, str] = {}
    for source_id, source in sources.items():
        trust_record = registry.resolve(
            source_id,
            declared_trust=DECLARED_SOURCE_TRUST,
            content_hash=source["content_hash"],
        )
        # Guard against a future refactor that silently promotes the declaration.
        expected = effective_trust_class(trust_record.source_trust, trust_record.approval_status)
        if trust_record.trust_class != expected:
            raise IndexSafetyError(
                f"provenance for {source_id!r} derives {expected!r} but carries {trust_record.trust_class!r}"
            )
        provenance_by_source_id[source_id] = trust_record.to_payload()
        for record in source["records"]:
            logical_to_source[str(record["logical_id"])] = source_id

    resolved = [
        {**chunk, "provenance": provenance_by_source_id[logical_to_source[str(chunk["logical_id"])]]}
        for chunk in chunks
    ]
    return resolved, provenance_by_source_id


def pending_sources(provenance_by_source_id: dict[str, dict]) -> list[str]:
    return sorted(
        source_id for source_id, provenance in provenance_by_source_id.items() if not _is_activatable(provenance)
    )


def _is_activatable(provenance: dict) -> bool:
    try:
        return SourceTrustRecord.from_payload(provenance).is_activatable
    except Exception:  # noqa: BLE001 - unusable provenance is never activatable
        return False


def _build_documents(writer: ElasticsearchWriter, resolved: list[dict]) -> list[dict]:
    documents = []
    for item in resolved:
        documents.append(writer.build_document(_Chunk(item, item["provenance"])))
    return documents


def _fingerprint_payload(
    *,
    index_name: str,
    profile: str,
    info: dict,
    host: str,
    chunks: list[dict],
    provenance_by_source_id: dict[str, dict],
    fingerprint,
    state_before: IndexState,
    plan: IndexPlan,
    staging_unapproved: bool,
    approval_store: str,
) -> dict:
    trust_counts = _trust_counts(provenance_by_source_id)
    pending = pending_sources(provenance_by_source_id)
    return {
        "schema_version": "real-corpus-es-fingerprint/2",
        "index_profile": profile,
        "index_name": index_name,
        "elasticsearch_version": info.get("version", {}).get("number", ""),
        "host": host,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chunks_written": len(chunks),
        "source_file_count": len(provenance_by_source_id),
        "corpus_version": chunks[0]["corpus_version"],
        "corpus_sha256_local": _local_corpus_sha256(chunks),
        "source_trust_policy": {
            "declared_source_trust": DECLARED_SOURCE_TRUST,
            "approval_store": approval_store,
            "activatable_requires": "a named reviewer decision bound to the reviewed document bytes",
            "staging_unapproved": staging_unapproved,
            "trust_class_counts": trust_counts,
            "pending_source_count": len(pending),
            "pending_source_ids": pending[:50],
        },
        "preflight": state_before.as_dict(),
        "plan": plan.as_dict(),
        "fingerprint": fingerprint.as_dict(),
        "note": (
            "BM25-only index built from corpus/real_v1/chunks.jsonl with the production ES writer. "
            "No dense vectors were written: the BGE weights are absent, so the dense collection is "
            "intentionally unbuilt and dense/hybrid retrieval is not claimed. "
            "External official documents are declared UNTRUSTED; an experiment index that staged "
            "unapproved sources is not an activatable snapshot and must not be used as one."
        ),
    }


def _is_committed_fingerprint(path: str) -> bool:
    """True when ``path`` resolves to the committed production fingerprint."""
    try:
        return Path(path).resolve() == (REPO_ROOT / COMMITTED_FINGERPRINT).resolve()
    except OSError:
        return False


def _local_corpus_sha256(chunks: list[dict]) -> str:
    """The same digest ``build_real_corpus_v1.py`` writes to the manifest."""
    digest = hashlib.sha256()
    seen: set[tuple[str, str]] = set()
    for record in chunks:
        key = (str(record["doc_id"]), str(record.get("content_hash", "")))
        seen.add(key)
    for doc_id, content_hash in sorted(seen):
        digest.update(doc_id.encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(content_hash.encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()


def run(
    args: argparse.Namespace,
    *,
    client=None,
    registry: TrustRegistry | None = None,
) -> int:
    """Execute one invocation. ``client``/``registry`` are injectable for tests."""
    if client is None:
        from elasticsearch import Elasticsearch

        client = Elasticsearch(args.host, request_timeout=30)
    info = client.info()
    resolve_profile(args.index, args.profile)

    chunks = load_chunks(Path(args.chunks))
    if args.corpus_version and args.corpus_version != str(chunks[0]["corpus_version"]):
        raise IndexSafetyError(
            f"--corpus-version {args.corpus_version!r} does not match the chunks file "
            f"({chunks[0]['corpus_version']!r}); refusing to bind a fingerprint to the wrong corpus"
        )

    if args.profile == PROFILE_EXPERIMENT and _is_committed_fingerprint(args.out):
        raise IndexSafetyError(
            f"an experiment run must not write the committed production fingerprint {args.out!r}; "
            "pass --out with a path under artifacts/ or another experiment directory"
        )

    state_before = inspect_index(client, args.index)
    plan_error: str | None = None
    try:
        plan = plan_index_write(
            state_before,
            profile=args.profile,
            target_epoch=str(chunks[0]["corpus_version"]),
            recreate=args.recreate,
            confirm_index=args.confirm_index,
            append_epoch=args.append_epoch,
        )
    except IndexSafetyError as exc:
        if not args.check_only:
            raise
        # A preflight must be able to *report* a conflict, so the refusal is
        # recorded as the plan rather than raised before the evidence is printed.
        plan_error = str(exc)
        plan = IndexPlan("refused", args.index, args.profile, plan_error)

    owns_registry = registry is None
    if registry is None:
        registry = TrustRegistry(args.approval_store)
    try:
        sources = load_source_files(Path(args.sources_dir))
        resolved, provenance_by_source_id = resolve_provenance(sources, chunks, registry=registry)
        pending = pending_sources(provenance_by_source_id)

        if args.check_only:
            payload = {
                "schema_version": "real-corpus-es-preflight/1",
                "index_profile": args.profile,
                "index_name": args.index,
                "corpus_version": chunks[0]["corpus_version"],
                "corpus_sha256_local": _local_corpus_sha256(chunks),
                "index_state": state_before.as_dict(),
                "plan": plan.as_dict(),
                "source_trust_policy": {
                    "declared_source_trust": DECLARED_SOURCE_TRUST,
                    "approval_store": args.approval_store,
                    "trust_class_counts": _trust_counts(provenance_by_source_id),
                    "pending_source_count": len(pending),
                    "pending_source_ids": pending[:50],
                },
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            if plan_error:
                print(f"CONFLICT: {plan_error}", file=sys.stderr)
                return 2
            if pending and args.profile == PROFILE_PRODUCTION:
                print(
                    f"BLOCKED: {len(pending)} source(s) are not approved for production activation; "
                    "a named reviewer must approve the reviewed bytes first",
                    file=sys.stderr,
                )
                return 2
            return 0

        if args.profile == PROFILE_PRODUCTION and pending:
            raise IndexSafetyError(
                f"refusing to write the production index {args.index!r}: {len(pending)} source(s) have no "
                f"approval bound to their reviewed bytes (first: {pending[:5]}). Use "
                "--profile experiment --staging-unapproved for a non-activatable staging index, or record "
                "explicit approvals through the existing `review-source` flow."
            )
        if pending and not args.staging_unapproved:
            raise IndexSafetyError(
                f"{len(pending)} source(s) are unapproved; an experiment index may stage them only with "
                "--staging-unapproved (first: {pending[:5]})"
            )

        writer = ElasticsearchWriter(client, args.index)
        if plan.action == "recreate":
            writer.ensure_index(recreate=True, confirm=True)
        else:
            writer.ensure_index()
        documents = _build_documents(writer, resolved)
        writer.upsert_documents(documents)
        client.indices.refresh(index=args.index)

        fingerprint = fingerprint_elasticsearch(client, args.index)
        payload = _fingerprint_payload(
            index_name=args.index,
            profile=args.profile,
            info=info,
            host=args.host,
            chunks=chunks,
            provenance_by_source_id=provenance_by_source_id,
            fingerprint=fingerprint,
            state_before=state_before,
            plan=plan,
            staging_unapproved=bool(pending),
            approval_store=args.approval_store,
        )
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"action: {plan.action}")
        print(f"indexed chunks: {len(documents)}")
        print(f"index: {args.index} ({args.profile})")
        print(f"pending sources: {len(pending)}")
        print(f"content_sha256: {fingerprint.content_sha256}")
        print(f"wrote: {args.out}")
        return 0
    finally:
        if owns_registry and registry is not None:
            registry.close()


def _trust_counts(provenance_by_source_id: dict[str, dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for provenance in provenance_by_source_id.values():
        trust_class = str(provenance.get("trust_class", ""))
        counts[trust_class] = counts.get(trust_class, 0) + 1
    return counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chunks", default="corpus/real_v1/chunks.jsonl")
    parser.add_argument(
        "--sources-dir",
        default="corpus/real_v1/sources",
        help="committed source records that approvals are bound to (default: %(default)s)",
    )
    parser.add_argument(
        "--out",
        default="corpus/real_v1/es_fingerprint.json",
        help="fingerprint output; experiments must override it (default: %(default)s)",
    )
    parser.add_argument("--index", default=os.environ.get("ELASTICSEARCH_INDEX", DEFAULT_INDEX))
    parser.add_argument("--host", default=os.environ.get("ELASTICSEARCH_HOST", "http://127.0.0.1:9200"))
    parser.add_argument("--profile", choices=PROFILES, default=PROFILE_PRODUCTION)
    parser.add_argument("--recreate", action="store_true", help="destructively rebuild the existing index")
    parser.add_argument("--confirm-index", default=None, help="exact index name required with --recreate")
    parser.add_argument("--append-epoch", action="store_true", help="add this epoch alongside existing epochs")
    parser.add_argument("--check-only", action="store_true", help="inspect and plan without writing anything")
    parser.add_argument(
        "--staging-unapproved",
        action="store_true",
        help="experiment profile only: allow UNTRUSTED/PENDING_REVIEW sources to be staged (never activatable)",
    )
    parser.add_argument(
        "--approval-store",
        default=os.environ.get("SOURCE_TRUST_APPROVAL_STORE", "data/offline_source_trust.sqlite3"),
        help="existing TrustRegistry SQLite ledger (default: %(default)s)",
    )
    parser.add_argument("--corpus-version", default=None, help="optional assertion against chunks.jsonl")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except IndexSafetyError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
