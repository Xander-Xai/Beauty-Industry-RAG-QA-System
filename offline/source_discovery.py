"""Source discovery and path-based permission resolution.

The relative path inside ``knowledge_base.data_dir`` is the default logical
``source_id``, so the same file tree mounted under a different root yields the
same identities. Permission rules are evaluated first-match-wins and missing
defaults fail closed.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from offline.snapshot_builder import IngestionSource, classify_source


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


def discover_sources(
    data_dir: str | Path,
    *,
    permission_rules: dict,
    extensions: tuple[str, ...] | None = None,
) -> list[IngestionSource]:
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
        sources.append(
            IngestionSource(
                source_id=relative_path,
                path=str(path),
                document_type=document_type,
                role_mask=role_mask,
                dept_mask=dept_mask,
                relative_path=relative_path,
            )
        )
    return sources
