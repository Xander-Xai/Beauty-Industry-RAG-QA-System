#!/usr/bin/env python3
"""Check repository metadata, links, and drift-prone documentation claims.

This guard is intentionally conservative: it fails on broken local references,
runtime-artifact leaks, version drift, documented-but-missing offline CLI
subcommands, stale "offline ingestion is missing" claims in current operator
docs, superseded governance/contract claims in canonical docs, post-merge
reconciliation-phase wording (pending candidate / awaiting merge / stale
latest-merged-main references), and an invalid/absent repository truth audit. It
does not flag historical CHANGELOG text or historical implementation plans.

The truth audit is expected to resolve its candidate from ``HEAD`` and to carry
an ISO ``YYYY-MM-DD`` verification date. The date is validated for shape only;
the guard never hardcodes a specific date or depends on the current date, a
GitHub API, or wall-clock state, so runs stay deterministic.
"""

from __future__ import annotations

import argparse
import ast
import datetime
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

# Current operator-facing docs that must not regress to "offline ingestion is
# missing" claims. Historical docs (CHANGELOG, the audit's history section) and
# historical plans are excluded on purpose.
CURRENT_OFFLINE_DOCS = [
    ROOT / "README.md",
    ROOT / "PRD.md",
    ROOT / "docs/data-admin-guide.md",
    ROOT / "docs/deployment-guide.md",
    ROOT / "docs/operations-guide.md",
    ROOT / "docs/pre-launch-checklist.md",
    ROOT / "docs/user-guide.md",
]

STALE_OFFLINE_CLAIM_PATTERNS = [
    r"TXT[- ]only",
    r"仅支持.{0,8}TXT",
    r"只支持.{0,8}TXT",
    r"唯一.{0,10}ingest-text",
    r"PDF/DOCX/XLSX.{0,24}(未实现|尚未实现|not implemented)",
    r"(未实现|尚未实现|不包含|不存在).{0,24}(ingestion|离线管线|文档导入|导入管线)",
    r"(不含|没有).{0,10}(文档\s*ingestion|离线 ingestion|原始文档导入)",
    r"offline.{0,12}modules.{0,24}(不存在|missing|absent)",
    r"no\s+ingestion\s+pipeline",
]

# Current canonical docs must not carry superseded governance state. Merged PR /
# closed Issue status belongs to Git/GitHub history, not long-term docs.
STALE_GOVERNANCE_CLAIM_PATTERNS = [
    r"PR\s*#\d+[^\n]{0,40}(remains?|is|still)\s+open",
    r"#\d+[^\n]{0,20}(仍|尚)(未|待)合并",
    r"Issue\s*#\d+[^\n]{0,30}(is\s+open|未关闭|仍开放)",
]

# After a reconciliation PR is squash-merged, current docs must stop describing
# a pending candidate/merge phase. These patterns are phase-specific on purpose:
# they never hardcode which PR number is "latest", never call the GitHub API, and
# never depend on the current date or wall clock. Historical narration that
# carries an explicit historical marker is exempt.
POST_MERGE_PHASE_DRIFT_PATTERNS = [
    r"candidate\b[^\n]{0,80}?\bbefore\s+(?:the\s+)?merge\b",
    r"\bmust\s+pass\b[^\n]{0,40}?\bbefore\s+(?:the\s+)?merge\b",
    r"awaiting\s+merge\b",
    r"latest\s+merged\s+`?main`?\s*\(PR\s*#\d+\)",
]

POST_MERGE_HISTORICAL_MARKERS = re.compile(
    r"historical|at\s+that\s+time|release\s+history|before\s+PR\s*#\d+\s+merged|"
    r"retained\s+unchanged|superseded|历史|当时|发布历史|保留不变",
    re.IGNORECASE,
)

# Qdrant IVF tuning parameters (nlist/nprobe) are not part of the implemented
# collection contract, so current docs must not present them as implemented.
# Matching is negation-aware: an affirmative implementation/tuning claim is
# flagged, while a truthful disclaimer ("nprobe is not supported") is allowed.
_QDRANT_PARAM_RE = re.compile(r"\b(?:nlist|nprobe)\b")
_QDRANT_USE_VERB_RE = re.compile(
    r"use[sd]?|using|adopt(?:s|ed)?|configure[sd]?|tun(?:e|es|ed|ing)|采用|使用|配置|设置|启用|调优",
    re.IGNORECASE,
)
_QDRANT_NEGATION_RE = re.compile(
    r"not|never|unsupported|without|no\s+support|"
    r"不支持|未|尚未|没有|不含|不使用|未声明|未配置",
    re.IGNORECASE,
)

