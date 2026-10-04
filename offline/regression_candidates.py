"""Regression candidates: reviewed negative feedback -> human-approved cases.

One narrow capability, four stages, two independent review gates:

1. a feedback record that the **existing** feedback gate has already marked
   ``accepted`` and that carries a negative user signal becomes a regression
   **candidate**;
2. every candidate is created ``PENDING_REVIEW`` -- no code path in this module
   creates an accepted candidate;
3. a reviewer fills in the human expectation and explicitly accepts the
   candidate;
4. only accepted candidates are exported as regression cases.

Why two gates: the feedback gate answers "is this user signal worth building
on?", the candidate gate answers "is this a real case we want to keep testing?".
Collapsing them would let an unreviewed signal skip a human decision.

Model output is never ground truth. A negative record's ``answer`` and the
``evidence_doc_ids`` the pipeline retrieved are what the user rejected, so they
are recorded under ``provenance`` as ``observed`` material for diagnosis only
and never seed ``expected_behaviour`` or ``expected_evidence``. A candidate
without a human-written expectation cannot be accepted, and cannot be rendered
into the dataset: that is the fail-closed rule, enforced in one place
(:func:`build_regression_case`).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from offline.feedback_loop import (
    ACCEPTED,
    REJECTED,
    FeedbackRecord,
    FeedbackStore,
    is_negative,
)

#: Terminal review states are imported from the feedback gate rather than
#: redefined, so one reviewer vocabulary covers both gates. Only the pre-decision
#: state needs a distinct name: the candidate must read as *unreviewed*, not as
#: feedback that happens to still be waiting.
PENDING_REVIEW = "PENDING_REVIEW"
CANDIDATE_REVIEW_STATUSES = frozenset({PENDING_REVIEW, ACCEPTED, REJECTED})

#: Bumped when the provenance payload changes shape, so a stored dataset can be
#: interpreted without reading this file.
PROVENANCE_SCHEMA_VERSION = "regression-candidate/1"

GENERATOR = "offline.regression_candidates"

#: A candidate may only be built from feedback that already cleared the
#: feedback gate. Pending or rejected feedback stays in that queue.
SOURCE_FEEDBACK_STATUS = ACCEPTED

_WHITESPACE = re.compile(r"\s+")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS regression_candidates (
    case_id TEXT PRIMARY KEY,
    source_feedback_id TEXT NOT NULL,
    question TEXT NOT NULL,
    expected_behaviour TEXT NOT NULL DEFAULT '',
    expected_evidence TEXT NOT NULL,
    business_type TEXT NOT NULL DEFAULT '',
    intent TEXT NOT NULL DEFAULT '',
    provenance TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
    review_note TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT NOT NULL DEFAULT '',
    reviewed_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
)
"""


class RegressionCandidateError(Exception):
    """Base class for regression-candidate failures."""


class UnknownRegressionCase(RegressionCandidateError):
    """A review action targeted a case id that is not in the store."""


class MissingExpectationError(RegressionCandidateError):
    """A candidate cannot become a regression case without human expectations.

    Raised instead of emitting a half-specified case, so a missing expectation
    can never reach the dataset as a silently empty field.
    """


@dataclass(frozen=True)
class RegressionCandidate:
    case_id: str
    source_feedback_id: str
    question: str
    expected_behaviour: str = ""
    expected_evidence: list[str] = field(default_factory=list)
    business_type: str = ""
    intent: str = ""
    provenance: dict = field(default_factory=dict)
    review_status: str = PENDING_REVIEW
    review_note: str = ""
    reviewed_at: str = ""
    reviewed_by: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_question(question: str) -> str:
    """Collapse whitespace and case so cosmetic edits keep one case id."""
    return _WHITESPACE.sub(" ", question or "").strip().casefold()


