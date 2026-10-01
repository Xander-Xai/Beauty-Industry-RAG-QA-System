"""Shared input validation for the offline pipeline (fail closed)."""

from __future__ import annotations

import re
from pathlib import Path

UINT32_MAX = 0xFFFFFFFF
_EPOCH_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9._:@/-]+$")


def validate_permissions(role_mask: int, dept_mask: int) -> None:
    """Reject missing or out-of-range permission masks.

    A missing mask must never be silently treated as public.
    """
    for name, value in (("role_mask", role_mask), ("dept_mask", dept_mask)):
        if type(value) is not int or not 0 <= value <= UINT32_MAX:
            raise ValueError(f"{name} must be an integer uint32")


def validate_epoch(epoch: str) -> None:
    if not isinstance(epoch, str) or not _EPOCH_RE.fullmatch(epoch):
        raise ValueError("doc_version_epoch must contain only letters, digits, _ or -")


def validate_source_id(source_id: str) -> str:
    if not isinstance(source_id, str):
        raise ValueError("source_id must be a string")
    identity = source_id.strip()
    if not identity:
        raise ValueError("source_id must not be empty")
    if ".." in identity or identity.startswith("/") or identity.startswith("\\"):
        raise ValueError("source_id must not contain path traversal")
    if not _SOURCE_ID_RE.fullmatch(identity):
        raise ValueError("source_id contains unsupported characters")
    return identity


def ensure_within_root(resolved_path: Path, source_root: Path) -> str:
    """Return the POSIX relative path or fail closed when outside the root."""
    try:
        return resolved_path.relative_to(source_root).as_posix()
    except ValueError as exc:
        raise ValueError(
            f"source is outside the configured data root {source_root}; provide an explicit source_id"
        ) from exc
