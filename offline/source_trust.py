"""Bounded ingestion trust contract: provenance, trust classification, quarantine.

This module is the **one canonical schema** for where an ingested document came
from and whether a human has cleared it. It deliberately has three moving parts
and no fourth:

1. **Provenance claim** (:attr:`SourceTrustRecord.source_trust`) — declared at
   ingest time from managed configuration, never inferred from content:
   ``MANAGED_INTERNAL`` (produced and curated inside the managed data root) or
   ``UNTRUSTED`` (anything imported from outside that curation: a vendor feed, a
   shared drive, a third-party PDF, a user-submitted file).
2. **Explicit human decision** (:attr:`SourceTrustRecord.approval_status`) —
   ``NOT_REQUIRED`` for managed sources, ``PENDING_REVIEW`` for an untrusted
   source nobody has looked at yet, then ``APPROVED`` or ``REJECTED``. There is
   no code path that produces ``APPROVED`` as a side effect of ingestion.
3. **Derived activation class** (:func:`effective_trust_class`) — the two stored
   axes collapse into exactly one bounded value the seal gate reads:
   ``MANAGED_INTERNAL``, ``APPROVED_EXTERNAL`` or ``UNTRUSTED``. Deriving it once,
   in one place, is what keeps three call sites (writer, builder, validator) from
   re-implementing the boolean and drifting apart.

The contract: ``UNTRUSTED`` content may be parsed, chunked, embedded and staged
into a non-activatable staging epoch, but it can never reach an activatable
snapshot until an explicitly attributed reviewer approves *these bytes*. An
approval is bound to the content hash it was made for
(:meth:`TrustRegistry.resolve`), so editing an approved file returns it to
``PENDING_REVIEW`` instead of inheriting a stale approval.

**What this is not.** It is a provenance and quarantine control, i.e. one
defense-in-depth layer that reduces the ingestion-poisoning surface and makes
review decisions durable and attributable. It inspects no document content, it
classifies no document as benign, and it does **not** eliminate prompt
injection: an approved document can still contain adversarial text, and the
prompt-layer retrieved-context boundary is a separate, unchanged control. The
approval decision records that a human accepted responsibility for a source, not
that the source is safe.
"""

from __future__ import annotations

import fnmatch
import hashlib
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ── Canonical vocabulary ──────────────────────────────────────────────────────
#
# The stored values are the two *claims*. The third and fourth names below are
# derived classes only, so a persisted document can never disagree with itself
# about whether it was approved.

#: Content produced and curated inside the managed knowledge-base data root.
TRUST_MANAGED_INTERNAL = "MANAGED_INTERNAL"

#: Content imported from outside that curation. Never activatable on its own.
TRUST_UNTRUSTED = "UNTRUSTED"

#: The complete, finite set of provenance claims. Anything else is rejected.
SOURCE_TRUST_LEVELS = frozenset({TRUST_MANAGED_INTERNAL, TRUST_UNTRUSTED})

#: A managed source needs no review decision; it inherits the managed contract.
APPROVAL_NOT_REQUIRED = "NOT_REQUIRED"

#: The initial state of every untrusted source. Reads as *unreviewed*, never as
#: *approved by omission*.
APPROVAL_PENDING_REVIEW = "PENDING_REVIEW"

#: An explicitly attributed human decision to admit the reviewed bytes.
APPROVAL_APPROVED = "APPROVED"

#: An explicitly attributed human decision to refuse the reviewed bytes.
APPROVAL_REJECTED = "REJECTED"

APPROVAL_STATUSES = frozenset(
    {
        APPROVAL_NOT_REQUIRED,
        APPROVAL_PENDING_REVIEW,
        APPROVAL_APPROVED,
        APPROVAL_REJECTED,
    }
)

#: The statuses a reviewer may set. ``NOT_REQUIRED`` and ``PENDING_REVIEW`` are
#: not decisions, so neither can be requested as one.
DECISION_APPROVAL_STATUSES = frozenset({APPROVAL_APPROVED, APPROVAL_REJECTED})