def make_case_id(question: str, business_type: str = "") -> str:
    """Stable regression-case id.

    Derived only from reviewer-visible content -- the question and its business
    type -- never from time, rating, request id or feedback id. A question that
    fails again next month therefore maps to the same case instead of piling up
    near-duplicates, and rebuilding the store from the same feedback yields the
    same ids.
    """
    key = f"{normalize_question(question)}|{(business_type or '').strip().casefold()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def build_candidate(record: FeedbackRecord) -> RegressionCandidate:
    """Derive a ``PENDING_REVIEW`` candidate from one reviewed negative record.

    ``expected_behaviour`` is seeded only from ``record.correction``, which is
    human-authored by construction. ``record.answer`` is model output and is
    never copied: if a correction happens to equal the answer we treat it as
    unverified and leave the expectation for a human.
    """
    question = (record.query or "").strip()
    if not question:
        raise RegressionCandidateError("cannot build a regression candidate from feedback without a question")

    seeded = bool(record.correction.strip()) and record.correction.strip() != (record.answer or "").strip()
    return RegressionCandidate(
        case_id=make_case_id(question, record.business_type),
        source_feedback_id=record.feedback_id,
        question=question,
        expected_behaviour=record.correction.strip() if seeded else "",
        expected_evidence=[],
        business_type=record.business_type,
        intent=record.intent,
        provenance=build_provenance(
            record,
            expected_behaviour_origin="human_correction" if seeded else "pending_human_annotation",
        ),
        review_status=PENDING_REVIEW,
    )


def build_provenance(record: FeedbackRecord, *, expected_behaviour_origin: str) -> dict:
    """Everything needed to audit where a regression case came from.

    ``observed_evidence_doc_ids`` is deliberately named for what it is: the
    documents the rejected answer actually retrieved. For a negative record
    those documents are the failure, not the target.
    """
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "generator": GENERATOR,
        "source_feedback_id": record.feedback_id,
        "source_feedback_source": record.source,
        "source_feedback_rating": record.rating,
        "source_feedback_review_status": record.review_status,
        "source_feedback_created_at": record.created_at,
        "source_feedback_request_id": record.request_id,
        "source_feedback_session_ref": record.session_ref,
        "source_feedback_intent": record.intent,
        "expected_behaviour_origin": expected_behaviour_origin,
        "expected_evidence_origin": "pending_human_annotation",
        "model_answer_promoted": False,
        "observed_evidence_doc_ids": list(record.evidence_doc_ids),
    }


def build_regression_case(candidate: RegressionCandidate) -> dict:
    """Render an approved candidate as a regression-dataset row.

    The single choke point for the dataset contract, so acceptance and export
    cannot drift apart. Fails closed on a missing question, behaviour, evidence
    or approval rather than emitting a partially specified case.
    """
    if candidate.review_status != ACCEPTED:
        raise RegressionCandidateError(
            f"case {candidate.case_id} is {candidate.review_status!r}, only {ACCEPTED!r} candidates become dataset rows"
        )

    question = (candidate.question or "").strip()
    behaviour = (candidate.expected_behaviour or "").strip()
    evidence = [str(item).strip() for item in (candidate.expected_evidence or []) if str(item).strip()]

    missing = [
        name
        for name, value in (("question", question), ("expected_behaviour", behaviour), ("expected_evidence", evidence))
        if not value
    ]
    if missing:
        raise MissingExpectationError(
            f"case {candidate.case_id} cannot become a regression case; missing human-authored {', '.join(missing)}"
        )

    return {
        "case_id": candidate.case_id,
        "question": question,
        "expected_behaviour": behaviour,
        "expected_evidence": evidence,
        "business_type": candidate.business_type,
        "source_feedback_id": candidate.source_feedback_id,
        "created_at": candidate.created_at,
        "review_status": candidate.review_status,
        "review_note": candidate.review_note,
        "reviewed_at": candidate.reviewed_at,
        "reviewed_by": candidate.reviewed_by,
        "provenance": dict(candidate.provenance),
    }


