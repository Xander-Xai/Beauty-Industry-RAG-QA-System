"""Source discovery and path-based permission and trust resolution.

The relative path inside ``knowledge_base.data_dir`` is the default logical
``source_id``, so the same file tree mounted under a different root yields the
same identities. Permission and trust rules are both evaluated
first-match-wins, and both fail closed on a missing default.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from offline.document_processor import SUPPORTED_DOCUMENT_EXTENSIONS
from offline.image_processor import SUPPORTED_IMAGE_EXTENSIONS
from offline.snapshot_builder import IngestionSource, classify_source
from offline.source_trust import resolve_source_trust

SUPPORTED_SOURCE_EXTENSIONS = frozenset(SUPPORTED_DOCUMENT_EXTENSIONS) | frozenset(SUPPORTED_IMAGE_EXTENSIONS)


def resolve_permission(relative_path: str, permission_rules: dict) -> tuple[int, int]:
    rules = (permission_rules or {}).get("rules", [])
    for rule in rules:
        pattern = rule.get("path_pattern", "")
        if not pattern:
            continue
        if fnmatch.fnmatch(relative_path, pattern) or fnmatch.fnmatch(f"/{relative_path}", pattern):
            return int(rule["role_mask"]), int(rule["dept_mask"])
    default_role = permission_rules.get("default_role_mask")
    default_dept = permission_rules.get("default_dept_mask")
    if default_role is None or default_dept is None:
        raise ValueError("permission rules must define default_role_mask and default_dept_mask")
    return int(default_role), int(default_dept)


def resolve_supported_extensions(configured: list[str] | None) -> tuple[str, ...] | None:
    """Validate a configured extension allowlist against the parsers.

    A configured extension that no parser supports is rejected rather than
    silently claimed.
    """
    if not configured:
        return None
    normalized = tuple(extension.lower() for extension in configured)
    unsupported = [extension for extension in normalized if extension not in SUPPORTED_SOURCE_EXTENSIONS]
    if unsupported:
        raise ValueError(
            f"knowledge_base.supported_extensions contains unsupported formats: {unsupported}; "
            f"supported: {sorted(SUPPORTED_SOURCE_EXTENSIONS)}"
        )
    return normalized


def discover_sources(
    data_dir: str | Path,
    *,
    permission_rules: dict,
    extensions: tuple[str, ...] | None = None,
    trust_rules: dict | None = None,
) -> list[IngestionSource]:
    """Discover every supported source and resolve its masks and trust claim.

    ``trust_rules`` is the ``source_trust`` configuration block. When it is
    omitted the resolver falls back to its own required default, so a caller
    that forgets the block gets the same fail-closed behaviour as one that
    supplies an incomplete block: a missing ``default_source_trust`` is an error
    rather than a silent managed-content classification.
    """
    root = Path(data_dir).resolve()
    if not root.is_dir():
        raise ValueError(f"knowledge base data directory does not exist: {root}")
    sources: list[IngestionSource] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            document_type = classify_source(path)
        except ValueError:
            continue
        if extensions is not None and path.suffix.lower() not in extensions:
            continue
        relative_path = path.relative_to(root).as_posix()
        role_mask, dept_mask = resolve_permission(relative_path, permission_rules)
        source_trust = resolve_source_trust(relative_path, trust_rules or {})
        sources.append(
            IngestionSource(
                source_id=relative_path,
                path=str(path),
                document_type=document_type,
                role_mask=role_mask,
                dept_mask=dept_mask,
                relative_path=relative_path,
                source_trust=source_trust,
            )
        )
    return sources