#: A managed source reviewed into existence would be a lie about the managed
#: contract; an untrusted source is exactly the case approval exists for.
TRUST_APPROVAL_MATRIX = {
    TRUST_MANAGED_INTERNAL: frozenset({APPROVAL_NOT_REQUIRED}),
    TRUST_UNTRUSTED: frozenset({APPROVAL_PENDING_REVIEW, APPROVAL_APPROVED, APPROVAL_REJECTED}),
}

# ── Derived activation classes ───────────────────────────────────────────────

#: Managed content: activatable without any review decision.
CLASS_MANAGED_INTERNAL = TRUST_MANAGED_INTERNAL

#: Imported content that a reviewer explicitly approved for these exact bytes.
CLASS_APPROVED_EXTERNAL = "APPROVED_EXTERNAL"

#: Imported content with no approval, or an explicit rejection. Quarantined.
CLASS_UNTRUSTED = TRUST_UNTRUSTED

#: The only two classes that may appear in an activatable snapshot.
ACTIVATABLE_TRUST_CLASSES = frozenset({CLASS_MANAGED_INTERNAL, CLASS_APPROVED_EXTERNAL})

#: Every class :func:`effective_trust_class` can return.
TRUST_CLASSES = frozenset({CLASS_MANAGED_INTERNAL, CLASS_APPROVED_EXTERNAL, CLASS_UNTRUSTED})

#: Bumped when the persisted provenance payload changes shape, so a stored point
#: can be interpreted without reading this file.
PROVENANCE_SCHEMA_VERSION = "ingestion-source-trust/1"

#: Persisted provenance keys. Read through :class:`SourceTrustRecord` so a caller
#: never hand-rolls a subset of the schema.
SOURCE_ID = "source_id"
SOURCE_TRUST = "source_trust"
APPROVAL_STATUS = "approval_status"
APPROVAL_ACTOR = "approval_actor"
APPROVAL_DECIDED_AT = "approval_decided_at"
APPROVAL_NOTE = "approval_note"
APPROVED_CONTENT_HASH = "approved_content_hash"
TRUST_CLASS = "trust_class"
SCHEMA_VERSION = "provenance_schema_version"

PROVENANCE_FIELDS = (
    SOURCE_ID,
    SOURCE_TRUST,
    APPROVAL_STATUS,
    APPROVAL_ACTOR,
    APPROVAL_DECIDED_AT,
    APPROVAL_NOTE,
    APPROVED_CONTENT_HASH,
    TRUST_CLASS,
    SCHEMA_VERSION,
)

#: Trust levels that require an explicit review decision before activation.
QUARANTINED_TRUST_LEVELS = frozenset({TRUST_UNTRUSTED})

#: Ingestion is an offline CLI/filesystem action, not an authenticated API call,
#: so a writer-side refusal is attributed to a CLI principal. The OS user is
#: carried in the audit metadata by the CLI, matching the seal audit.
CLI_TRUST_ACTOR_ID = "cli_operator"


class SourceTrustError(RuntimeError):
    """Base class for ingestion trust failures. Always fails closed."""


class UnknownTrustLevel(SourceTrustError):
    """The declared trust level is outside the canonical vocabulary."""


class UnknownApprovalStatus(SourceTrustError):
    """The approval status is outside the canonical vocabulary."""


class TrustApprovalNotAllowed(SourceTrustError):
    """The requested trust/approval combination is not a legal state."""


class UnattributedApproval(SourceTrustError):
    """An approval or rejection was requested without an attributable reviewer.

    An approval with no actor is indistinguishable from an approval nobody made,
    which is exactly the silent auto-promotion this contract forbids.
    """


class RejectedSourceError(SourceTrustError):
    """A source a reviewer rejected cannot be indexed at all."""


# ── Normalization and derivation ─────────────────────────────────────────────


def normalize_source_trust(value: str) -> str:
    """Return the canonical trust level, rejecting anything unknown."""
    if value not in SOURCE_TRUST_LEVELS:
        raise UnknownTrustLevel(f"unknown source_trust {value!r}; expected one of {sorted(SOURCE_TRUST_LEVELS)}")
    return value


