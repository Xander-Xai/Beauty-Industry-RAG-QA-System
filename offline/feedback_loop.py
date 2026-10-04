"""Unified offline feedback pipeline.

Collects retrieval/answer feedback into one reviewable store. User feedback is
never treated as automatically-accepted ground truth: records carry a
``review_status`` and only ``accepted`` records are exported as training or
evaluation candidates. The existing rewrite feedback utility is reused rather
than duplicated.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PENDING = "pending"
ACCEPTED = "accepted"
REJECTED = "rejected"
_REVIEW_STATUSES = {PENDING, ACCEPTED, REJECTED}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    answer TEXT NOT NULL,
    evidence_doc_ids TEXT NOT NULL,
    request_id TEXT NOT NULL DEFAULT '',
    session_ref TEXT NOT NULL DEFAULT '',
    rating REAL,
    correction TEXT NOT NULL DEFAULT '',
    business_type TEXT NOT NULL DEFAULT '',
    intent TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'user',
    review_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class FeedbackRecord:
    feedback_id: str
    query: str
    answer: str = ""
    evidence_doc_ids: list[str] = field(default_factory=list)
    request_id: str = ""
    session_ref: str = ""
    rating: float | None = None
    correction: str = ""
    business_type: str = ""
    intent: str = ""
    source: str = "user"
    review_status: str = PENDING
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _hash_ref(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16] if value else ""


def make_feedback_id(query: str, request_id: str = "") -> str:
    return hashlib.sha256(f"{request_id}:{query}".encode()).hexdigest()


def is_negative(record: FeedbackRecord) -> bool:
    """True when a record carries an explicit negative user signal.

    Public because "this record was rejected by the user" is a property of the
    feedback gate itself, so downstream consumers (regression candidates) must
    not re-implement the threshold.
    """
    return record.rating is not None and record.rating <= 0


class FeedbackStore:
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

    def add(self, record: FeedbackRecord) -> bool:
        """Insert a feedback record; return False when it is a duplicate."""
        if record.review_status not in _REVIEW_STATUSES:
            raise ValueError(f"invalid review_status {record.review_status!r}")
        created_at = record.created_at or datetime.now(timezone.utc).isoformat()
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO feedback (
                        feedback_id, query, answer, evidence_doc_ids, request_id, session_ref,
                        rating, correction, business_type, intent, source, review_status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.feedback_id,
                        record.query,
                        record.answer,
                        json.dumps(record.evidence_doc_ids, ensure_ascii=False),
                        record.request_id,
                        record.session_ref,
                        record.rating,
                        record.correction,
                        record.business_type,
                        record.intent,
                        record.source,
                        record.review_status,
                        created_at,
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def set_review_status(self, feedback_id: str, status: str) -> None:
        if status not in _REVIEW_STATUSES:
            raise ValueError(f"invalid review_status {status!r}")
        with self._connection:
            self._connection.execute(
                "UPDATE feedback SET review_status = ? WHERE feedback_id = ?", (status, feedback_id)
            )

    def list_records(self, *, status: str | None = None) -> list[FeedbackRecord]:
        if status is None:
            rows = self._connection.execute("SELECT * FROM feedback").fetchall()
        else:
            rows = self._connection.execute("SELECT * FROM feedback WHERE review_status = ?", (status,)).fetchall()
        return [_row_to_record(row) for row in rows]

    def count(self, status: str | None = None) -> int:
        if status is None:
            row = self._connection.execute("SELECT COUNT(*) AS n FROM feedback").fetchone()
        else:
            row = self._connection.execute(
                "SELECT COUNT(*) AS n FROM feedback WHERE review_status = ?", (status,)
            ).fetchone()
        return int(row["n"])


class FeedbackLoop:
    """Collect, normalize, review-gate, and export feedback datasets."""

    def __init__(
        self,
        store_path: str | Path,
        *,
        output_dir: str | Path = "./data/feedback",
        rewrite_feedback=None,
    ):
        self.store = FeedbackStore(store_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.rewrite_feedback = rewrite_feedback

    def collect_from_rewrite(self, days: int = 7) -> int:
        """Import existing rewrite feedback logs as pending records."""
        if self.rewrite_feedback is None:
            try:
                from rewrite.feedback import RewriteFeedback

                self.rewrite_feedback = RewriteFeedback()
            except Exception:
                return 0
        logs = self.rewrite_feedback.collect_rewrite_logs(days=days)
        imported = 0
        for log in logs:
            query = log.get("rewritten_query") or log.get("query_hash", "")
            record = FeedbackRecord(
                feedback_id=make_feedback_id(query, log.get("request_id", "")),
                query=query,
                request_id=log.get("request_id", ""),
                rating=log.get("user_feedback") if log.get("user_feedback", -1) != -1 else None,
                business_type=log.get("business_type", ""),
                intent=log.get("intent", ""),
                source="rewrite",
            )
            imported += int(self.store.add(record))
        return imported

    def export_review_queue(self) -> str:
        return self._export(
            [record.to_dict() for record in self.store.list_records(status=PENDING)],
            "review_queue.jsonl",
        )

    def export_datasets(self) -> dict[str, str]:
        accepted = self.store.list_records(status=ACCEPTED)
        outputs = {
            "hard_negatives": self._export(
                [_hard_negative(record) for record in accepted if is_negative(record)],
                "retrieval_hard_negatives.jsonl",
            ),
            "rewrite_corrections": self._export(
                [_rewrite_correction(record) for record in accepted if record.correction],
                "rewrite_corrections.jsonl",
            ),
            "evaluation": self._export(
                [_evaluation(record) for record in accepted if record.answer],
                "rag_evaluation.jsonl",
            ),
            "qlora_dpo": self._export(
                [_qlora_candidate(record) for record in accepted if record.correction and record.answer],
                "qlora_dpo_candidates.jsonl",
            ),
        }
        return outputs

    def run_full_feedback_cycle(self, *, days: int = 7) -> dict:
        collected = self.collect_from_rewrite(days=days)
        review_queue = self.export_review_queue()
        datasets = self.export_datasets()
        return {
            "collected": collected,
            "pending": self.store.count(PENDING),
            "accepted": self.store.count(ACCEPTED),
            "rejected": self.store.count(REJECTED),
            "review_queue": review_queue,
            "datasets": datasets,
        }

    def _export(self, rows: list[dict], filename: str) -> str:
        path = self.output_dir / filename
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return str(path)


def _hard_negative(record: FeedbackRecord) -> dict:
    return {
        "query": record.query,
        "negative_doc_ids": record.evidence_doc_ids,
        "rating": record.rating,
        "feedback_id": record.feedback_id,
    }


def _rewrite_correction(record: FeedbackRecord) -> dict:
    return {
        "query": record.query,
        "correction": record.correction,
        "business_type": record.business_type,
        "feedback_id": record.feedback_id,
    }


def _evaluation(record: FeedbackRecord) -> dict:
    return {
        "query": record.query,
        "answer": record.answer,
        "evidence_doc_ids": record.evidence_doc_ids,
        "rating": record.rating,
        "feedback_id": record.feedback_id,
    }


def _qlora_candidate(record: FeedbackRecord) -> dict:
    return {
        "prompt": record.query,
        "chosen": record.correction,
        "rejected": record.answer,
        "feedback_id": record.feedback_id,
    }


def _row_to_record(row: sqlite3.Row) -> FeedbackRecord:
    return FeedbackRecord(
        feedback_id=row["feedback_id"],
        query=row["query"],
        answer=row["answer"],
        evidence_doc_ids=json.loads(row["evidence_doc_ids"] or "[]"),
        request_id=row["request_id"],
        session_ref=row["session_ref"],
        rating=row["rating"],
        correction=row["correction"],
        business_type=row["business_type"],
        intent=row["intent"],
        source=row["source"],
        review_status=row["review_status"],
        created_at=row["created_at"],
    )
