#!/usr/bin/env python3
"""Check repository metadata and links that should stay aligned."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DOCS = [
    ROOT / "README.md",
    ROOT / "PRD.md",
    *sorted((ROOT / "docs").glob("*.md")),
]
STATUSES = {"VERIFIED", "PARTIAL", "PLANNED", "BROKEN", "STALE", "HISTORICAL"}


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def check_markdown_links(path: Path, errors: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    for target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", text):
        target = target.strip().split()[0].strip("<>")
        if not target or target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        local_target = target.split("#", 1)[0].split("?", 1)[0]
        if not local_target:
            continue
        if not (path.parent / local_target).resolve().exists():
            fail(errors, f"{path.relative_to(ROOT)}: broken local link {target}")


def resolve_repository_path(reference: str) -> Path | None:
    normalized = reference.removeprefix("./").removeprefix("/")
    candidate = (ROOT / normalized).resolve()
    if candidate.exists():
        return candidate
    if "/" not in reference:
        matches = [
            path
            for path in ROOT.rglob(reference)
            if not any(part in {".git", ".venv", "venv", "node_modules", ".superpowers"} for part in path.parts)
        ]
        if len(matches) == 1:
            return matches[0]
    return None


def check_documented_paths(path: Path, errors: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    planned_missing = {
        "offline/document_processor.py",
        "offline/image_processor.py",
        "offline/vectorizer.py",
        "offline/scheduler.py",
        "offline/feedback_loop.py",
        "tests/test_offline_pipeline.py",
    }
    path_pattern = re.compile(r"`((?:\.?/?[\w.-]+/)+[\w.-]+\.(?:py|md|json|ya?ml|sh|jsx|toml|txt))`")
    for reference in path_pattern.findall(text):
        normalized = reference.removeprefix("./")
        if normalized.startswith("models/") or normalized.startswith("docs/superpowers/"):
            continue  # operator-supplied assets or historical plans
        if normalized in planned_missing:
            continue  # explicitly documented as absent/planned
        if resolve_repository_path(normalized) is None:
            fail(errors, f"{path.relative_to(ROOT)}: referenced local path does not exist: {reference}")


def check_documented_python_commands(path: Path, errors: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    command_pattern = re.compile(r"(?m)^\s*(?:\$\s*)?python(?:3|\d+(?:\.\d+)?)?\s+([\w./-]+\.py)\b")
    for entrypoint in command_pattern.findall(text):
        if not (ROOT / entrypoint).is_file():
            fail(errors, f"{path.relative_to(ROOT)}: documented Python entrypoint does not exist: {entrypoint}")


def check_entrypoint_imports(entrypoint: Path, errors: list[str]) -> None:
    try:
        tree = ast.parse(entrypoint.read_text(encoding="utf-8"), filename=str(entrypoint))
    except (OSError, SyntaxError) as exc:
        fail(errors, f"cannot parse entrypoint {entrypoint.relative_to(ROOT)}: {exc}")
        return

    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        for module in modules:
            parts = module.split(".")
            local_root = ROOT / parts[0]
            if not local_root.exists():
                continue  # stdlib or installed dependency
            module_path = ROOT.joinpath(*parts).with_suffix(".py")
            package_path = ROOT.joinpath(*parts) / "__init__.py"
            if not module_path.exists() and not package_path.exists():
                fail(errors, f"{entrypoint.relative_to(ROOT)}: unresolved local import {module}")


def main() -> int:
    errors: list[str] = []

    config_path = ROOT / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        runtime_version = config["system"]["version"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        fail(errors, f"config.json is invalid or lacks system.version: {exc}")
        runtime_version = None

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    releases = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)
    if not releases:
        fail(errors, "CHANGELOG.md has no dated semantic-version release heading")
    elif runtime_version and releases[0] != runtime_version:
        fail(errors, f"runtime version {runtime_version} differs from latest changelog release {releases[0]}")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if not (ROOT / "app.py").is_file() or "`app.py`" not in readme:
        fail(errors, "README must identify the existing canonical app.py entrypoint")
    if re.search(r"(?im)^\s*License\s*:\s*MIT\s*$|\[MIT\]\(LICENSE\)", readme) and not (ROOT / "LICENSE").is_file():
        fail(errors, "README declares MIT but root LICENSE is missing")
    if re.search(
        r"(?m)^\s*python(?:3|\d+(?:\.\d+)?)?\s+run_offline\.py\s+--mode\s+(?:create-index|incremental|full|feedback)\b",
        readme,
    ):
        fail(errors, "README advertises unavailable offline ingestion commands")

    docs_index = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    for heading in ("Canonical / Current", "Historical / Implementation Plans"):
        if heading not in docs_index:
            fail(errors, f"docs/README.md must separate current and historical documentation ({heading})")

    audit_path = ROOT / "docs/repository-truth-audit.md"
    if audit_path.exists():
        audit_text = audit_path.read_text(encoding="utf-8")
        if not re.search(r"(?m)^Reconciled candidate:\s+`HEAD`(?:\s|$)", audit_text):
            fail(errors, "repository truth audit must resolve its candidate from HEAD at verification time")
        if not re.search(r"(?m)^Post-reconciliation verification date:\s+2026-10-01\.?\s*$", audit_text):
            fail(errors, "repository truth audit verification date is missing or stale")
        audit_lines = audit_text.splitlines()
        header = next((line for line in audit_lines if line.startswith("| Area |")), "")
        columns_header = [part.strip().lower() for part in header.strip("|").split("|")]
        status_column = columns_header.index("status") if "status" in columns_header else -1
        for line_number, line in enumerate(audit_lines, 1):
            if line.startswith("|") and "---" not in line and "Area" not in line:
                columns = [part.strip() for part in line.strip("|").split("|")]
                if status_column < 0 or len(columns) <= status_column or columns[status_column] not in STATUSES:
                    value = columns[status_column] if status_column >= 0 and len(columns) > status_column else "missing"
                    fail(errors, f"repository audit line {line_number}: invalid status {value!r}")
        audit_text_lower = audit_text.lower()
        if "offline ingestion" in audit_text_lower and "| planned |" not in audit_text_lower:
            fail(errors, "offline ingestion must remain classified as PLANNED in repository audit")

    contract_dir = ROOT / "tests/contracts"
    if contract_dir.exists() and any(path.name.startswith("test_") for path in contract_dir.rglob("*.py")):
        fail(errors, "planned contracts under tests/contracts must not match pytest's default test_*.py collection")

    try:
        tracked = (
            subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True)
            .stdout.decode()
            .split("\0")
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        fail(errors, f"cannot inspect tracked runtime artifacts: {exc}")
        tracked = []
    runtime_state_paths = [
        path
        for path in tracked
        if path and (path.endswith(".pid") or "/state/server-stopped" in path or "/state/server.pid" in path)
    ]
    if runtime_state_paths:
        fail(errors, f"runtime state artifacts must not be tracked: {', '.join(runtime_state_paths)}")

    for doc in CANONICAL_DOCS:
        if doc.exists():
            check_markdown_links(doc, errors)
            check_documented_paths(doc, errors)
            check_documented_python_commands(doc, errors)

    active_entrypoints = [ROOT / name for name in ("app.py", "run_offline.py", "run_services.py")]
    for entrypoint in active_entrypoints:
        if not entrypoint.is_file():
            fail(errors, f"active Python entrypoint is missing: {entrypoint.relative_to(ROOT)}")
        else:
            check_entrypoint_imports(entrypoint, errors)

    if errors:
        print("Repository consistency check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Repository consistency check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