def normalize_approval_status(value: str) -> str:
    """Return the canonical approval status, rejecting anything unknown."""
    if value not in APPROVAL_STATUSES:
        raise UnknownApprovalStatus(f"unknown approval_status {value!r}; expected one of {sorted(APPROVAL_STATUSES)}")
    return value


def default_approval_status(source_trust: str) -> str:
    """Return the only approval status a freshly declared trust level may hold.

    This is what makes quarantine the default rather than a consequence of
    forgetting something: an untrusted source is born ``PENDING_REVIEW``, and
    nothing in the ingestion path moves it.
    """
    if source_trust == TRUST_MANAGED_INTERNAL:
        return APPROVAL_NOT_REQUIRED
    return APPROVAL_PENDING_REVIEW


def validate_trust_approval(source_trust: str, approval_status: str) -> tuple[str, str]:
    """Return the normalized pair, rejecting an illegal combination."""
    trust = normalize_source_trust(source_trust)
    approval = normalize_approval_status(approval_status)
    if approval not in TRUST_APPROVAL_MATRIX[trust]:
        raise TrustApprovalNotAllowed(
            f"source_trust {trust!r} cannot hold approval_status {approval!r}; "
            f"allowed: {sorted(TRUST_APPROVAL_MATRIX[trust])}"
        )
    return trust, approval


def effective_trust_class(source_trust: str, approval_status: str) -> str:
    """Collapse the two stored axes into one bounded activation class.

    ``APPROVED`` is honored only for an untrusted source — a managed source
    claiming an approval is a contract violation, and this function refuses to
    launder it into ``APPROVED_EXTERNAL``.
    """
    trust, approval = validate_trust_approval(source_trust, approval_status)
    if trust == TRUST_MANAGED_INTERNAL:
        return CLASS_MANAGED_INTERNAL
    if approval == APPROVAL_APPROVED:
        return CLASS_APPROVED_EXTERNAL
    return CLASS_UNTRUSTED


def quarantine_reason(source_trust: str, approval_status: str) -> str:
    """Return one explainable sentence for why a source is not activatable."""
    if source_trust == TRUST_MANAGED_INTERNAL:
        return f"managed internal source (approval_status={approval_status}) requires no review"
    if approval_status == APPROVAL_REJECTED:
        return "a reviewer explicitly rejected this source"
    if approval_status == APPROVAL_PENDING_REVIEW:
        return "imported source has no approval decision yet"
    return f"imported source is held at approval_status={approval_status}"


def persisted_trust_class(provenance) -> str:
    """Return the derived class of persisted provenance, or ``""`` when unusable.

    Total on purpose: this is for *comparison*, so it has to be computable for a
    pre-contract point that carries no provenance at all and must never raise
    while building an idempotency signature. Failing closed on missing
    provenance is the writer gate's and the validator's job, not this function's.
    """
    if not isinstance(provenance, dict) or not provenance:
        return ""
    if provenance.get(SCHEMA_VERSION) != PROVENANCE_SCHEMA_VERSION:
        return ""
    try:
        return effective_trust_class(provenance.get(SOURCE_TRUST), provenance.get(APPROVAL_STATUS))
    except SourceTrustError:
        return ""


def provenance_verdict(provenance, *, label: str) -> tuple[str | None, str]:
    """Return ``(blocking_error_or_None, source_id)`` for persisted provenance.

    The single fail-closed check both the writers and the seal gate call, so
    "is this source activatable" is answered from stored metadata in one place
    rather than re-derived from the stored axes at each call site. The source id
    is returned separately so the seal gate can attribute the refusal to a source
    without parsing an error string.

    ``label`` names the object being checked so a validation report points at the
    point or document that is actually wrong.
    """
    try:
        record = SourceTrustRecord.from_payload(provenance)
    except SourceTrustError as exc:
        return f"{label} has no usable ingestion provenance: {exc}", ""
    if record.is_activatable:
        return None, record.source_id
    return (
        f"{label} is quarantined ({record.source_id}): {record.trust_class} — "
        f"{quarantine_reason(record.source_trust, record.approval_status)}",
        record.source_id,
    )


