"""Cross-platform advisory file locking for the offline pipeline.

The Phase 1 slice used ``fcntl`` directly, which fails at import time on
Windows. This module keeps the same semantics (non-evictable OS advisory
locks, private lock directory, shared/exclusive coordination) while selecting
an implementation at runtime:

- POSIX: ``fcntl.flock`` with ``LOCK_SH`` / ``LOCK_EX``.
- Windows: ``msvcrt.locking``. Windows has no shared advisory lock in the
  standard library, so shared requests are emulated with an exclusive lock.
  The epoch seal path relies on shared/exclusive separation for cross-process
  coordination only on POSIX; on Windows writers remain serialized, which is
  safe (never less strict) and never corrupts a snapshot.
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

_LOCK_DIR_ENV = "OFFLINE_INGESTION_LOCK_DIR"


def _owner_id() -> int:
    return os.getuid() if hasattr(os, "getuid") else os.getpid()


def resolve_lock_dir() -> Path:
    """Return the private directory used for offline ingestion locks."""
    owner_id = _owner_id()
    configured = os.environ.get(_LOCK_DIR_ENV)
    lock_dir = (
        Path(configured) if configured else Path(tempfile.gettempdir()) / f"beauty-rag-offline-{owner_id}"
    )
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stat = lock_dir.stat()
    if hasattr(os, "getuid") and stat.st_uid != owner_id:
        raise PermissionError(f"ingestion lock directory is owned by another user: {lock_dir}")
    if stat.st_mode & 0o077:
        raise PermissionError(f"ingestion lock directory must not be accessible by other users: {lock_dir}")
    return lock_dir


def lock_path(namespace: str) -> Path:
    lock_dir = resolve_lock_dir()
    lock_key = hashlib.sha256(namespace.encode("utf-8")).hexdigest()
    return lock_dir / f"{lock_key}.lock"


def open_lock_file(namespace: str) -> int:
    path = lock_path(namespace)
    return os.open(path, os.O_CREAT | os.O_RDWR, 0o600)


def acquire(file_descriptor: int, *, shared: bool) -> None:
    """Acquire an advisory lock on ``file_descriptor`` (blocking)."""
    if sys.platform.startswith("win"):
        import msvcrt

        # msvcrt locks a byte range; lock a single byte from the start.
        os.lseek(file_descriptor, 0, os.SEEK_SET)
        msvcrt.locking(file_descriptor, msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(file_descriptor, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)


def release(file_descriptor: int) -> None:
    if sys.platform.startswith("win"):
        import msvcrt

        os.lseek(file_descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(file_descriptor, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        fcntl.flock(file_descriptor, fcntl.LOCK_UN)


@contextmanager
def file_lock(namespace: str, *, shared: bool = False):
    """Context manager holding an OS advisory lock for ``namespace``."""
    file_descriptor = open_lock_file(namespace)
    try:
        acquire(file_descriptor, shared=shared)
        yield
    finally:
        release(file_descriptor)
        os.close(file_descriptor)


class FileLockProvider:
    """Factory for the three lock scopes used by epoch-aware writers."""

    def replacement_lock(self, doc_id: str, epoch: str):
        return file_lock(f"{doc_id}:{epoch}")

    def epoch_lock(self, epoch: str, *, shared: bool):
        return file_lock(f"epoch:{epoch}", shared=shared)

    def epoch_embedding_lock(self, epoch: str):
        return file_lock(f"epoch-embedding:{epoch}")
