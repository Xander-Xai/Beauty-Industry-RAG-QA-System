#!/usr/bin/env python3
"""Guard the boundary between a public engineering repository and private material.

``check_repo_consistency.py`` answers "do the docs still agree with the code?".
This script answers a different question: **"is anything in this repository that
belongs to a person's career, a prior employer's operations, or an unlicensed
data asset still reachable in the working tree?"** The two concerns are kept in
separate scripts on purpose — a single 2000-line guard that fails for unrelated
reasons is one nobody runs.

What it checks
--------------

1. **Career material** — file names and content that would turn an engineering
   repository into a job-application artefact (résumé claims, interview guides,
   recruiter-facing material, employment timelines).
2. **Third-party operational identity** — prior-organization product names,
   internal project codenames, serving hardware models and business-scale figures.
3. **High-risk credential shapes** — tokens, keys and connection strings, with
   explicit allow-listing for the placeholders this repository legitimately ships.
4. **Data declaration presence** — ``DATA_PROVENANCE.md``, ``DATA_MANIFEST.json``
   and the links that make them discoverable from the README and the docs index.
5. **Data provenance consistency** — every tracked data file is classified in
   the manifest, every classified path exists, and every synthetic asset carries
   the    marker that tells a reader the contents are fabricated.
6. **Fabricated regulatory authority** — a synthetic source that pairs a real
   regulatory instrument or registry number with a limit-shaped value.
7. **Broken markdown references** in the boundary documents themselves.

What it deliberately does not do
--------------------------------

It never flags normal engineering vocabulary. ``Query Rewrite``, ``Candidate
Ranking``, ``Checkpoint Resume``, ``rerank candidates`` and ``resume the
ingestion run`` are product terms, not career claims, and the patterns below are
built so those survive. It also never asserts that the repository is free of
anything: it can only report what is present in the files it can read. The
history caveat is documented in ``DATA_PROVENANCE.md`` §6 rather than papered over
here, because no working-tree scan can see a deleted blob.

Check 6 is scoped to *generator* sources on purpose. The boundary documents
themselves name real instruments on purpose, because the disclosure has to say
which families of content are fabricated; a repository-wide version of that
check would either fire on the disclosure or need an allow-list wide enough to
defeat itself.

The checks are deterministic and read the working tree through ``git ls-files``,
so they never depend on wall-clock state or on a GitHub API.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------
# Scope
# --------------------------------------------------------------------------

#: Directories whose contents are never scanned for prose. The manifest and the
#: guard itself must be able to *name* the patterns they forbid, so they are
#: skipped rather than special-cased.
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".ruff_cache", ".pytest_cache"})

#: Documentation that is allowed to quote forbidden vocabulary in order to state
#: the boundary. ``DATA_PROVENANCE.md`` names the audit areas on purpose.
_BOUNDARY_POLICY_FILES = frozenset(
    {
        "DATA_PROVENANCE.md",
        "DATA_MANIFEST.json",
        "scripts/check_public_repository_boundary.py",
        "tests/test_check_public_repository_boundary.py",
    }
)

#: Documents whose job is to *report* career material rather than contain any:
#: the audit backlog names what was removed and why it needs review, exactly as
#: the provenance statement does. They are exempt from the two career checks
#: only — the credential, third-party identity, fabricated-authority, manifest
#: and link checks still read them, so a leaked key in either would still fail.
_CAREER_POLICY_FILES = _BOUNDARY_POLICY_FILES | frozenset({"docs/finalization/DATA_REVIEW_BACKLOG.md"})

#: Files that legitimately carry long lists of literal identifiers: lockfiles pin
#: package names, and the repo's own guard tests assert on pattern text.
_TEXT_EXEMPT_SUFFIXES = (".lock", ".jsonl")

_CAREER_FILENAME_PATTERNS = (
    # Underscore is a regex word character, so `RESUME_CLAIMS` has no boundary
    # after `RESUME`; the explicit separator class is what matches it.
    re.compile(r"(?i)RESUME(?:[_ .-]|$)"),
    re.compile(r"(?i)\bCV\b"),
    re.compile(r"(?i)INTERVIEW[_ -]?(GUIDE|PREPP?|NOTES?|QUESTIONS?)?"),
    re.compile(r"(?i)\bCOVER[_ -]?LETTER\b"),
    re.compile(r"(?i)\bJOB[_ -]?(APPLICATION|DESCRIPTION|DESC)\b"),
    re.compile(r"(?i)\b(RECRUIT|CANDIDATE[_ -]?(POOL|LIST|TRACKER))\b"),
    re.compile(r"(?i)\bPERFORMANCE[_ -]?REVIEW\b"),
)

#: Content that would describe the repository as a job artefact or bind it to a
#: personal employment history. Each entry is (pattern, why-it-matters).
_CAREER_CONTENT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"简历"), "résumé material"),
    (re.compile(r"求职"), "job-seeking material"),
    (re.compile(r"面试(?:官|题|准备|话术|复现)"), "interview material"),
    (re.compile(r"面试指南"), "interview material"),
    (re.compile(r"(?:面向|用于)(?:招聘|求职|应聘)"), "recruiting-facing material"),
    (re.compile(r"候选人(?:筛选|评估|画像)"), "recruiting material"),
    (re.compile(r"离职(?:后|时间|前)?"), "employment timeline"),
    (re.compile(r"前雇主"), "prior-employer reference"),
    (re.compile(r"上一家公司"), "prior-employer reference"),
    (re.compile(r"(?:任职|在职)期间"), "employment timeline"),
    (re.compile(r"(?i)\bpost[- ]employment\b"), "employment framing"),
    (re.compile(r"(?i)\bafter leaving (?:the |my |their )?(?:company|employer)\b"), "employment timeline"),
    (re.compile(r"(?i)\b(?:company|employer) project experience\b"), "employment framing"),
    (re.compile(r"(?i)\bformer employer\b"), "prior-employer reference"),
    (re.compile(r"(?i)\bopen[- ]source (?:evolution|derivative) of\b"), "employer-project framing"),
    (
        re.compile(r"(?i)\bindependent reproduction of (?:an|the) (?:enterprise|company|former)"),
        "employer-project framing",
    ),
    (re.compile(r"(?i)\benterprise project experience\b"), "employment framing"),
    (re.compile(r"(?i)\bjob application\b"), "job-seeking material"),
    (re.compile(r"(?i)\brecruiter[- ]facing\b"), "recruiting-facing material"),
)

#: Names of prior organizations or their internal product codenames. Empty by
#: design in the committed state; kept as a table so the guard is extensible and
#: so a reviewer can see at a glance what is being watched for.
_THIRD_PARTY_IDENTITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"药妆助手"),
    re.compile(r"(?i)\bl'[oô]r[ée]al\b"),
    re.compile(r"(?i)\bunilever\b"),
    re.compile(r"(?i)\bbeiersdorf\b"),
    re.compile(r"(?i)\bprocter\s*(?:&\s*gamble)?\b"),
    re.compile(r"(?i)\best[ée]e\s*lauder\b"),
    re.compile(r"(?i)\bshiseido\b"),
)

#: Production serving hardware tied to a prior deployment. A generic "dual GPU"
#: design target is allowed — see ``_ALLOWED_TECHNICAL_CONTEXT``.
_HARDWARE_FACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bRTX\s*A5000\b"),
    re.compile(r"(?i)\bA5000\b"),
)

#: Business-scale figures of the shape a prior production environment would have.
#: Each entry is (pattern, label). Deliberately narrow: a design target such as
#: the PRD's 500-document baseline and the SLO thresholds must survive.
_BUSINESS_SCALE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"3000\+?\s*(?:份|个)?\s*(?:内部)?文档"), "prior-organization corpus size"),
    (re.compile(r"5000\+?\s*(?:份|个)?\s*图片"), "prior-organization corpus size"),
    (re.compile(r"1500\+?\s*(?:份|个)?\s*产品"), "prior-organization corpus size"),
    (re.compile(r"2000\+?\s*(?:种|个)?\s*成分"), "prior-organization corpus size"),
    (re.compile(r"(?:200\+?\s*(?:名|位)?\s*内部用户)|(?:内部用户[^。\n]{0,8}200\+?)"), "prior-organization user count"),
    (re.compile(r"日均\s*1500\+?\s*请求"), "prior-organization traffic volume"),
    (
        re.compile(r"(?:高峰[^。\n]{0,12})?(?:10\s*[-–—]\s*15|10\s*[—–-]\s*15)\s*QPS", re.IGNORECASE),
        "prior-organization traffic volume",
    ),
    (re.compile(r"(?:年度|技术)?创新奖"), "internal recognition"),
    (re.compile(r"(?i)\bQwen2\.5\s*(?:→|->|to)\s*Qwen3\b"), "prior-organization model migration history"),
    # A tunable value in *this* repository attributed to someone else's
    # production runs. The value itself may be generic; the provenance is the
    # third-party work result, so the pattern targets the attribution.
    (
        re.compile(
            r"(?:数值|权重|阈值|参数|系数|超参|w_[a-z_]+)[^。\n]{0,40}?来自[^。\n]{0,20}?(?:线上|生产|客户|内网)"
        ),
        "tuning provenance attributed to prior production",
    ),
    (
        re.compile(r"(?i)derived from (?:live |production |customer )?(?:experiments?|traffic|a/?b test)"),
        "tuning provenance attributed to prior production",
    ),
)

#: Phrases that make an otherwise ambiguous identifier a *product* claim rather
#: than a career claim. Matched against the whole line, case-insensitively.
_ALLOWED_TECHNICAL_CONTEXT = re.compile(
    r"(?i)"
    r"query\s*rewrite|rewrite\s*path|candidate\s*rank|rerank|recall_candidate"
    r"|checkpoint\s*resume|resume\s+(?:the\s+)?(?:run|ingestion|job|pipeline|replay)"
    r"|candidate\s*(?:document|set|result|user|pass|row|label|mapping|id)"
    r"|regulatory|regulation|ingredient|formula|dosage|labeling"
)

# --------------------------------------------------------------------------
# Credential shapes
# --------------------------------------------------------------------------

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key id"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"), "GitHub token"),
    (re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"sk-[A-Za-z0-9]{32,}"), "OpenAI-style API key"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "Google API key"),
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"), "private key block"),
    (
        re.compile(
            r"(?i)\b(?:aws_secret_access_key|api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"][^'\"\s]{16,}['\"]"
        ),
        "hardcoded credential assignment",
    ),
    (
        re.compile(r"(?i)\b(?:postgres|postgresql|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s:@/]+:[^\s:@/]+@"),
        "connection string with inline password",
    ),
)

#: Values that look like credentials but are the repository's own placeholders.
_SECRET_ALLOWLIST = re.compile(
    r"(?i)^(?:\s*|[\"']?)(?:"
    r"REPLACE[_A-Z]*|CHANGE[_A-Z]*|YOUR[_A-Z]*|EXAMPLE|DUMMY|PLACEHOLDER|SAMPLE|TODO|XXX+"
    r"|ci-only[\w-]*|ci-test[\w-]*|ci-[a-z-]*"
    r"|not-a-secret|changeme|secret|password|token"
    r"|<[^>]+>|\$\{[^}]+\}|\{\{[^}]+\}\}"
    r"|\d{4}-\d{2}-\d{2}T[\d:.+\-Z]+"
    r")$"
)

#: Files whose job is to hold placeholders or to describe credential handling.
_SECRET_EXEMPT_FILES = frozenset({".env.example", "SECURITY.md", "DATA_PROVENANCE.md"})

# --------------------------------------------------------------------------
# Data provenance contract
# --------------------------------------------------------------------------

DATA_PROVENANCE_DOC = "DATA_PROVENANCE.md"
DATA_MANIFEST = "DATA_MANIFEST.json"

#: Extensions that make a tracked file a *data asset* rather than source or
#: documentation. Matching on extension rather than on a directory list is
#: deliberate: a fixture added under a new directory (`tests/integration/
#: testdata/`, a future `benchmarks/fixtures/`) must be forced to declare
#: itself, otherwise the classification requirement is opt-in and therefore
#: optional.
_DATA_SUFFIXES = frozenset({".json", ".jsonl", ".csv", ".tsv"})

#: Tracked data-extension files that carry configuration or dependency
#: metadata, not an information asset. Each is listed explicitly so adding one
#: is a reviewable act rather than a silent hole in the scan.
_NON_DATA_FILES = frozenset(
    {
        "config.json",
        "DATA_MANIFEST.json",
        "frontend/package.json",
        "frontend/package-lock.json",
        "monitoring/grafana/dashboards/rag-overview.json",
    }
)

#: Every classified asset must be discoverable from the human-readable document.
_PROVENANCE_LINK_TARGETS = (DATA_PROVENANCE_DOC, DATA_MANIFEST)


# --------------------------------------------------------------------------
# Tree helpers
# --------------------------------------------------------------------------


def tracked_files(root: Path | None = None) -> list[Path]:
    """Return Git-tracked files, falling back to a filesystem walk.

    The fallback keeps the guard usable in an unpacked archive or a shallow copy
    where Git metadata is absent; it scans the same directory exclusions.
    """
    base = root or ROOT
    try:
        raw = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=base,
            check=True,
            capture_output=True,
        ).stdout
        names = [name for name in raw.decode("utf-8").split("\0") if name]
        return [base / name for name in names]
    except (OSError, subprocess.CalledProcessError):
        collected: list[Path] = []
        for path in base.rglob("*"):
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if path.is_file() and ".git" not in path.parts:
                collected.append(path)
        return sorted(collected)


def relative(path: Path, root: Path | None = None) -> str:
    base = root or ROOT
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def is_scannable(path: Path, root: Path | None = None) -> bool:
    """True when *path* should be read as text and checked for prose."""
    name = relative(path, root)
    if name in _BOUNDARY_POLICY_FILES:
        return False
    if path.suffix in _TEXT_EXEMPT_SUFFIXES:
        return False
    return not any(part in _SKIP_DIRS for part in path.parts)


def read_text(path: Path) -> str | None:
    """Read a file as UTF-8 text, or return None when it is binary."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# --------------------------------------------------------------------------