def provenance_error(provenance, *, label: str) -> str | None:
    """Return why persisted provenance blocks activation, or ``None`` when clean."""
    return provenance_verdict(provenance, label=label)[0]


# ── Canonical persisted record ───────────────────────────────────────────────


@dataclass(frozen=True)
class SourceTrustRecord:
    """The complete persisted provenance of one ingested source.

    This object is the schema. Everything that persists provenance — Qdrant
    text/image payloads, Elasticsearch documents, the source state row and the
    epoch trust manifest — serializes it through :meth:`to_payload` and reads it
    back through :meth:`from_payload`, so there is exactly one field list.
    """

    source_id: str
    source_trust: str = TRUST_MANAGED_INTERNAL
    approval_status: str = APPROVAL_NOT_REQUIRED
    approval_actor: str = ""
    approval_decided_at: str = ""
    approval_note: str = ""
    approved_content_hash: str = ""
    trust_class: str = field(default=CLASS_MANAGED_INTERNAL)
    #: Stored, not merely implied, so a payload read back from a point is
    #: self-describing and a reader never has to know which build wrote it.
    provenance_schema_version: str = PROVENANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.provenance_schema_version != PROVENANCE_SCHEMA_VERSION:
            raise SourceTrustError(
                f"unsupported provenance_schema_version {self.provenance_schema_version!r}; "
                f"this build writes {PROVENANCE_SCHEMA_VERSION!r}"
            )
        trust, approval = validate_trust_approval(self.source_trust, self.approval_status)
        expected_class = effective_trust_class(trust, approval)
        if self.trust_class != expected_class:
            raise TrustApprovalNotAllowed(
                f"trust_class {self.trust_class!r} does not match "
                f"({trust!r}, {approval!r}) which derives {expected_class!r}"
            )
        object.__setattr__(self, "source_trust", trust)
        object.__setattr__(self, "approval_status", approval)

    @property
    def is_activatable(self) -> bool:
        """True when this provenance may appear in an activatable snapshot."""
        return self.trust_class in ACTIVATABLE_TRUST_CLASSES

    @property
    def is_quarantined(self) -> bool:
        """True when this provenance may never be sealed or activated."""
        return not self.is_activatable

    @property
    def may_be_staged(self) -> bool:
        """True when this provenance may be written into a non-activatable epoch.

        Quarantine means *held*, not *deleted*: an unapproved import is parsed and
        staged so a reviewer can inspect it, while a rejection is terminal and is
        not ingested at all.
        """
        return self.approval_status != APPROVAL_REJECTED

    def to_payload(self) -> dict:
        """Return the canonical persisted provenance payload."""
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: dict | None) -> SourceTrustRecord:
        """Rebuild a record from persisted metadata, failing closed on absence.

        A payload with a missing or unknown field raises rather than defaulting,
        so partially-written provenance cannot be read back as managed content.
        """
        if not isinstance(payload, dict) or not payload:
            raise SourceTrustError("point carries no ingestion provenance")
        version = payload.get(SCHEMA_VERSION)
        if version != PROVENANCE_SCHEMA_VERSION:
            raise SourceTrustError(
                f"unsupported {SCHEMA_VERSION} {version!r}; this build writes {PROVENANCE_SCHEMA_VERSION!r}"
            )
        missing = [field_name for field_name in PROVENANCE_FIELDS if field_name not in payload]
        if missing:
            raise SourceTrustError(f"ingestion provenance is missing fields: {missing}")
        return cls(
            source_id=str(payload[SOURCE_ID]),
            source_trust=str(payload[SOURCE_TRUST]),
            approval_status=str(payload[APPROVAL_STATUS]),
            approval_actor=str(payload[APPROVAL_ACTOR] or ""),
            approval_decided_at=str(payload[APPROVAL_DECIDED_AT] or ""),
            approval_note=str(payload[APPROVAL_NOTE] or ""),
            approved_content_hash=str(payload[APPROVED_CONTENT_HASH] or ""),
            trust_class=str(payload[TRUST_CLASS]),
            provenance_schema_version=str(payload[SCHEMA_VERSION]),
        )