REQUIRED_AUDIT_AREAS = {
    "Application",
    "Microservices",
    "Document parsing",
    "OCR",
    "BGE",
    "CLIP",
    "Qdrant text",
    "Qdrant image",
    "Elasticsearch",
    "Source state",
    "Incremental snapshot",
    "Carry-forward",
    "Full rebuild",
    "Validator",
    "Epoch seal",
    "CLI",
    "Scheduler",
    "Airflow integration",
    "Feedback",
    "QLoRA",
    "AdapterManager",
    "RRF",
    "BiEncoder",
    "RAGAS",
    "RBAC",
    "Cache",
    "Performance",
    "CI",
    "Security",
    "Documentation governance",
}

# Offline capabilities that now exist in code. They must never be classified as
# PLANNED/BROKEN in the current truth audit.
OFFLINE_CAPABILITY_AREAS = {
    "Document parsing",
    "OCR",
    "BGE",
    "CLIP",
    "Qdrant text",
    "Qdrant image",
    "Elasticsearch",
    "Source state",
    "Incremental snapshot",
    "Carry-forward",
    "Full rebuild",
    "Validator",
    "Epoch seal",
    "CLI",
    "Scheduler",
    "Feedback",
}


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def run_offline_subcommands() -> set[str]:
    """Return the subcommands actually defined by run_offline.py."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import run_offline

    parser = run_offline._build_parser()
    subcommands: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            subcommands.update(action.choices.keys())
    return subcommands


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
    path_pattern = re.compile(r"`((?:\.?/?[\w.-]+/)+[\w.-]+\.(?:py|md|json|ya?ml|sh|jsx|toml|txt))`")
    for reference in path_pattern.findall(text):
        normalized = reference.removeprefix("./")
        if normalized.startswith("models/") or normalized.startswith("docs/superpowers/"):
            continue  # operator-supplied assets or historical plans
        if resolve_repository_path(normalized) is None:
            fail(errors, f"{path.relative_to(ROOT)}: referenced local path does not exist: {reference}")


def check_documented_python_commands(path: Path, errors: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    command_pattern = re.compile(r"(?m)^\s*(?:\$\s*)?python(?:3|\d+(?:\.\d+)?)?\s+([\w./-]+\.py)\b")
    for entrypoint in command_pattern.findall(text):
        if not (ROOT / entrypoint).is_file():
            fail(errors, f"{path.relative_to(ROOT)}: documented Python entrypoint does not exist: {entrypoint}")


def check_documented_offline_commands(path: Path, subcommands: set[str], errors: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    for subcommand in re.findall(r"run_offline\.py\s+([a-z][a-z-]+)", text):
        if subcommand not in subcommands:
            fail(
                errors,
                f"{_display(path)}: documented run_offline.py subcommand does not exist: {subcommand}",
            )


def non_implemented_qdrant_param_claims(text: str) -> list[str]:
    """Return nlist/nprobe occurrences presented as current implementation.

    An occurrence is a claim when it sits in an affirmative use/tuning clause and
    no negation marker appears in its surrounding context.
    """
    claims: list[str] = []
    for match in _QDRANT_PARAM_RE.finditer(text):
        context = text[max(0, match.start() - 40) : match.end() + 20]
        if _QDRANT_NEGATION_RE.search(context):
            continue
        prefix = text[max(0, match.start() - 40) : match.start()]
        suffix = text[match.end() : match.end() + 20]
        affirmative_use = _QDRANT_USE_VERB_RE.search(prefix) is not None
        affirmative_tuning = re.match(r"\s*(?:[=≈~]|调优|自适应|tuning)", suffix, flags=re.IGNORECASE) is not None
        if affirmative_use or affirmative_tuning:
            claims.append(match.group(0))
    return claims


def post_merge_phase_drift_claims(text: str) -> list[str]:
    """Return reconciliation-phase claims that should have become post-merge truth.

    A claim is any line matching a phase-specific pattern that is not explicitly
    marked as historical narration. This is intentionally line-scoped and
    deterministic: it never hardcodes a specific PR number, calls the GitHub
    API, reads the clock, or inspects git history.
    """
    claims: list[str] = []
    for line in text.splitlines():
        if POST_MERGE_HISTORICAL_MARKERS.search(line):
            continue
        for pattern in POST_MERGE_PHASE_DRIFT_PATTERNS:
            match = re.search(pattern, line, flags=re.IGNORECASE)
            if match:
                claims.append(match.group(0))
                break
    return claims


def check_forbidden_current_claims(path: Path, errors: list[str]) -> None:
    """Flag superseded governance claims and non-implemented contract parameters."""
    text = path.read_text(encoding="utf-8")
    for pattern in STALE_GOVERNANCE_CLAIM_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            fail(
                errors,
                f"{_display(path)}: superseded current claim matched {pattern!r}: {match.group(0)!r}",
            )

    phase_claims = post_merge_phase_drift_claims(text)
    if phase_claims:
        fail(
            errors,
            f"{_display(path)}: reconciliation-phase wording in current docs: {sorted(set(phase_claims))}",
        )

    qdrant_claims = sorted(set(non_implemented_qdrant_param_claims(text)))
    if qdrant_claims:
        fail(
            errors,
            f"{_display(path)}: presents non-implemented Qdrant IVF parameters as current: {qdrant_claims}",
        )


def check_metrics_route_contract(errors: list[str]) -> None:
    """The metrics route is ``/api/metrics``; current docs must not claim ``GET /metrics``."""
    for name in ("docs/operations-guide.md", "docs/pre-launch-checklist.md"):
        path = ROOT / name
        if path.exists() and "/api/metrics" not in path.read_text(encoding="utf-8"):
            fail(errors, f"{name}: must document the /api/metrics route")
    for path in CANONICAL_DOCS:
        if path.exists() and re.search(r"`GET /metrics`", path.read_text(encoding="utf-8")):
            fail(errors, f"{_display(path)}: metrics route must be documented as /api/metrics")


def check_rbac_mask_contract(errors: list[str]) -> None:
    """Canonical RBAC docs must use the uint32 mask contract."""
    prd = ROOT / "PRD.md"
    if not prd.exists():
        return
    text = prd.read_text(encoding="utf-8")
    if "uint32" not in text:
        fail(errors, "PRD.md must document the uint32 RBAC mask contract")
    if re.search(r"\bint32\b", text):
        fail(errors, "PRD.md must not describe RBAC masks as int32")


def check_stale_offline_claims(path: Path, errors: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    for pattern in STALE_OFFLINE_CLAIM_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            fail(
                errors,
                f"{_display(path)}: stale offline-ingestion claim matched {pattern!r}: {match.group(0)!r}",
            )


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


def check_truth_audit(errors: list[str], audit_path: Path | None = None) -> None:
    audit_path = audit_path or (ROOT / "docs/repository-truth-audit.md")
    if not audit_path.exists():
        fail(errors, "docs/repository-truth-audit.md is missing")
        return
    audit_text = audit_path.read_text(encoding="utf-8")
    if not re.search(r"(?m)^Reconciled candidate:\s+`HEAD`(?:\s|$)", audit_text):
        fail(errors, "repository truth audit must resolve its candidate from HEAD at verification time")

    date_match = re.search(r"(?m)^Post-reconciliation verification date:\s*(\S+)\s*$", audit_text)
    if not date_match:
        fail(errors, "repository truth audit verification date is missing")
    else:
        value = date_match.group(1).rstrip(".")
        # Require the documented YYYY-MM-DD shape explicitly: date.fromisoformat()
        # also accepts compact and ISO-week forms such as 20261002 or 2026-W40-5.
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            fail(
                errors,
                f"repository truth audit verification date must be ISO YYYY-MM-DD, got {value!r}",
            )
        else:
            try:
                datetime.date.fromisoformat(value)
            except ValueError:
                fail(
                    errors,
                    f"repository truth audit verification date is not a valid calendar date: {value!r}",
                )

    audit_lines = audit_text.splitlines()
    header = next((line for line in audit_lines if line.startswith("| Area |")), "")
    columns_header = [part.strip().lower() for part in header.strip("|").split("|")]
    area_column = columns_header.index("area") if "area" in columns_header else -1
    status_column = columns_header.index("status") if "status" in columns_header else -1
    if area_column < 0 or status_column < 0:
        fail(errors, "repository truth audit must have Area and Status columns")
        return

    seen_areas: dict[str, str] = {}
    for line_number, line in enumerate(audit_lines, 1):
        if not line.startswith("|") or "---" in line or line.startswith("| Area"):
            continue
        columns = [part.strip() for part in line.strip("|").split("|")]
        if len(columns) <= max(area_column, status_column):
            fail(errors, f"repository audit line {line_number}: malformed row")
            continue
        status = columns[status_column]
        if status not in STATUSES:
            fail(errors, f"repository audit line {line_number}: invalid status {status!r}")
            continue
        seen_areas[columns[area_column]] = status

    missing_areas = REQUIRED_AUDIT_AREAS - set(seen_areas)
    if missing_areas:
        fail(errors, f"repository truth audit is missing required areas: {sorted(missing_areas)}")

    for area in sorted(OFFLINE_CAPABILITY_AREAS & set(seen_areas)):
        if seen_areas[area] in {"PLANNED", "BROKEN", "STALE"}:
            fail(
                errors, f"repository truth audit classifies existing offline capability {area!r} as {seen_areas[area]}"
            )


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

    docs_index = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    for heading in ("Canonical / Current", "Historical / Implementation Plans"):
        if heading not in docs_index:
            fail(errors, f"docs/README.md must separate current and historical documentation ({heading})")

    check_truth_audit(errors)
    check_metrics_route_contract(errors)
    check_rbac_mask_contract(errors)

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
            check_forbidden_current_claims(doc, errors)

    subcommands = run_offline_subcommands()
    for doc in CURRENT_OFFLINE_DOCS:
        if doc.exists():
            check_documented_offline_commands(doc, subcommands, errors)
            check_stale_offline_claims(doc, errors)

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