class RegressionCandidateStore:
    """SQLite-backed store of regression candidates.

    Shares the feedback store's SQLite file but not its table: candidates have
    their own lifecycle and their own reviewer, and keeping them apart means an
    approval here can never be mistaken for the feedback gate's approval.
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

    def add(self, candidate: RegressionCandidate) -> bool:
        """Insert a new candidate; return False when the case already exists.

        Only ``PENDING_REVIEW`` may be inserted. A terminal status is refused
        here on purpose: ``export_regression_dataset`` publishes every
        ``accepted`` row, so accepting one at insert time would let a caller
        reach the dataset without ever calling :meth:`set_review_status`, which
        is the only place the human-review gate and its expectation check live.
        Approval has to travel through that method.
        """
        if candidate.review_status != PENDING_REVIEW:
            raise ValueError(
                f"new candidates must start as {PENDING_REVIEW}, got {candidate.review_status!r}; "
                f"use set_review_status() to approve or reject"
            )
        created_at = candidate.created_at or datetime.now(timezone.utc).isoformat()
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO regression_candidates (
                        case_id, source_feedback_id, question, expected_behaviour, expected_evidence,
                        business_type, intent, provenance, review_status, review_note,
                        reviewed_at, reviewed_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate.case_id,
                        candidate.source_feedback_id,
                        candidate.question,
                        candidate.expected_behaviour,
                        json.dumps(list(candidate.expected_evidence), ensure_ascii=False),
                        candidate.business_type,
                        candidate.intent,
                        json.dumps(candidate.provenance, ensure_ascii=False),
                        candidate.review_status,
                        candidate.review_note,
                        candidate.reviewed_at,
                        candidate.reviewed_by,
                        created_at,
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def get(self, case_id: str) -> RegressionCandidate | None:
        row = self._connection.execute("SELECT * FROM regression_candidates WHERE case_id = ?", (case_id,)).fetchone()
        return _row_to_candidate(row) if row is not None else None

    def require(self, case_id: str) -> RegressionCandidate:
        candidate = self.get(case_id)
        if candidate is None:
            raise UnknownRegressionCase(f"unknown regression case {case_id!r}")
        return candidate

    def set_expectations(
        self,
        case_id: str,
        *,
        expected_behaviour: str | None = None,
        expected_evidence: list[str] | None = None,
        note: str = "",
        reviewer: str = "",
    ) -> RegressionCandidate:
        """Record the reviewer's human expectation for a candidate.

        Partial updates are allowed: a reviewer may state the behaviour first and
        fill in evidence afterwards. Nothing here approves anything -- only
        :meth:`set_review_status` with ``accepted`` does.
        """
        candidate = self.require(case_id)
        behaviour = candidate.expected_behaviour if expected_behaviour is None else str(expected_behaviour).strip()
        if expected_evidence is None:
            evidence = list(candidate.expected_evidence)
        else:
            evidence = [str(item).strip() for item in expected_evidence if str(item).strip()]
        with self._connection:
            self._connection.execute(
                """
                UPDATE regression_candidates
                SET expected_behaviour = ?, expected_evidence = ?, review_note = ?, reviewed_by = ?
                WHERE case_id = ?
                """,
                (behaviour, json.dumps(evidence, ensure_ascii=False), note or candidate.review_note, reviewer, case_id),
            )
        return self.require(case_id)

    def set_review_status(
        self, case_id: str, status: str, *, note: str = "", reviewer: str = ""
    ) -> RegressionCandidate:
        """Move a candidate through review.

        ``accepted`` is refused unless the candidate already carries a
        human-authored behaviour and at least one expected evidence item, so the
        approval step cannot ratify a guess.

        ``reviewed_at`` is stamped on every transition rather than preserved
        from the first one: a candidate that was rejected and later accepted
        must record when it was accepted, or the exported case attributes the
        approval to the rejection.
        """
        if status not in CANDIDATE_REVIEW_STATUSES:
            raise ValueError(f"invalid review_status {status!r}")
        candidate = self.require(case_id)
        if status == ACCEPTED:
            _assert_has_expectations(candidate)
        reviewed_at = datetime.now(timezone.utc).isoformat()
        with self._connection:
            self._connection.execute(
                """
                UPDATE regression_candidates
                SET review_status = ?, review_note = ?, reviewed_at = ?, reviewed_by = ?
                WHERE case_id = ?
                """,
                (status, note or candidate.review_note, reviewed_at, reviewer or candidate.reviewed_by, case_id),
            )
        return self.require(case_id)

    def list_candidates(self, *, status: str | None = None) -> list[RegressionCandidate]:
        if status is None:
            rows = self._connection.execute("SELECT * FROM regression_candidates").fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM regression_candidates WHERE review_status = ?", (status,)
            ).fetchall()
        return [_row_to_candidate(row) for row in rows]

    def count(self, status: str | None = None) -> int:
        if status is None:
            row = self._connection.execute("SELECT COUNT(*) AS n FROM regression_candidates").fetchone()
        else:
            row = self._connection.execute(
                "SELECT COUNT(*) AS n FROM regression_candidates WHERE review_status = ?", (status,)
            ).fetchone()
        return int(row["n"])


class RegressionCandidateLoop:
    """Turn reviewed negative feedback into a human-approved regression dataset."""

    def __init__(
        self,
        store_path: str | Path,
        *,
        output_dir: str | Path = "./data/feedback",
        candidate_store: RegressionCandidateStore | None = None,
    ):
        self.feedback_store = FeedbackStore(store_path)
        self.candidate_store = candidate_store or RegressionCandidateStore(store_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        self.candidate_store.close()
        self.feedback_store.close()

    def collect_candidates(self, *, source_status: str | None = SOURCE_FEEDBACK_STATUS) -> dict:
        """Create ``PENDING_REVIEW`` candidates from reviewed negative feedback.

        Idempotent: a case id that already exists is left untouched, so
        re-running over the same feedback never duplicates a case.
        """
        created = 0
        skipped = 0
        for record in self.feedback_store.list_records(status=source_status):
            if not is_negative(record):
                continue
            try:
                candidate = build_candidate(record)
            except RegressionCandidateError:
                skipped += 1
                continue
            created += int(self.candidate_store.add(candidate))
        return {"created": created, "skipped": skipped}

    def export_candidates(
        self, *, status: str | None = PENDING_REVIEW, filename: str = "regression_candidates.jsonl"
    ) -> str:
        """Export candidates for review. Never a dataset: this is the work queue."""
        rows = [candidate.to_dict() for candidate in self.candidate_store.list_candidates(status=status)]
        return _write_jsonl(self.output_dir / filename, rows)

    def export_regression_dataset(self, filename: str = "regression_dataset.jsonl") -> str:
        """Export human-approved regression cases, failing closed on any gap."""
        rows = [build_regression_case(candidate) for candidate in self.candidate_store.list_candidates(status=ACCEPTED)]
        return _write_jsonl(self.output_dir / filename, rows)

    def run_export_cycle(
        self, *, status: str | None = PENDING_REVIEW, source_status: str | None = SOURCE_FEEDBACK_STATUS
    ) -> dict:
        """Collect candidates, export the review queue, export the approved dataset."""
        collected = self.collect_candidates(source_status=source_status)
        queue = self.export_candidates(status=status)
        dataset = self.export_regression_dataset()
        return {
            "candidates_created": collected["created"],
            "candidates_skipped": collected["skipped"],
            "candidates_total": self.candidate_store.count(),
            "pending_review": self.candidate_store.count(PENDING_REVIEW),
            "accepted": self.candidate_store.count(ACCEPTED),
            "rejected": self.candidate_store.count(REJECTED),
            "review_queue": queue,
            "regression_dataset": dataset,
        }


def _assert_has_expectations(candidate: RegressionCandidate) -> None:
    behaviour = (candidate.expected_behaviour or "").strip()
    evidence = [str(item).strip() for item in (candidate.expected_evidence or []) if str(item).strip()]
    missing = [
        name for name, value in (("expected_behaviour", behaviour), ("expected_evidence", evidence)) if not value
    ]
    if missing:
        raise MissingExpectationError(
            f"case {candidate.case_id} cannot be accepted without human-authored {', '.join(missing)}"
        )


def _write_jsonl(path: Path, rows: list[dict]) -> str:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return str(path)


def _row_to_candidate(row: sqlite3.Row) -> RegressionCandidate:
    return RegressionCandidate(
        case_id=row["case_id"],
        source_feedback_id=row["source_feedback_id"],
        question=row["question"],
        expected_behaviour=row["expected_behaviour"],
        expected_evidence=json.loads(row["expected_evidence"] or "[]"),
        business_type=row["business_type"],
        intent=row["intent"],
        provenance=json.loads(row["provenance"] or "{}"),
        review_status=row["review_status"],
        review_note=row["review_note"],
        reviewed_at=row["reviewed_at"],
        reviewed_by=row["reviewed_by"],
        created_at=row["created_at"],
    )