def managed_record(source_id: str) -> SourceTrustRecord:
    """Return the compatibility record for an existing managed pipeline caller.

    This is the *explicit* legacy policy, not a silent default: before this
    contract existed, every source reaching ``offline/`` was ingested from the
    managed data root, so a caller that declares nothing is declaring managed
    internal content. It does not relax the seal gate — provenance still has to
    be persisted on the points it produces.
    """
    return SourceTrustRecord(
        source_id=source_id,
        source_trust=TRUST_MANAGED_INTERNAL,
        approval_status=APPROVAL_NOT_REQUIRED,
        trust_class=CLASS_MANAGED_INTERNAL,
    )


# ── Managed configuration resolution ─────────────────────────────────────────


def resolve_source_trust(relative_path: str, trust_rules: dict) -> str:
    """Resolve the provenance claim for one relative path, fail closed.

    Mirrors :func:`offline.source_discovery.resolve_permission`: rules are
    first-match-wins over ``path_pattern`` globs and a missing
    ``default_source_trust`` is an error, so a deployment cannot inherit public
    content by omitting the default the way a missing permission mask must not.
    """
    rules = (trust_rules or {}).get("rules", [])
    for rule in rules:
        pattern = rule.get("path_pattern", "")
        if not pattern:
            continue
        if fnmatch.fnmatch(relative_path, pattern) or fnmatch.fnmatch(f"/{relative_path}", pattern):
            return normalize_source_trust(rule["source_trust"])
    default_trust = trust_rules.get("default_source_trust")
    if default_trust is None:
        raise SourceTrustError("source trust rules must define default_source_trust")
    return normalize_source_trust(default_trust)


# ── Approval ledger ──────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_trust_decisions (
    source_id TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    approval_status TEXT NOT NULL,
    decided_by TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 1
)
"""


@dataclass(frozen=True)
class SourceTrustDecision:
    """One row of the approval ledger: a reviewer's decision about one source."""

    source_id: str
    content_hash: str
    approval_status: str
    decided_by: str
    decided_at: str
    note: str = ""
    revision: int = 1

    def to_dict(self) -> dict:
        return asdict(self)


