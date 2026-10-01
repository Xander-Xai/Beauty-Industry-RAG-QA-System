"""Crash-safe SQLite state store for incremental ingestion detection.

Change detection is content-hash authoritative: NEW/UNCHANGED/MODIFIED is
decided by comparing the stored content hash with the current content hash.
``file_size`` and ``mtime_ns`` are recorded for observability only and are
never used to skip the hash comparison, so a preserved ``mtime`` with changed
content is still detected. The snapshot builder hashes every discovered source.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

NEW = "NEW"
UNCHANGED = "UNCHANGED"
MODIFIED = "MODIFIED"
DELETED = "DELETED"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_state (
    source_id TEXT PRIMARY KEY,
    relative_path TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    document_type TEXT NOT NULL,
    last_successful_epoch TEXT NOT NULL DEFAULT '',
    last_processed_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    role_mask INTEGER NOT NULL DEFAULT 0,
    dept_mask INTEGER NOT NULL DEFAULT 0
)
"""

_MIGRATION_COLUMNS = {
    "role_mask": "INTEGER NOT NULL DEFAULT 0",
    "dept_mask": "INTEGER NOT NULL DEFAULT 0",
}


@dataclass(frozen=True)
class SourceState:
    source_id: str
    relative_path: str
    file_size: int
    mtime_ns: int
    content_hash: str
    document_type: str
    last_successful_epoch: str
    last_processed_at: str
    status: str
    role_mask: int = 0
    dept_mask: int = 0


@dataclass(frozen=True)
class ChangeSet:
    new: list[str]
    modified: list[str]
    unchanged: list[str]
    deleted: list[str]


class StateStore:
    """SQLite-backed source state with atomic transactions."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.execute(_SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        existing = {row["name"] for row in self._connection.execute("PRAGMA table_info(source_state)")}
        for column, definition in _MIGRATION_COLUMNS.items():
            if column not in existing:
                self._connection.execute(f"ALTER TABLE source_state ADD COLUMN {column} {definition}")

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def get(self, source_id: str) -> SourceState | None:
        row = self._connection.execute("SELECT * FROM source_state WHERE source_id = ?", (source_id,)).fetchone()
        return _row_to_state(row) if row else None

    def all_states(self) -> list[SourceState]:
        rows = self._connection.execute("SELECT * FROM source_state").fetchall()
        return [_row_to_state(row) for row in rows]

    def active_source_ids(self) -> set[str]:
        rows = self._connection.execute("SELECT source_id FROM source_state WHERE status = 'active'").fetchall()
        return {row["source_id"] for row in rows}

    def upsert(self, state: SourceState) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO source_state (
                    source_id, relative_path, file_size, mtime_ns, content_hash,
                    document_type, last_successful_epoch, last_processed_at, status,
                    role_mask, dept_mask
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    relative_path = excluded.relative_path,
                    file_size = excluded.file_size,
                    mtime_ns = excluded.mtime_ns,
                    content_hash = excluded.content_hash,
                    document_type = excluded.document_type,
                    last_successful_epoch = excluded.last_successful_epoch,
                    last_processed_at = excluded.last_processed_at,
                    status = excluded.status,
                    role_mask = excluded.role_mask,
                    dept_mask = excluded.dept_mask
                """,
                (
                    state.source_id,
                    state.relative_path,
                    state.file_size,
                    state.mtime_ns,
                    state.content_hash,
                    state.document_type,
                    state.last_successful_epoch,
                    state.last_processed_at,
                    state.status,
                    state.role_mask,
                    state.dept_mask,
                ),
            )

    def mark_deleted(self, source_id: str) -> None:
        with self._connection:
            self._connection.execute("UPDATE source_state SET status = 'archived' WHERE source_id = ?", (source_id,))

    def classify(
        self,
        source_id: str,
        *,
        content_hash: str,
        role_mask: int | None = None,
        dept_mask: int | None = None,
    ) -> str:
        state = self.get(source_id)
        if state is None or state.status != "active":
            return NEW
        if state.content_hash != content_hash:
            return MODIFIED
        if role_mask is not None and role_mask != state.role_mask:
            return MODIFIED
        if dept_mask is not None and dept_mask != state.dept_mask:
            return MODIFIED
        return UNCHANGED

    def diff(self, current: dict[str, dict]) -> ChangeSet:
        """Classify the current source set against stored state.

        ``current`` maps ``source_id`` to a dict with ``content_hash`` and,
        when available, the resolved ``role_mask``/``dept_mask`` so permission
        changes force reprocessing even when the file bytes are unchanged.
        """
        new, modified, unchanged = [], [], []
        for source_id, metadata in current.items():
            change = self.classify(
                source_id,
                content_hash=metadata["content_hash"],
                role_mask=metadata.get("role_mask"),
                dept_mask=metadata.get("dept_mask"),
            )
            if change == NEW:
                new.append(source_id)
            elif change == MODIFIED:
                modified.append(source_id)
            else:
                unchanged.append(source_id)
        deleted = sorted(self.active_source_ids() - set(current.keys()))
        return ChangeSet(new=sorted(new), modified=sorted(modified), unchanged=sorted(unchanged), deleted=deleted)


def _row_to_state(row: sqlite3.Row) -> SourceState:
    return SourceState(
        source_id=row["source_id"],
        relative_path=row["relative_path"],
        file_size=row["file_size"],
        mtime_ns=row["mtime_ns"],
        content_hash=row["content_hash"],
        document_type=row["document_type"],
        last_successful_epoch=row["last_successful_epoch"],
        last_processed_at=row["last_processed_at"],
        status=row["status"],
        role_mask=row["role_mask"] if "role_mask" in row.keys() else 0,
        dept_mask=row["dept_mask"] if "dept_mask" in row.keys() else 0,
    )
