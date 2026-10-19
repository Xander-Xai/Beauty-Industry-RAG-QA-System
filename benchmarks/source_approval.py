"""Source-approval verification for a retrieval-benchmark run.

The publication gate in :mod:`benchmarks.retrieval_benchmark` used to be a
conjunction of three facts — something executed, the golden set is attributable,
and the retriever was not a fixture — and none of them said anything about where
the indexed text came from. A run could therefore retrieve happily out of an
index whose documents carried ``UNTRUSTED`` provenance, or out of one that mixed
an approved regulation with an unreviewed catalogue, and still record
``results_are_benchmark: true``.

This module closes that gap. It answers one question about **the index the run
actually queried**: is every document in it attributable to a source a named
reviewer approved, for the exact bytes that were reviewed?

Four properties make the answer trustworthy rather than decorative:

1. **Whole-index, not top-N.** It pages through *every* document and refuses to
   certify an index it did not read in full. Checking the ten rows a query
   happened to return would be a sample of the answers, not of the corpus.
2. **Cross-checked against the ledger, not against the index's own claim.** A
   document asserting ``approval_status: APPROVED`` proves nothing. The stored
   hash is compared to the approval ledger *and* to the bytes of the source file
   on disk, so a stale approval, a rewritten source and a self-certified index
   all fail.
3. **Self-consistency of the stored axes.** ``trust_class`` is recomputed from
   ``(source_trust, approval_status)`` and compared, so a payload cannot claim
   ``APPROVED_EXTERNAL`` while its own decision axes say otherwise.
4. **Fail-closed.** An unreachable cluster, a missing ledger, a partial read or a
   timeout yields ``verified=False`` with a reason. There is no branch that
   degrades to "assume it was fine" — that branch is the defect being fixed.

The verification is *evidence*, not a licence: it says the corpus passed review.
It does not say the content is legally current, safe against prompt injection, or
fit for production deployment. Those are separate decisions with separate
evidence, and :mod:`offline.source_trust` says so in its own contract.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from offline.source_trust import (
    ACTIVATABLE_TRUST_CLASSES,
    APPROVAL_APPROVED,
    APPROVAL_NOT_REQUIRED,
    APPROVAL_REJECTED,
    CLASS_APPROVED_EXTERNAL,
    PROVENANCE_FIELDS,
    PROVENANCE_SCHEMA_VERSION,
    TRUST_MANAGED_INTERNAL,
    SourceTrustError,
    effective_trust_class,
    file_content_hash,
)

#: Fields read from every document. The identity fields are needed to prove the
#: whole index was covered and to attribute each verdict to a gold chunk.
SOURCE_FIELDS = [
    "chunk_id",
    "doc_id",
    "doc_version_epoch",
    "provenance.source_id",
    "provenance.source_trust",
    "provenance.approval_status",
    "provenance.approval_actor",
    "provenance.approval_decided_at",
    "provenance.approval_note",
    "provenance.approved_content_hash",
    "provenance.trust_class",
    "provenance.provenance_schema_version",
    "provenance.staging_unapproved",
]

#: Documents per page when walking an index. Large enough that a 212-document
#: corpus is one round trip, small enough not to blow past ``max_result_window``
#: on an index with deep paging.
PAGE_SIZE = 500

#: Bound on reported issues. The summary is a report, not a dump: a wholesale
#: failure would otherwise write one entry per document into the artifact.
MAX_REPORTED_ISSUES = 50


@dataclass(frozen=True)
class SourceApprovalIssue:
    """One blocking reason, attributed to the source or index that caused it."""

    code: str
    detail: str
    source_id: str = ""
    document_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "source_id": self.source_id,
            "document_count": self.document_count,
        }


@dataclass
class _IssueLog:
    """Bounded issue collector that still counts everything it drops."""

    issues: list[SourceApprovalIssue] = field(default_factory=list)
    total: int = 0
    by_code: dict[str, int] = field(default_factory=dict)

    def add(self, code: str, detail: str, *, source_id: str = "", document_count: int = 0) -> None:
        self.total += 1
        self.by_code[code] = self.by_code.get(code, 0) + 1
        if len(self.issues) < MAX_REPORTED_ISSUES:
            self.issues.append(
                SourceApprovalIssue(code=code, detail=detail, source_id=source_id, document_count=document_count)
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "issue_count": self.total,
            "by_code": dict(sorted(self.by_code.items())),
            "issues": [issue.as_dict() for issue in self.issues],
            "truncated": self.total > len(self.issues),
        }


@dataclass(frozen=True)
class SourceApprovalReport:
    """The verdict on one index, with the evidence that produced it.

    ``verified`` is a conjunction and nothing else: it is true only when
    :func:`verify_index_source_approval` found a reachable index, read all of
    it, found a single expected epoch, and attributed every document to an
    approved source whose ledger decision still matches the bytes on disk.
    """

    verified: bool
    index_name: str
    expected_epoch: str
    observed_epochs: tuple[str, ...] = ()
    document_count: int = 0
    documents_verified: int = 0
    source_count: int = 0
    trust_class_counts: dict[str, int] = field(default_factory=dict)
    approval_status_counts: dict[str, int] = field(default_factory=dict)
    sources: tuple[dict[str, Any], ...] = ()
    ledger_path: str = ""
    ledger_reachable: bool = False
    staging_unapproved: bool = False
    content_sha256: str = ""
    corpus_sha256: str = ""
    gold_chunk_total: int = 0
    gold_chunk_resolved: int = 0
    gold_chunk_unresolved: tuple[str, ...] = ()
    gold_chunks_from_unapproved_sources: tuple[str, ...] = ()
    issues: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "index_name": self.index_name,
            "expected_epoch": self.expected_epoch,
            "observed_epochs": list(self.observed_epochs),
            "document_count": self.document_count,
            "documents_verified": self.documents_verified,
            "source_count": self.source_count,
            "trust_class_counts": dict(sorted(self.trust_class_counts.items())),
            "approval_status_counts": dict(sorted(self.approval_status_counts.items())),
            "sources": list(self.sources),
            "ledger_path": self.ledger_path,
            "ledger_reachable": self.ledger_reachable,
            "staging_unapproved": self.staging_unapproved,
            "content_sha256": self.content_sha256,
            "corpus_sha256": self.corpus_sha256,
            "gold_chunk_total": self.gold_chunk_total,
            "gold_chunk_resolved": self.gold_chunk_resolved,
            "gold_chunk_unresolved": list(self.gold_chunk_unresolved),
            "gold_chunks_from_unapproved_sources": list(self.gold_chunks_from_unapproved_sources),
            "issues": dict(self.issues),
        }


def unverified_report(
    index_name: str,
    expected_epoch: str,
    *,
    code: str,
    detail: str,
    **overrides: Any,
) -> SourceApprovalReport:
    """Build a fail-closed report carrying exactly one blocking reason.

    Used for every path where the verifier could not establish the fact — an
    unreachable cluster, an absent ledger, a page that would not load. Each of
    those is a *different* operational problem, so each names itself rather than
    collapsing into one opaque "verification failed".
    """
    log = _IssueLog()
    log.add(code, detail)
    fields: dict[str, Any] = {
        "verified": False,
        "index_name": index_name,
        "expected_epoch": expected_epoch,
        "issues": log.as_dict(),
    }
    fields.update(overrides)
    return SourceApprovalReport(**fields)


def resolve_approval_store(config: dict | None = None) -> str:
    """Return the approval-ledger path from the same config the indexer used.

    Reading the path from configuration rather than accepting it as an argument
    is deliberate: the ledger a run verifies against must be the ledger the
    ingestion path consults, or the check is against a file nothing enforces.
    """
    if config is None:
        from common.config import get_config_dict

        config = get_config_dict() or {}
    trust_rules = (config or {}).get("source_trust", {}) or {}
    return str(trust_rules.get("approval_store_path", "") or "")


def _resolve_configured_index(config: dict | None = None) -> tuple[str, str]:
    """Return ``(index_name, epoch)`` exactly as the BM25 retriever reads them.

    ``retrieval.bm25_retriever`` resolves its index from
    ``config["elasticsearch"]["index"]`` and its epoch filter from
    ``config["knowledge_version_epoch"]``. Verifying any *other* index would be
    verifying a corpus this run never queried.
    """
    if config is None:
        from common.config import get_config_dict

        config = get_config_dict() or {}
    elastic = (config or {}).get("elasticsearch", {}) or {}
    return str(elastic.get("index", "") or ""), str((config or {}).get("knowledge_version_epoch", "") or "")


def _load_all_documents(client: Any, index_name: str, log: _IssueLog) -> tuple[list[dict], int]:
    """Page through the entire index and return every document's fields.

    Uses ``search_after`` on ``chunk_id`` — the same stable, uniquely-valued
    keyword field the ES writer paginates on — so the walk terminates and covers
    each document exactly once. Returns ``(documents, declared_count)``; the
    caller compares the two so a truncated walk cannot certify a partial index.
    """
    try:
        declared = int(client.count(index=index_name).get("count", 0))
    except Exception as exc:  # noqa: BLE001 - a probe reports, it does not raise
        log.add("index_count_failed", f"count() failed: {type(exc).__name__}: {exc}")
        return [], -1

    documents: list[dict] = []
    after: Any = None
    while True:
        body: dict[str, Any] = {
            "size": PAGE_SIZE,
            "query": {"match_all": {}},
            "_source": SOURCE_FIELDS,
            "sort": [{"chunk_id": "asc"}],
        }
        if after is not None:
            body["search_after"] = after
        try:
            response = client.search(index=index_name, body=body)
        except Exception as exc:  # noqa: BLE001
            log.add(
                "index_page_failed", f"search() failed after {len(documents)} documents: {type(exc).__name__}: {exc}"
            )
            return documents, declared
        page = (response.get("hits", {}) or {}).get("hits", []) or []
        if not page:
            break
        for hit in page:
            documents.append(hit.get("_source") or {})
        after = page[-1].get("sort")
        if not after:
            break
        if len(documents) >= declared and declared >= 0:
            # Every declared document has been read; one more page would only
            # confirm the end of the index.
            break
    return documents, declared


def _check_document_provenance(document: dict, log: _IssueLog) -> dict[str, Any] | None:
    """Validate one document's persisted provenance, or record why it is unusable.

    Returns the provenance mapping when it is complete and self-consistent, and
    ``None`` otherwise. Every rejection is a distinct code so a reader can tell
    "no provenance at all" from "provenance present but the trust class was
    laundered".
    """
    chunk_id = str(document.get("chunk_id") or "")
    provenance = document.get("provenance")
    if not isinstance(provenance, dict) or not provenance:
        log.add("provenance_missing", f"document {chunk_id!r} carries no provenance", document_count=1)
        return None

    missing = [name for name in PROVENANCE_FIELDS if name not in provenance]
    if missing:
        log.add(
            "provenance_incomplete",
            f"document {chunk_id!r} provenance is missing fields {missing}",
            document_count=1,
        )
        return None

    version = provenance.get("provenance_schema_version")
    if version != PROVENANCE_SCHEMA_VERSION:
        log.add(
            "provenance_schema_mismatch",
            f"document {chunk_id!r} declares provenance schema {version!r}, "
            f"this build reads {PROVENANCE_SCHEMA_VERSION!r}",
            document_count=1,
        )
        return None

    if provenance.get("staging_unapproved") is True:
        log.add(
            "staging_unapproved",
            f"document {chunk_id!r} is marked staging_unapproved; a staged index is never an activatable snapshot",
            source_id=str(provenance.get("source_id") or ""),
            document_count=1,
        )
        return None

    trust = str(provenance.get("source_trust") or "")
    approval = str(provenance.get("approval_status") or "")
    stored_class = str(provenance.get("trust_class") or "")

    # An absent trust_class is a distinct failure from a wrong one: the former
    # means the payload predates the field, the latter means it was written
    # inconsistently. Both are untrustworthy, but they need different fixes.
    if not stored_class:
        log.add(
            "trust_class_missing",
            f"document {chunk_id!r} has no provenance.trust_class, so its activation state is undeclared",
            source_id=str(provenance.get("source_id") or ""),
            document_count=1,
        )
        return None

    try:
        derived_class = effective_trust_class(trust, approval)
    except SourceTrustError as exc:
        log.add(
            "trust_class_undecidable",
            f"document {chunk_id!r} cannot be classified from (source_trust={trust!r}, "
            f"approval_status={approval!r}): {exc}",
            source_id=str(provenance.get("source_id") or ""),
            document_count=1,
        )
        return None

    if stored_class != derived_class:
        log.add(
            "trust_class_inconsistent",
            f"document {chunk_id!r} stores trust_class={stored_class!r} but its decision axes "
            f"(source_trust={trust!r}, approval_status={approval!r}) derive {derived_class!r}",
            source_id=str(provenance.get("source_id") or ""),
            document_count=1,
        )
        return None

    if derived_class not in ACTIVATABLE_TRUST_CLASSES:
        log.add(
            "source_not_activatable",
            f"document {chunk_id!r} derives trust_class={derived_class!r}, which is not an activatable class",
            source_id=str(provenance.get("source_id") or ""),
            document_count=1,
        )
        return None

    if derived_class == CLASS_APPROVED_EXTERNAL:
        # An approval with no actor is indistinguishable from an approval nobody
        # made, which is exactly what offline.source_trust refuses to store.
        if not str(provenance.get("approval_actor") or "").strip():
            log.add(
                "approval_actor_missing",
                f"document {chunk_id!r} is APPROVED_EXTERNAL with no approval_actor",
                source_id=str(provenance.get("source_id") or ""),
                document_count=1,
            )
            return None
        if not str(provenance.get("approved_content_hash") or "").strip():
            log.add(
                "approved_content_hash_missing",
                f"document {chunk_id!r} is APPROVED_EXTERNAL with no approved_content_hash to bind the decision to",
                source_id=str(provenance.get("source_id") or ""),
                document_count=1,
            )
            return None

    return provenance


def _load_ledger_decisions(ledger_path: str) -> tuple[dict[str, dict[str, Any]], bool]:
    """Read every decision out of the approval ledger, or report it unreadable.

    Reads through the ledger's own connection rather than importing
    :class:`offline.source_trust.TrustRegistry`, so verification cannot be
    confused with the write path it is meant to check: a registry instance also
    *creates* the schema it reads.
    """
    if not ledger_path:
        return {}, False
    path = Path(ledger_path)
    if not path.is_file():
        return {}, False
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT source_id, content_hash, approval_status, decided_by, decided_at, revision "
                "FROM source_trust_decisions"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return {}, False
    return {str(row["source_id"]): dict(row) for row in rows}, True


def _verify_source_against_ledger(
    source_id: str,
    provenance: dict[str, Any],
    decisions: dict[str, dict[str, Any]],
    repo_root: Path,
    log: _IssueLog,
) -> dict[str, Any]:
    """Confirm one source's approval still matches the ledger *and* the bytes.

    Three independent hashes must agree: the hash the index claims was approved,
    the hash the ledger recorded at decision time, and the hash of the source
    file as it exists on disk right now. A source edited after approval fails on
    the third even though the first two still match, which is the entire reason
    approvals are stored hash-bound.
    """
    indexed_hash = str(provenance.get("approved_content_hash") or "")
    indexed_class = str(provenance.get("trust_class") or "")
    indexed_status = str(provenance.get("approval_status") or "")
    decision = decisions.get(source_id)

    if indexed_class not in ACTIVATABLE_TRUST_CLASSES:
        # The index does not hold activatable content for this source, so the
        # ledger cannot change that: an approval recorded after the build is not
        # reflected in what was written. Reporting the ledger verdict here would
        # read as "approved" for an index that would be refused at activation.
        return {
            "source_id": source_id,
            "verdict": "not_activatable_in_index",
            "indexed_trust_class": indexed_class,
            "indexed_approval_status": indexed_status,
            "ledger_approval_status": str((decision or {}).get("approval_status") or ""),
            "index_approval_predates_ledger": bool(decision)
            and indexed_status != str(decision.get("approval_status") or ""),
        }

    if provenance.get("source_trust") == TRUST_MANAGED_INTERNAL:
        # Managed content inherits the managed contract and has no approval to
        # re-verify — but the *only* evidence for that claim is this very index
        # document. An index cannot attest to itself, so a MANAGED_INTERNAL
        # declaration in Elasticsearch is unverifiable by construction: there is
        # no content hash to re-hash and no ledger row that could disagree.
        # That is a different failure from a missing ledger row (which an
        # operator could fix by recording a decision), and reporting it as one
        # would send them to record a decision the contract then ignores. The
        # real fix is to rebuild the index through the trust contract, which
        # declares these external documents UNTRUSTED and attributes them to a
        # source file that *can* be approved.
        log.add(
            "source_managed_internal_not_reviewable",
            f"{source_id!r} declares MANAGED_INTERNAL, whose only evidence is this index document "
            "itself; an index cannot attest to its own provenance, so the claim cannot be "
            "cross-checked against reviewed bytes",
            source_id=source_id,
        )
        return {"source_id": source_id, "verdict": "managed_internal_unverifiable"}

    if decision is None:
        log.add(
            "source_not_in_ledger",
            f"{source_id!r} is indexed as {provenance.get('trust_class')!r} but has no decision in the approval ledger",
            source_id=source_id,
        )
        return {"source_id": source_id, "verdict": "not_in_ledger"}

    ledger_hash = str(decision.get("content_hash") or "")
    approval_status = str(decision.get("approval_status") or "")

    if approval_status == APPROVAL_REJECTED:
        log.add(
            "source_approval_revoked",
            f"{source_id!r} was rejected in review but is still indexed",
            source_id=source_id,
        )
        return {"source_id": source_id, "verdict": "rejected", "approval_status": approval_status}
    if approval_status != APPROVAL_APPROVED and approval_status != APPROVAL_NOT_REQUIRED:
        log.add(
            "source_not_approved",
            f"{source_id!r} resolves to approval_status={approval_status!r}, which does not admit activation",
            source_id=source_id,
        )
        return {"source_id": source_id, "verdict": approval_status.lower(), "approval_status": approval_status}

    if not ledger_hash:
        log.add(
            "ledger_hash_missing",
            f"{source_id!r} is approved in the ledger but no content hash was recorded with the decision",
            source_id=source_id,
        )
        return {"source_id": source_id, "verdict": "ledger_hash_missing"}

    if indexed_hash != ledger_hash:
        log.add(
            "indexed_hash_disagrees_with_ledger",
            f"{source_id!r} is indexed with approved_content_hash={indexed_hash[:16]}… but the ledger recorded "
            f"{ledger_hash[:16]}… for that approval",
            source_id=source_id,
        )
        return {
            "source_id": source_id,
            "verdict": "hash_mismatch",
            "indexed_hash": indexed_hash,
            "ledger_hash": ledger_hash,
        }

    # The decisive check: re-hash the bytes a reviewer could have opened.
    on_disk = file_content_hash(repo_root / source_id)
    if not on_disk:
        log.add(
            "source_file_unreadable",
            f"{source_id!r} is indexed and approved but its bytes could not be read at {repo_root / source_id!r}",
            source_id=source_id,
        )
        return {"source_id": source_id, "verdict": "source_unreadable"}
    if on_disk != ledger_hash:
        log.add(
            "approved_bytes_changed_since_approval",
            f"{source_id!r} no longer hashes to the bytes that were approved: on disk {on_disk[:16]}…, "
            f"approved {ledger_hash[:16]}…",
            source_id=source_id,
        )
        return {
            "source_id": source_id,
            "verdict": "stale_approval",
            "on_disk_hash": on_disk,
            "ledger_hash": ledger_hash,
        }

    indexed_actor = str(provenance.get("approval_actor") or "")
    ledger_actor = str(decision.get("decided_by") or "")
    if indexed_actor != ledger_actor:
        log.add(
            "approval_actor_disagrees_with_ledger",
            f"{source_id!r} is indexed with approval_actor={indexed_actor!r} but the ledger attributes the "
            f"decision to {ledger_actor!r}",
            source_id=source_id,
        )
        return {"source_id": source_id, "verdict": "actor_mismatch"}

    return {
        "source_id": source_id,
        "verdict": "approved",
        "trust_class": provenance.get("trust_class"),
        "approval_status": approval_status,
        "approved_content_hash": ledger_hash,
        "approval_actor": ledger_actor,
        "approval_decided_at": str(decision.get("decided_at") or ""),
        "ledger_revision": decision.get("revision"),
    }


def verify_index_source_approval(
    client: Any,
    *,
    index_name: str,
    expected_epoch: str,
    gold_chunk_ids: frozenset[str] | set[str] = frozenset(),
    ledger_path: str = "",
    repo_root: Path | None = None,
    staging_unapproved: bool = False,
) -> SourceApprovalReport:
    """Verify that every document in ``index_name`` is an approved source.

    ``client`` is any object with the Elasticsearch ``count``/``search`` shape,
    so the walk is testable without a cluster. Returns a
    :class:`SourceApprovalReport` whose ``verified`` is true only when all of
    the following held:

    * the index exists and every one of its documents was read;
    * all documents carry one ``doc_version_epoch``, equal to ``expected_epoch``;
    * every document's provenance is complete, current-schema, self-consistent,
      activatable, and — for external content — attributed to a named actor and a
      recorded content hash;
    * each source's indexed hash equals the ledger's hash *and* the hash of the
      source file on disk;
    * ``staging_unapproved`` was not set when the index was built;
    * every gold chunk the run scored resolves in the index and comes from a
      source that passed the same checks.
    """
    repo_root = repo_root or Path(__file__).resolve().parents[1]
    log = _IssueLog()

    if not index_name:
        return unverified_report(
            "",
            expected_epoch,
            code="index_not_configured",
            detail="no Elasticsearch index is configured, so there is no corpus to verify",
        )

    if staging_unapproved:
        log.add(
            "index_built_with_staging_unapproved",
            f"index {index_name!r} was built with --staging-unapproved; a staged index is never "
            "an activatable snapshot and may not back a benchmark result",
        )

    try:
        exists = bool(client.indices.exists(index=index_name))
    except Exception as exc:  # noqa: BLE001
        return unverified_report(
            index_name,
            expected_epoch,
            code="index_probe_failed",
            detail=f"could not probe index {index_name!r}: {type(exc).__name__}: {exc}",
        )
    if not exists:
        return unverified_report(
            index_name,
            expected_epoch,
            code="index_missing",
            detail=f"index {index_name!r} does not exist",
        )

    documents, declared_count = _load_all_documents(client, index_name, log)
    documents_verified = len(documents)
    if declared_count < 0:
        return SourceApprovalReport(
            verified=False,
            index_name=index_name,
            expected_epoch=expected_epoch,
            documents_verified=documents_verified,
            ledger_path=ledger_path,
            issues=log.as_dict(),
        )
    if documents_verified != declared_count:
        log.add(
            "index_read_incomplete",
            f"read {documents_verified} of {declared_count} documents from {index_name!r}; a partial walk "
            "cannot certify a whole index",
            document_count=declared_count - documents_verified,
        )

    decisions, ledger_reachable = _load_ledger_decisions(ledger_path)
    if not ledger_reachable:
        log.add(
            "approval_ledger_unavailable",
            f"the approval ledger at {ledger_path!r} could not be read, so no indexed approval claim "
            "can be cross-checked against a reviewer decision",
        )

    epochs: set[str] = set()
    trust_class_counts: dict[str, int] = {}
    approval_status_counts: dict[str, int] = {}
    provenance_by_source: dict[str, dict[str, Any]] = {}
    documents_by_source: dict[str, int] = {}
    chunk_id_to_source: dict[str, str] = {}
    usable_documents = 0

    for document in documents:
        epoch = str(document.get("doc_version_epoch") or "")
        if epoch:
            epochs.add(epoch)
        # Register the source from the raw payload before validating the
        # document, so a wholly quarantined source still gets a per-source
        # verdict. Registering only activatable documents would make the report
        # silently omit exactly the sources an operator needs to see named.
        raw_provenance = document.get("provenance")
        if isinstance(raw_provenance, dict):
            raw_source_id = str(raw_provenance.get("source_id") or "")
            if raw_source_id:
                documents_by_source[raw_source_id] = documents_by_source.get(raw_source_id, 0) + 1
                if raw_source_id not in provenance_by_source:
                    provenance_by_source[raw_source_id] = raw_provenance
                chunk_id = str(document.get("chunk_id") or "")
                if chunk_id:
                    chunk_id_to_source[chunk_id] = raw_source_id
        provenance = _check_document_provenance(document, log)
        if provenance is None:
            continue
        usable_documents += 1
        source_id = str(provenance.get("source_id") or "")
        trust_class_counts[provenance["trust_class"]] = trust_class_counts.get(provenance["trust_class"], 0) + 1
        approval_status_counts[provenance["approval_status"]] = (
            approval_status_counts.get(provenance["approval_status"], 0) + 1
        )

    if not expected_epoch:
        log.add(
            "epoch_not_configured",
            "no knowledge_version_epoch is configured, so the index epoch cannot be bound to an "
            "intended corpus version",
        )
    observed_epochs = tuple(sorted(epochs))
    if not epochs:
        log.add("epoch_absent", f"no document in {index_name!r} declares doc_version_epoch")
    elif expected_epoch and epochs != {expected_epoch}:
        log.add(
            "epoch_mismatch",
            f"index {index_name!r} holds epochs {list(observed_epochs)} but the configured "
            f"knowledge_version_epoch is {expected_epoch!r}; the configured retrieval filter would "
            "silently match none of these documents",
        )
    elif len(epochs) > 1:
        log.add(
            "epoch_mixed",
            f"index {index_name!r} mixes epochs {list(observed_epochs)}; a benchmark result must name "
            "one corpus version",
        )

    source_reports: list[dict[str, Any]] = []
    verified_sources: set[str] = set()
    for source_id in sorted(provenance_by_source):
        entry = _verify_source_against_ledger(source_id, provenance_by_source[source_id], decisions, repo_root, log)
        entry["document_count"] = documents_by_source.get(source_id, 0)
        entry["indexed_trust_class"] = provenance_by_source[source_id].get("trust_class")
        entry["indexed_approval_status"] = provenance_by_source[source_id].get("approval_status")
        entry["indexed_approved_content_hash"] = provenance_by_source[source_id].get("approved_content_hash")
        source_reports.append(entry)
        if entry["verdict"] == "approved":
            verified_sources.add(source_id)

    gold_chunks = {str(chunk_id) for chunk_id in gold_chunk_ids if str(chunk_id)}
    unresolved_gold = sorted(chunk_id for chunk_id in gold_chunks if chunk_id not in chunk_id_to_source)
    gold_from_unapproved = sorted(
        chunk_id for chunk_id in gold_chunks if chunk_id_to_source.get(chunk_id, "") not in verified_sources
    )
    for chunk_id in unresolved_gold:
        log.add(
            "gold_chunk_unresolved",
            f"gold chunk {chunk_id!r} does not resolve to any document in {index_name!r}",
        )
    for chunk_id in gold_from_unapproved:
        if chunk_id not in unresolved_gold:
            log.add(
                "gold_chunk_from_unapproved_source",
                f"gold chunk {chunk_id!r} resolves to {chunk_id_to_source[chunk_id]!r}, which did not pass "
                "source-approval verification",
                source_id=chunk_id_to_source.get(chunk_id, ""),
            )

    content_sha256 = ""
    corpus_sha256 = ""
    if not documents:
        log.add("index_empty", f"index {index_name!r} contains no documents")
    else:
        content_sha256 = _index_content_fingerprint(documents)
        corpus_sha256 = content_sha256
    report = SourceApprovalReport(
        verified=False,
        index_name=index_name,
        expected_epoch=expected_epoch,
        observed_epochs=observed_epochs,
        document_count=declared_count,
        documents_verified=documents_verified,
        source_count=len(provenance_by_source),
        trust_class_counts=trust_class_counts,
        approval_status_counts=approval_status_counts,
        sources=tuple(source_reports),
        ledger_path=ledger_path,
        ledger_reachable=ledger_reachable,
        staging_unapproved=bool(staging_unapproved),
        content_sha256=content_sha256,
        corpus_sha256=corpus_sha256,
        gold_chunk_total=len(gold_chunks),
        gold_chunk_resolved=len(gold_chunks) - len(unresolved_gold),
        gold_chunk_unresolved=tuple(unresolved_gold),
        gold_chunks_from_unapproved_sources=tuple(gold_from_unapproved),
        issues=log.as_dict(),
    )
    # `verified` is computed last, from the issue log alone, so no branch of the
    # walk can decide it positively by omission.
    return SourceApprovalReport(**{**report.__dict__, "verified": log.total == 0 and usable_documents > 0})


def _index_content_fingerprint(documents: list[dict]) -> str:
    """Digest the whole index by ``(doc_id, chunk_id, epoch)`` identity.

    Deliberately not the sampled content digest
    :func:`benchmarks.corpus.fingerprint_elasticsearch` produces: that one is
    sampled and exists to compare corpora cheaply, and here it would let two
    different indexes share a fingerprint. This walk already holds every
    document, so hashing all of them costs nothing extra and is exact.
    """
    digest = hashlib.sha256()
    for document in sorted(
        documents,
        key=lambda item: (str(item.get("doc_id") or ""), str(item.get("chunk_id") or "")),
    ):
        digest.update(
            "\x1f".join(
                (
                    str(document.get("doc_id") or ""),
                    str(document.get("chunk_id") or ""),
                    str(document.get("doc_version_epoch") or ""),
                )
            ).encode("utf-8")
        )
        digest.update(b"\x1e")
    return digest.hexdigest()


def gold_chunk_ids_from_rows(rows: list[dict[str, Any]]) -> frozenset[str]:
    """Collect every ``chunk_id`` the scored golden set points at.

    The contract stores ground truth as ``annotations[].chunk_id``. A row with
    none contributes nothing, which is correct: there is no gold chunk to bind
    to an approved source, and the dataset-contract gate is what reports that
    separately.
    """
    chunk_ids: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for annotation in row.get("annotations") or []:
            if isinstance(annotation, dict):
                chunk_id = annotation.get("chunk_id")
                if chunk_id not in (None, ""):
                    chunk_ids.add(str(chunk_id))
    return frozenset(chunk_ids)


def gather_source_approval_evidence(
    *,
    gold_rows: list[dict[str, Any]] | None = None,
    config: dict | None = None,
    client: Any | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Collect the source-approval evidence for a run, never raising.

    Mirrors :func:`benchmarks.retrieval_benchmark.corpus_evidence_for`: an
    unreachable store is an expected state, not a crash. The difference is that
    an unreachable store yields ``verified: False`` rather than a soft
    ``"unavailable"`` verdict — the evidence is absent, so the gate has nothing
    to stand on.
    """
    try:
        if config is None:
            from common.config import get_config_dict

            config = get_config_dict() or {}
        index_name, expected_epoch = _resolve_configured_index(config)
        if not index_name:
            return unverified_report(
                "",
                expected_epoch,
                code="index_not_configured",
                detail="no Elasticsearch index is configured for this run",
            ).as_dict()
        if client is None:
            from retrieval.bm25_retriever import BM25Retriever

            retriever = BM25Retriever()
            if getattr(retriever, "enabled", True) is False or retriever.es_client is None:
                return unverified_report(
                    index_name,
                    expected_epoch,
                    code="elasticsearch_unavailable",
                    detail="the BM25 Elasticsearch client could not connect, so the indexed corpus cannot be inspected",
                ).as_dict()
            client = retriever.es_client
        report = verify_index_source_approval(
            client,
            index_name=index_name,
            expected_epoch=expected_epoch,
            gold_chunk_ids=gold_chunk_ids_from_rows(gold_rows or []),
            ledger_path=resolve_approval_store(config),
            repo_root=repo_root,
        )
        return report.as_dict()
    except Exception as exc:  # noqa: BLE001 - evidence collection must not break the run
        return unverified_report(
            "",
            "",
            code="verification_failed",
            detail=f"{type(exc).__name__}: {exc}",
        ).as_dict()