class TrustRegistry:
    """SQLite ledger of explicit source trust decisions, keyed by ``source_id``.

    Approvals are stored with the content hash they were granted for, and
    :meth:`resolve` only honors a stored decision whose hash still matches the
    bytes on disk. Editing an approved file therefore returns it to
    ``PENDING_REVIEW`` — the approval does not silently carry over to content
    nobody reviewed. Every decision writes an audit event, so the ledger and the
    audit trail cannot disagree.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.execute(_SCHEMA)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> TrustRegistry:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def get(self, source_id: str) -> SourceTrustDecision | None:
        row = self._connection.execute(
            "SELECT * FROM source_trust_decisions WHERE source_id = ?", (source_id,)
        ).fetchone()
        return _row_to_decision(row) if row else None

    def all_decisions(self) -> list[SourceTrustDecision]:
        rows = self._connection.execute("SELECT * FROM source_trust_decisions ORDER BY source_id").fetchall()
        return [_row_to_decision(row) for row in rows]

    def decide(
        self,
        source_id: str,
        *,
        content_hash: str,
        approval_status: str,
        actor: str,
        note: str = "",
        decided_at: str | None = None,
    ) -> SourceTrustDecision:
        """Record an explicit reviewer decision and audit it.

        Fails closed on an unattributed decision: ``APPROVED`` and ``REJECTED``
        both require a named actor, because an approval nobody can attribute is
        indistinguishable from one nobody made.
        """
        approval = normalize_approval_status(approval_status)
        if approval in DECISION_APPROVAL_STATUSES:
            if not actor or not actor.strip():
                raise UnattributedApproval(
                    f"approval_status {approval!r} requires an attributable --actor; "
                    "an unreviewed source stays PENDING_REVIEW instead"
                )
        else:
            raise TrustApprovalNotAllowed(
                f"{approval!r} is not a review decision; use one of {sorted(DECISION_APPROVAL_STATUSES)}"
            )
        if not content_hash:
            raise SourceTrustError("content_hash is required; an approval must name the bytes it covers")

        previous = self.get(source_id)
        decision = SourceTrustDecision(
            source_id=source_id,
            content_hash=content_hash,
            approval_status=approval,
            decided_by=actor.strip(),
            decided_at=decided_at or datetime.now(timezone.utc).isoformat(),
            note=note,
            revision=(previous.revision + 1) if previous else 1,
        )
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO source_trust_decisions (
                    source_id, content_hash, approval_status, decided_by, decided_at, note, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    approval_status = excluded.approval_status,
                    decided_by = excluded.decided_by,
                    decided_at = excluded.decided_at,
                    note = excluded.note,
                    revision = excluded.revision
                """,
                (
                    decision.source_id,
                    decision.content_hash,
                    decision.approval_status,
                    decision.decided_by,
                    decision.decided_at,
                    decision.note,
                    decision.revision,
                ),
            )
        _audit_decision(decision, previous)
        return decision

    def resolve(
        self,
        source_id: str,
        *,
        declared_trust: str,
        content_hash: str,
    ) -> SourceTrustRecord:
        """Combine the declared trust claim with the ledger into one record.

        This is the only place a source becomes activatable, and it is a pure
        function of three inputs: the configured claim, the ledger, and the bytes
        on disk. There is no branch here that promotes a source on its own.
        """
        trust = normalize_source_trust(declared_trust)
        if trust == TRUST_MANAGED_INTERNAL:
            return SourceTrustRecord(
                source_id=source_id,
                source_trust=trust,
                approval_status=default_approval_status(trust),
                trust_class=CLASS_MANAGED_INTERNAL,
            )

        # An untrusted source with no matching decision is unreviewed, whatever
        # the ledger may say about other bytes of the same source.
        decision = self.get(source_id)
        if decision is None or decision.content_hash != content_hash:
            return SourceTrustRecord(
                source_id=source_id,
                source_trust=trust,
                approval_status=default_approval_status(trust),
                trust_class=CLASS_UNTRUSTED,
            )
        return SourceTrustRecord(
            source_id=source_id,
            source_trust=trust,
            approval_status=decision.approval_status,
            approval_actor=decision.decided_by,
            approval_decided_at=decision.decided_at,
            approval_note=decision.note,
            approved_content_hash=decision.content_hash,
            trust_class=effective_trust_class(trust, decision.approval_status),
        )

    def quarantined_sources(self, sources) -> list[dict]:
        """Return the review queue for the given discovered sources.

        ``sources`` are ``IngestionSource``-shaped objects; only
        ``source_id``/``source_trust``/``path`` are read, so this accepts
        discovery output and test doubles alike.
        """
        queue: list[dict] = []
        for source in sources:
            if source.source_trust not in QUARANTINED_TRUST_LEVELS:
                continue
            content_hash = file_content_hash(getattr(source, "path", ""))
            record = self.resolve(
                source.source_id,
                declared_trust=source.source_trust,
                content_hash=content_hash,
            )
            if record.is_quarantined:
                queue.append(
                    {
                        "source_id": source.source_id,
                        "path": getattr(source, "path", ""),
                        "content_hash": content_hash,
                        "source_trust": record.source_trust,
                        "approval_status": record.approval_status,
                        "trust_class": record.trust_class,
                        "reason": quarantine_reason(record.source_trust, record.approval_status),
                    }
                )
        return queue