# Check 1 — career material
# --------------------------------------------------------------------------


def career_path_violations(paths: Iterable[Path], root: Path | None = None) -> list[str]:
    """Return career-artefact filenames that are still present in the tree."""
    violations: list[str] = []
    for path in paths:
        name = relative(path, root)
        if name in _CAREER_POLICY_FILES:
            continue
        stem = path.stem
        for pattern in _CAREER_FILENAME_PATTERNS:
            if pattern.search(stem) or pattern.search(path.name):
                violations.append(f"{name}: career-material filename ({pattern.pattern})")
                break
    return violations


def career_content_violations(paths: Iterable[Path], root: Path | None = None) -> list[str]:
    """Return prose that frames the repository as career or recruiting material.

    A line is only reported when it trips a career pattern *and* is not
    recognizably a product/engineering statement, which is what keeps
    "Candidate Ranking" and "resume the ingestion run" out of the findings.
    """
    violations: list[str] = []
    for path in paths:
        if not is_scannable(path, root):
            continue
        name = relative(path, root)
        if name in _CAREER_POLICY_FILES:
            continue
        text = read_text(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if len(line) > 2000:
                continue
            if _ALLOWED_TECHNICAL_CONTEXT.search(line):
                continue
            for pattern, label in _CAREER_CONTENT_PATTERNS:
                if pattern.search(line):
                    violations.append(f"{name}:{lineno}: {label}")
                    break
    return violations


# --------------------------------------------------------------------------
# Check 2 — third-party operational identity
# --------------------------------------------------------------------------


def third_party_identity_violations(paths: Iterable[Path], root: Path | None = None) -> list[str]:
    """Return prior-organization identity, hardware and scale facts."""
    violations: list[str] = []
    for path in paths:
        if not is_scannable(path, root):
            continue
        text = read_text(path)
        if text is None:
            continue
        name = relative(path, root)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if len(line) > 2000:
                continue
            if _ALLOWED_TECHNICAL_CONTEXT.search(line):
                continue
            for pattern in _THIRD_PARTY_IDENTITY_PATTERNS:
                if pattern.search(line):
                    violations.append(f"{name}:{lineno}: prior-organization identity ({pattern.pattern})")
                    break
            for pattern in _HARDWARE_FACT_PATTERNS:
                if pattern.search(line):
                    violations.append(f"{name}:{lineno}: production serving hardware fact ({pattern.pattern})")
                    break
            for pattern, label in _BUSINESS_SCALE_PATTERNS:
                if pattern.search(line):
                    violations.append(f"{name}:{lineno}: {label} ({pattern.pattern})")
    return violations


# --------------------------------------------------------------------------
# Check 3 — high-risk credential shapes
# --------------------------------------------------------------------------


def _is_allowed_secret_value(value: str) -> bool:
    return bool(_SECRET_ALLOWLIST.match(value.strip()))


def secret_violations(paths: Iterable[Path], root: Path | None = None) -> list[str]:
    """Return credential-shaped literals that are not recognised placeholders."""
    violations: list[str] = []
    for path in paths:
        if not is_scannable(path, root):
            continue
        text = read_text(path)
        if text is None:
            continue
        name = relative(path, root)
        exempt = name in _SECRET_EXEMPT_FILES
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern, label in _SECRET_PATTERNS:
                match = pattern.search(line)
                if not match:
                    continue
                if exempt and _is_allowed_secret_value(match.group(0)):
                    continue
                violations.append(f"{name}:{lineno}: {label}")
    return violations


# --------------------------------------------------------------------------
# Check 4 — data declaration presence
# --------------------------------------------------------------------------


def data_declaration_errors(root: Path | None = None) -> list[str]:
    """Return missing boundary declarations and undiscoverable links."""
    base = root or ROOT
    errors: list[str] = []

    provenance = base / DATA_PROVENANCE_DOC
    if not provenance.is_file():
        errors.append(f"{DATA_PROVENANCE_DOC} is missing; the repository must ship a data-provenance statement")
    else:
        text = provenance.read_text(encoding="utf-8")
        # A disclaimer is not a substitute for an audit: the document has to
        # state both what it promises and what was actually checked.
        for required in ("非关联声明", "synthetic", "审计"):
            if required.lower() not in text.lower():
                errors.append(f"{DATA_PROVENANCE_DOC}: missing required section marker {required!r}")

    if not (base / DATA_MANIFEST).is_file():
        errors.append(f"{DATA_MANIFEST} is missing; data assets cannot be machine-verified without it")

    readme = base / "README.md"
    if readme.is_file() and DATA_PROVENANCE_DOC not in readme.read_text(encoding="utf-8"):
        errors.append("README.md does not link to the data-provenance statement")

    docs_index = base / "docs/README.md"
    if docs_index.is_file() and DATA_PROVENANCE_DOC not in docs_index.read_text(encoding="utf-8"):
        errors.append("docs/README.md does not link to the data-provenance statement")

    return errors


# --------------------------------------------------------------------------
# Check 5 — data provenance consistency
# --------------------------------------------------------------------------


def _load_manifest(root: Path) -> dict:
    return json.loads((root / DATA_MANIFEST).read_text(encoding="utf-8"))


def manifest_errors(
    paths: Sequence[Path],
    root: Path | None = None,
    manifest: dict | None = None,
) -> list[str]:
    """Return drift between the manifest, the working tree and the statement.

    Three directions are checked, because each one fails silently on its own:
    a classified path that no longer exists, a new data file nobody classified,
    and a synthetic asset whose marker was dropped.
    """
    base = root or ROOT
    errors: list[str] = []
    if manifest is None:
        try:
            manifest = _load_manifest(base)
        except (OSError, json.JSONDecodeError) as exc:
            return [f"{DATA_MANIFEST} is unreadable: {exc}"]

    classes = manifest.get("classes") or {}
    assets = manifest.get("assets") or []
    if not assets:
        return [f"{DATA_MANIFEST}: no assets declared"]

    declared: set[str] = set()
    for asset in assets:
        asset_path = asset.get("path")
        asset_class = asset.get("class")
        if not asset_path or not asset_class:
            errors.append(f"{DATA_MANIFEST}: asset entry missing 'path' or 'class': {asset!r}")
            continue
        declared.add(asset_path)
        if asset_class not in classes:
            errors.append(f"{DATA_MANIFEST}: {asset_path} uses undeclared class {asset_class!r}")
        if not (base / asset_path).is_file():
            errors.append(f"{DATA_MANIFEST}: {asset_path} is declared but missing from the working tree")

        marker = asset.get("marker")
        if asset_class == "SYNTHETIC":
            # The marker is the whole point of the class. An entry that omits
            # it declares "this is fabricated" without telling any reader, so
            # absence is a finding rather than a neutral default.
            if not marker:
                errors.append(
                    f"{DATA_MANIFEST}: {asset_path} is class SYNTHETIC but declares no 'marker'; "
                    "a synthetic asset must name the file, sidecar or schema that discloses it"
                )
            else:
                errors.extend(_marker_errors(base, asset_path, marker, asset))
        elif marker:
            errors.extend(_marker_errors(base, asset_path, marker, asset))

    provenance_path = base / DATA_PROVENANCE_DOC
    provenance_text = provenance_path.read_text(encoding="utf-8") if provenance_path.is_file() else ""
    for asset_path in sorted(declared):
        if asset_path not in provenance_text:
            errors.append(f"{DATA_PROVENANCE_DOC}: {asset_path} is not listed in the human-readable inventory")

    tracked_names = {relative(p, base) for p in paths}
    for candidate in sorted(_expected_data_files(tracked_names)):
        if candidate not in declared:
            errors.append(f"{candidate} is a tracked data file with no entry in {DATA_MANIFEST}")

    return errors


def _marker_errors(base: Path, asset_path: str, marker: str, asset: dict) -> list[str]:
    """Return errors for a missing or unrecognised synthetic-data marker."""
    errors: list[str] = []
    if marker.startswith("sidecar:"):
        sidecar = base / marker.removeprefix("sidecar:")
        if not sidecar.is_file():
            errors.append(f"{asset_path}: synthetic marker points at a missing sidecar ({marker})")
            return errors
        text = sidecar.read_text(encoding="utf-8").lower()
        if "虚构" not in text and "synthetic" not in text:
            errors.append(f"{sidecar.relative_to(base)}: sidecar does not state that its data is synthetic")
        return errors
    if marker.startswith("schema:"):
        if not (base / marker.removeprefix("schema:")).is_file():
            errors.append(f"{asset_path}: marker points at a missing schema ({marker})")
        return errors
    # Inline marker: the file itself must carry the phrase.
    target = base / asset_path
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        errors.append(f"{asset_path}: inline synthetic marker cannot be read as UTF-8 text")
        return errors
    for needle in asset.get("marker_must_contain") or []:
        if needle.lower() not in text.lower():
            errors.append(f"{asset_path}: missing required synthetic marker phrase {needle!r}")
    return errors


def _expected_data_files(tracked_names: set[str]) -> set[str]:
    """Return tracked files that look like data assets and need classifying."""
    return {name for name in tracked_names if Path(name).suffix in _DATA_SUFFIXES and name not in _NON_DATA_FILES}


# --------------------------------------------------------------------------
# Check 6 — fabricated regulatory authority
# --------------------------------------------------------------------------

#: Synthetic sources: files whose job is to *manufacture* regulatory-shaped
#: fixtures. This is deliberately a narrow scope. The repository also documents
#: real regulatory vocabulary in prose (``DATA_PROVENANCE.md``, the backlog, the
#: boundary docs), and a repository-wide version of this check would either fire
#: on those or need an allow-list wide enough to defeat itself. What must never
#: happen is a *generator* emitting a value that reads as an authority.
_SYNTHETIC_SOURCE_PATTERNS = (
    re.compile(r"(?i)(?:^|/)mock_generator\.py$"),
    re.compile(r"(?i)(?:^|/)(?:mock|synthetic|fixture)[a-z_]*\.py$"),
    re.compile(r"(?i)(?:^|/)seed_[a-z_]*\.py$"),
)

#: Real instruments, issuing bodies and registries. Present in the prose of the
#: boundary documents on purpose; the check only looks where a generated value
#: would inherit the authority.
_REGULATORY_AUTHORITY_PATTERNS = (
    re.compile(r"\bGB\s*/?\s*T\b"),
    re.compile(r"\bQB\s*/?\s*T\b"),
    re.compile(r"\bNMPA\b"),
    re.compile(r"\bHFJB\b"),
    re.compile(r"\bGB\s*4806\b"),
    re.compile(r"\bGB\s*\d{4,5}(?:\.\d+)?\b"),
    re.compile(r"\bISO\s*\d{3,5}\b"),
    re.compile(r"\bFDA\b"),
    re.compile(r"\bEU\s*1223/2009\b"),
    re.compile(r"化妆品安全技术规范"),
    re.compile(r"化妆品监督管理条例"),
    re.compile(r"化妆品标签管理办法"),
    re.compile(r"化妆品功效宣称评价规范"),
    re.compile(r"化妆品生产质量管理规范"),
    # A registry number is an authority too: it is the lookup key that turns a
    # fictional limit into something a reader can resolve. ``000-00-0`` is the
    # reserved placeholder this repository uses instead, so it is excluded.
    re.compile(r"\b(?!000-00-0)\d{2,7}-\d{2}-\d\b"),
)

#: A limit-shaped value. The bare CAS shape is included because attaching a
#: registry number to a fictional limit is the same failure at a different
#: layer: the reader gets a lookup key that resolves to something real.
_LIMIT_VALUE_PATTERN = re.compile(
    r"(?:不超过|不得大于|≤|<=)\s*[\d.]+\s*(?:%|mg\s*/\s*kg|ppm|CFU)"
    r"|\b\d+(?:\.\d+)?\s*(?:%|mg\s*/\s*kg|ppm)\b"
    r"|\b(?!000-00-0)\d{2,7}-\d{2}-\d\b"
)

#: A line that discloses itself as synthetic is allowed to name an instrument —
#: that is what a deprecation notice or a compliance checklist has to do.
_SYNTHETIC_DISCLOSURE_PATTERN = re.compile(
    r"(?i)虚构|合成|placeholder|synthetic|not\s+authoritative|非真实|示例值|杜撰"
)


def _is_synthetic_source(name: str) -> bool:
    return any(pattern.search(name) for pattern in _SYNTHETIC_SOURCE_PATTERNS)


def fabricated_regulatory_authority_violations(paths: Iterable[Path], root: Path | None = None) -> list[str]:
    """Return synthetic sources that attach an authority to a fabricated value.

    The shape being caught is the hybrid the provenance statement rules out: a
    real instrument, registry or standard number sitting next to a made-up
    limit. Correcting such a line to a real value would require a source and a
    licence, so the fix is to invent an identifier that resolves to nothing —
    which is what ``data/mock_generator.py`` now does.
    """
    violations: list[str] = []
    for path in paths:
        if not is_scannable(path, root):
            continue
        name = relative(path, root)
        if not _is_synthetic_source(name):
            continue
        text = read_text(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if len(line) > 2000:
                continue
            if _SYNTHETIC_DISCLOSURE_PATTERN.search(line):
                continue
            if not any(pattern.search(line) for pattern in _REGULATORY_AUTHORITY_PATTERNS):
                continue
            if _LIMIT_VALUE_PATTERN.search(line):
                violations.append(
                    f"{name}:{lineno}: fabricated regulatory authority "
                    "(real instrument or registry number paired with a limit-shaped value)"
                )
    return violations


# --------------------------------------------------------------------------
# Check 7 — markdown references in the boundary documents
# --------------------------------------------------------------------------

_MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
_ANCHOR = re.compile(r"^L\d+(?:-L\d+)?$", re.IGNORECASE)


def broken_markdown_references(paths: Iterable[Path], root: Path | None = None) -> list[str]:
    """Return local links in the boundary documents that do not resolve.

    Only the provenance-facing documents are scanned. Repository-wide link
    checking already belongs to ``check_repo_consistency.py``; duplicating it
    here would make the two guards disagree about what a valid tree is.
    """
    base = root or ROOT
    errors: list[str] = []
    targets = [base / name for name in _PROVENANCE_LINK_TARGETS]
    targets += [base / "README.md", base / "docs" / "README.md"]
    for path in targets:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for target in _MARKDOWN_LINK.findall(text):
            target = target.strip().split()[0].strip("<>")
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            local_target = target.split("#", 1)[0].split("?", 1)[0]
            if not local_target:
                continue
            if not (path.parent / local_target).resolve().exists():
                errors.append(f"{relative(path, base)}: broken local link {target}")
    return errors


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def run_checks(root: Path | None = None) -> list[str]:
    """Return every boundary violation found in the working tree."""
    base = root or ROOT
    paths = tracked_files(base)
    manifest: dict | None = None
    manifest_path = base / DATA_MANIFEST
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = None

    return [
        *career_path_violations(paths, base),
        *career_content_violations(paths, base),
        *third_party_identity_violations(paths, base),
        *secret_violations(paths, base),
        *fabricated_regulatory_authority_violations(paths, base),
        *data_declaration_errors(base),
        *manifest_errors(paths, base, manifest),
        *broken_markdown_references(paths, base),
    ]


def list_datasets(root: Path | None = None) -> list[str]:
    """Return the tracked data files the guard considers unclassified if absent."""
    base = root or ROOT
    return sorted(_expected_data_files({relative(p, base) for p in tracked_files(base)}))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Guard the public repository boundary (career material, third-party identity, credentials, data provenance).",
    )
    parser.add_argument(
        "--list-datasets",
        action="store_true",
        help="print the tracked data files that must appear in DATA_MANIFEST.json and exit",
    )
    args = parser.parse_args()

    if args.list_datasets:
        for name in list_datasets():
            print(name)
        return 0

    errors = run_checks()
    if errors:
        print(f"Public repository boundary check failed ({len(errors)} finding(s)):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        print(
            "\nIf a finding is a false positive, teach the guard the technical term instead of "
            "deleting the engineering content. See DATA_PROVENANCE.md.",
            file=sys.stderr,
        )
        return 1

    print("Public repository boundary check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