def file_content_hash(path: str | Path) -> str:
    """Return the SHA256 of a file's bytes, or ``""`` when it cannot be read.

    The same hash the ingestion path stores, so an approval is bound to exactly
    the bytes that will be indexed. An unreadable file hashes to ``""``, which no
    stored decision can match — an unreadable source therefore resolves to
    ``PENDING_REVIEW`` rather than inheriting an approval.
    """
    if not path:
        return ""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def _row_to_decision(row: sqlite3.Row) -> SourceTrustDecision:
    return SourceTrustDecision(
        source_id=row["source_id"],
        content_hash=row["content_hash"],
        approval_status=row["approval_status"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
        note=row["note"],
        revision=row["revision"],
    )


def _audit_decision(decision: SourceTrustDecision, previous: SourceTrustDecision | None) -> None:
    """Emit one audit event for an explicit review decision.

    Imported lazily: the ingestion path must stay usable without the audit sinks,
    and an audit failure must not be swallowed into a silent promotion.
    """
    from common.audit import (
        ACTION_SOURCE_TRUST_DECISION,
        OUTCOME_DENIED,
        OUTCOME_SUCCESS,
        audit_event,
    )

    audit_event(
        action=ACTION_SOURCE_TRUST_DECISION,
        outcome=OUTCOME_SUCCESS if decision.approval_status == APPROVAL_APPROVED else OUTCOME_DENIED,
        actor_id=decision.decided_by,
        resource_type="knowledge_source",
        resource_id=decision.source_id,
        reason=(
            "explicit review approval recorded for the reviewed content hash"
            if decision.approval_status == APPROVAL_APPROVED
            else "explicit review rejection recorded; source is quarantined from activation"
        ),
        metadata={
            "approval_status": decision.approval_status,
            "approved_content_hash": decision.content_hash,
            "revision": decision.revision,
            "previous_approval_status": previous.approval_status if previous else "",
            "note": decision.note,
        },
    )


def audit_quarantine(
    *,
    source_id: str,
    trust_class: str,
    reason: str,
    actor_id: str,
    resource_type: str = "knowledge_source",
    epoch: str = "",
) -> None:
    """Record that a source was refused activation eligibility.

    ``trust_class`` is the derived bounded class, never raw document content, so
    the audit record stays free of anything an injected document could use to
    forge a log line.
    """
    from common.audit import ACTION_SOURCE_TRUST_QUARANTINE, OUTCOME_DENIED, audit_event

    audit_event(
        action=ACTION_SOURCE_TRUST_QUARANTINE,
        outcome=OUTCOME_DENIED,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=source_id,
        reason=reason,
        metadata={"trust_class": trust_class, "doc_version_epoch": epoch},
    )


def audit_activation_refused(
    *,
    source_id: str,
    trust_class: str,
    reason: str,
    actor_id: str,
    epoch: str,
) -> None:
    """Record a seal-time refusal of a quarantined source."""
    audit_quarantine(
        source_id=source_id,
        trust_class=trust_class,
        reason=reason,
        actor_id=actor_id,
        resource_type="knowledge_epoch",
        epoch=epoch,
    )


def enforce_writable_provenance(
    provenance,
    *,
    label: str,
    source_id: str,
    epoch: str,
) -> None:
    """Raise unless this provenance may be written into ``epoch``.

    Two refusals, and only two:

    * **unusable provenance** — absent, partial or from another schema version.
      This is the fail-closed half: a point with no readable trust contract is
      never written, so nothing enters an index without provenance at all.
    * **an explicit rejection** — the recorded decision was "do not ingest this",
      so staging it would contradict the decision a reviewer actually made.

    Quarantined-but-unapproved provenance is deliberately allowed to be
    **staged**. A staging epoch is not queryable, so writing it keeps the content
    inspectable by an operator without making it retrievable, and
    :func:`provenance_error` in ``offline/validator.py`` refuses to seal it. That
    is the whole point of a quarantine: hold the content, refuse to activate it.

    Every refusal is audited with the derived trust class only, so an injected
    document cannot influence the audit record.
    """
    try:
        record = SourceTrustRecord.from_payload(provenance)
    except SourceTrustError as exc:
        reason = f"{label} has no usable ingestion provenance: {exc}"
        audit_quarantine(
            source_id=source_id,
            trust_class=persisted_trust_class(provenance) or "UNKNOWN",
            reason=reason,
            actor_id=CLI_TRUST_ACTOR_ID,
            epoch=epoch,
        )
        raise SourceTrustError(reason) from exc

    if record.may_be_staged:
        return

    reason = f"{label} is not ingested: {record.source_id} was rejected in review"
    audit_quarantine(
        source_id=record.source_id,
        trust_class=record.trust_class,
        reason=reason,
        actor_id=CLI_TRUST_ACTOR_ID,
        epoch=epoch,
    )
    raise RejectedSourceError(reason)
