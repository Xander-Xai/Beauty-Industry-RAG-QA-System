#!/usr/bin/env python3
"""Check repository metadata, links, and drift-prone documentation claims.

This guard is intentionally conservative: it fails on broken local references,
runtime-artifact leaks, version drift, documented-but-missing offline CLI
subcommands, stale "offline ingestion is missing" claims in current operator
docs, superseded governance/contract claims in canonical docs,
post-merge reconciliation-phase wording (pending candidate / awaiting merge /
stale latest-merged-main references), current docs that still present the
retired dual-4B topology, and an invalid/absent repository truth audit. It does
not flag historical CHANGELOG text or historical implementation plans.

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

# After a reconciliation PR is merged, current docs must stop describing a
# pending candidate/merge phase. These patterns are phase-specific on purpose:
# they never hardcode which PR number is "latest", never call the GitHub API,
# and never depend on the current date or wall clock. Historical narration that
# carries an explicit historical marker is exempt.
POST_MERGE_PHASE_DRIFT_PATTERNS = [
    r"candidate\b[^\n]{0,80}?\bbefore\s+(?:the\s+)?merge\b",
    r"\bmust\s+pass\b[^\n]{0,40}?\bbefore\s+(?:the\s+)?merge\b",
    r"awaiting\s+merge\b",
    r"latest\s+merged\s+`?main`?\s*\(PR\s*#\d+\)",
]

# The runtime consolidated the former vLLM-Rewrite / vLLM-Gen-4B instances into
# a single shared 4B endpoint (`vllm_4b`). Current canonical docs must not
# present the two-instance topology as the current implementation. Lines that
# explicitly mark themselves as historical/target design are exempt.
CONSOLIDATED_4B_DRIFT_PATTERNS = [
    r"vLLM-(?:Rewrite|Gen-4B)",
    r"vllm[-_](?:rewrite|gen[-_]4b)",
]

HISTORICAL_MARKERS = re.compile(
    r"historical|target\s+design|at\s+that\s+time|release\s+history|"
    r"before\s+PR\s*#\d+\s+merged|retained\s+unchanged|superseded|reconciliation|"
    r"retired|obsolete|deprecated|no\s+longer|no\s+separate|removed|dropped|"
    r"历史|当时|发布历史|保留不变|目标设计|原设计|已移除",
    re.IGNORECASE,
)

# RAGAS is an isolated, optional evaluator. After the correctness work merged via
# PR #14, a missing evaluator dependency or credential must fail fast with a
# non-zero exit code and must not write a quality report. The retired
# "missing-RAGAS returns a zero score plus `_warning`" fallback must never be
# presented as the current canonical behavior. Matching is negation-aware so a
# truthful denial ("never emit a zero-score report") stays allowed, and lines
# carrying an explicit historical marker are exempt.
RAGAS_REQUIRED_DOCS = [
    "README.md",
    "docs/ragas-evaluation-guide.md",
    "docs/interview-architecture-baseline.md",
]

# Historical exemption for the RAGAS zero-fallback scanner. Deliberately
# narrower than HISTORICAL_MARKERS: ambiguous transition words such as
# "no longer" or "dropped" can appear in a current-state sentence and must not
# suppress the guard.
RAGAS_HISTORICAL_MARKERS = re.compile(
    r"historical|target\s+design|at\s+that\s+time|release\s+history|"
    r"before\s+PR\s*#\d+\s+merged|retained\s+unchanged|superseded|"
    r"retired|obsolete|deprecated|"
    r"历史|当时|发布历史|保留不变|目标设计|原设计",
    re.IGNORECASE,
)

RAGAS_ZERO_FALLBACK_CLAIM_PATTERNS = [
    r"返回零分",
    r"生成零分",
    r"零分\s*\+?\s*[`_]?\s*warning",
    r"[`_]warning[`_]?[^\n]{0,8}零分",
    r"returns?\s+(?:all\s+)?zero(?:\s+score[s]?)?",
    r"return(?:s|ing)?\s+(?:an?\s+)?zero",
    r"zero(?:\s+score[s]?)?\s+(?:when|if)\s+ragas\s+(?:is\s+)?(?:unavailable|missing|not\s+installed)",
    r"all[- ]zero\s+ragas\s+fallback",
    r"ragas\s+fallback[^\n]{0,12}(?:zero|全\s*0|零)",
]

# Explicit denial of the fallback *action*. This is deliberately narrow: a bare
# "不"/"not" is NOT a denial, because it usually negates the *condition*
# ("RAGAS 不可用时...", "RAGAS is not installed...") rather than the fallback.
RAGAS_ZERO_FALLBACK_DENIAL_PATTERNS = [
    r"(?:不会|不再|绝不|从未|未曾|没能|没有|不)\s*(?:再)?\s*(?:返回|生成|产生|输出|写出|写)\s*零",
    r"never\s+(?:returns?|emits?|produces?|writes?)\b[^\n]{0,24}zero",
    r"does\s+not\s+(?:return|emit|produce|write)\b[^\n]{0,24}zero",
    r"doesn't\s+(?:return|emit|produce|write)\b[^\n]{0,24}zero",
    r"no\s+longer\s+(?:returns?|emits?|produces?|writes?)\b[^\n]{0,24}zero",
]

# Two independent RAGAS failure contracts. They must each be documented, and a
# bare "UNAVAILABLE" must not satisfy the non-zero/failure-status half.
RAGAS_FAILURE_STATUS_REQUIRED_RE = re.compile(
    r"fail[- ]?fast|非\s*0\s*退出|非零退出|非零状态|non-?zero(?:\s+(?:status|exit))?|退出码\s*[2-5]",
    re.IGNORECASE,
)
RAGAS_NO_REPORT_REQUIRED_RE = re.compile(
    r"不生成[^\n]{0,16}(?:质量)?(?:报告|report)|不写[^\n]{0,8}(?:报告|report)|"
    r"no\s+quality\s+report|never\s+(?:write|emit|produce)[^\n]{0,20}report|"
    r"does\s+not\s+produce[^\n]{0,24}report",
    re.IGNORECASE,
)

# Only lines that describe an unavailable/failed evaluator count as the failure
# clause. Both guarantees must appear on such lines, so an unrelated RAGAS line
# (for example a benchmark report mention) cannot satisfy either half.
RAGAS_FAILURE_CONDITION_RE = re.compile(
    r"unavailable|missing|not\s+installed|缺少|缺失|不可用|未安装|fail[- ]?fast|非零|退出码\s*[2-5]",
    re.IGNORECASE,
)

# The v2.5 runtime/security work completed local validation against real Redis,
# real nginx, authenticated Elasticsearch and authenticated Prometheus. Canonical
# docs must classify that as LOCAL_REAL_VALIDATION and must not regress to
# "never actually validated" wording. Production cluster/HA/SLO and other
# external topologies are still out of scope and remain valid to mark as
# unverified, so lines that explicitly describe such a boundary are exempt.
LOCAL_VALIDATION_DOCS = [
    "docs/interview-architecture-baseline.md",
    "docs/repository-truth-audit.md",
]

LOCAL_VALIDATION_EXTERNAL_BOUNDARY_RE = re.compile(
    r"Cluster|Sentinel|production|生产|HA\b|SLO\b|cloud|云|load\s+balancer|\bLB\b|"
    r"multi-?node|多节点|TLS|long[- ]run|长期|Grafana",
    re.IGNORECASE,
)

STALE_LOCAL_VALIDATION_CLAIM_PATTERNS = [
    r"(?:Redis|反向代理|代理|Elasticsearch|Prometheus|(?<![A-Za-z])ES(?![A-Za-z]))[^\n]{0,24}"
    r"(?:尚待|仍待|仍需|尚未|还未|未[^\n]{0,6}(?:验证|验收))",
    r"(?:尚待|仍待|仍需|尚未|还未)[^\n]{0,16}"
    r"(?:Redis|反向代理|Elasticsearch|Prometheus)[^\n]{0,16}(?:验收|验证)",
    r"(?:Redis|proxy|Elasticsearch|Prometheus)[^\n]{0,40}"
    r"not\s+(?:yet\s+)?(?:validated|verified)",
]

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
    # v2.5 runtime/security contract areas.
    "FastAPI lifecycle",
    "Generation topology",
    "Login rate limiting",
    "Session persistence",
    "Trusted proxy",
    "Observability endpoints",
    "Elasticsearch security",
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


def _unmarked_claim_lines(text: str, patterns: list[str]) -> list[str]:
    """Return matches for ``patterns`` on lines without an explicit historical marker.

    Deterministic and line-scoped: never hardcodes a PR number, calls the GitHub
    API, reads the clock, or inspects git history.
    """
    claims: list[str] = []
    for line in text.splitlines():
        if HISTORICAL_MARKERS.search(line):
            continue
        for pattern in patterns:
            match = re.search(pattern, line, flags=re.IGNORECASE)
            if match:
                claims.append(match.group(0))
                break
    return claims


def post_merge_phase_drift_claims(text: str) -> list[str]:
    """Return reconciliation-phase claims that should have become post-merge truth."""
    return _unmarked_claim_lines(text, POST_MERGE_PHASE_DRIFT_PATTERNS)


def retired_topology_claims(text: str) -> list[str]:
    """Return current-doc references to the retired dual-4B topology."""
    return _unmarked_claim_lines(text, CONSOLIDATED_4B_DRIFT_PATTERNS)


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

    phase_claims = sorted(set(post_merge_phase_drift_claims(text)))
    if phase_claims:
        fail(
            errors,
            f"{_display(path)}: reconciliation-phase wording in current docs: {phase_claims}",
        )

    topology_claims = sorted(set(retired_topology_claims(text)))
    if topology_claims:
        fail(
            errors,
            f"{_display(path)}: retired dual-4B topology presented as current (mark as historical/target design): "
            f"{topology_claims}",
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


def check_docs_index(errors: list[str]) -> None:
    """docs/README.md must index the current canonical docs under stable sections."""
    path = ROOT / "docs/README.md"
    if not path.exists():
        fail(errors, "docs/README.md is missing")
        return
    text = path.read_text(encoding="utf-8")
    required = [
        "Canonical / Current",
        "Evaluation",
        "Interview / Architecture truth",
        "Design",
        "Historical / Implementation Plans",
        "interview-architecture-baseline.md",
        "ragas-evaluation-guide.md",
        "repository-truth-audit.md",
    ]
    for token in required:
        if token not in text:
            fail(errors, f"docs/README.md must index {token!r}")


def ragas_zero_fallback_claims(text: str) -> list[str]:
    """Return missing-RAGAS zero-score fallback claims presented as current.

    Line-scoped and negation-aware: a denial such as "never emit a zero-score
    report" is not a claim, and lines with an explicit historical marker are
    exempt. Only RAGAS lines are considered, so unrelated zero wording (for
    example a cache hit-rate sentence) is not misread as this fallback.
    Deterministic: no PR number, GitHub API, git history or wall clock.
    """
    claims: list[str] = []
    for line in text.splitlines():
        if "ragas" not in line.lower():
            continue
        if RAGAS_HISTORICAL_MARKERS.search(line):
            continue
        for pattern in RAGAS_ZERO_FALLBACK_CLAIM_PATTERNS:
            match = re.search(pattern, line, flags=re.IGNORECASE)
            if not match:
                continue
            window = line[max(0, match.start() - 24) : match.end() + 24]
            if any(re.search(denial, window, flags=re.IGNORECASE) for denial in RAGAS_ZERO_FALLBACK_DENIAL_PATTERNS):
                continue
            claims.append(match.group(0))
            break
    return claims


_RAGAS_SEGMENT_SPLIT_RE = re.compile(r"[；;。！!？?，,\n]+")

# A status guarantee is negated when an auxiliary/negation directly governs it,
# e.g. "does not fail fast", "will not return a non-zero status", "fails to
# return a non-zero status", "is unable to return", "不会 fail fast",
# "不返回非零状态". A bare "not installed" earlier in the segment does not count.
_STATUS_NEGATION_BEFORE_RE = re.compile(
    r"(?:does|do|will|would|should|could|can|is|are|was|were)\s+not\s+(?:\w+\s+){0,3}$|"
    r"(?:never|not)\s+(?:\w+\s+){0,3}$|"
    r"(?:fails?|failed)\s+to\s+(?:\w+\s+){0,3}$|"
    r"(?:is|are|was|were)?\s*unable\s+to\s+(?:\w+\s+){0,3}$|"
    r"(?:不|未|无法|不能|不会|未能)[^\n]{0,4}$",
    re.IGNORECASE,
)


def _ragas_failure_segments(text: str) -> list[str]:
    """Return sentence/clause segments that describe an unavailable/failed evaluator.

    Segments are split on sentence/clause punctuation (not commas), so a
    guarantee must live in the same clause as the evaluator-failure condition.
    """
    segments: list[str] = []
    for line in text.splitlines():
        for segment in _RAGAS_SEGMENT_SPLIT_RE.split(line):
            if not segment.strip():
                continue
            if "ragas" not in segment.lower() and "evaluator" not in segment.lower():
                continue
            if RAGAS_FAILURE_CONDITION_RE.search(segment):
                segments.append(segment)
    return segments


def _ragas_status_affirmative(segment: str) -> bool:
    """True when the segment asserts the non-zero/fail-fast status affirmatively."""
    for match in RAGAS_FAILURE_STATUS_REQUIRED_RE.finditer(segment):
        prefix = segment[max(0, match.start() - 24) : match.start()]
        if _STATUS_NEGATION_BEFORE_RE.search(prefix):
            continue
        return True
    return False


# A no-report guarantee is denied when the suppression itself is negated, e.g.
# "this does not mean no quality report", "并非不生成报告". The affirmative forms
# ("does not produce a report", "不生成报告") include the negation in the match
# itself, so only a meta-negation before the phrase counts.
_REPORT_NEGATION_BEFORE_RE = re.compile(
    r"(?:does|do|did)\s+not\s+mean\s+(?:\w+\s+){0,2}$|"
    r"(?:doesn't|don't|didn't)\s+mean\s+(?:\w+\s+){0,2}$|"
    r"not\s+that\s+(?:\w+\s+){0,2}$|"
    r"(?:并非|不代表|不等于|并不意味着|不能说明)[^\n]{0,6}$",
    re.IGNORECASE,
)


def _ragas_report_affirmative(segment: str) -> bool:
    """True when the segment asserts report suppression affirmatively."""
    for match in RAGAS_NO_REPORT_REQUIRED_RE.finditer(segment):
        prefix = segment[max(0, match.start() - 32) : match.start()]
        if _REPORT_NEGATION_BEFORE_RE.search(prefix):
            continue
        return True
    return False


def check_ragas_failure_contract(errors: list[str]) -> None:
    """RAGAS docs must describe both failure contracts, not a zero fallback.

    No canonical doc may present the retired missing-RAGAS zero fallback as
    current behavior. The core RAGAS docs must independently document that an
    unavailable/failed evaluator is unsuccessful (fail fast / non-zero), and
    that it does not produce a quality report. Both assertions are scoped to
    evaluator-failure clauses and the status half must not be negated.
    """
    for path in CANONICAL_DOCS:
        if not path.exists():
            continue
        claims = sorted(set(ragas_zero_fallback_claims(path.read_text(encoding="utf-8"))))
        if claims:
            fail(
                errors,
                f"{_display(path)}: missing-RAGAS zero / `_warning` fallback presented as current behavior: {claims}",
            )
    for name in RAGAS_REQUIRED_DOCS:
        path = ROOT / name
        if not path.exists():
            continue
        segments = _ragas_failure_segments(path.read_text(encoding="utf-8"))
        if not any(_ragas_status_affirmative(segment) for segment in segments):
            fail(
                errors,
                f"{name}: must document unavailable/failure as unsuccessful/non-zero",
            )
        if not any(_ragas_report_affirmative(segment) for segment in segments):
            fail(
                errors,
                f"{name}: must document that unavailable/failure does not produce a quality report",
            )


def stale_local_validation_claims(text: str) -> list[str]:
    """Return local-validation denial claims, exempting external-boundary clauses.

    The exemption is clause-scoped: a clause that explicitly describes an
    external/production boundary (Redis Cluster/Sentinel, cloud LB, multi-node
    ES/TLS, long-run Prometheus/Grafana, production HA/SLO) is allowed, while a
    stale claim about the completed single-host local validation in another
    clause on the same line is still flagged.
    """
    claims: list[str] = []
    for line in text.splitlines():
        for clause in re.split(r"[；;。，,]+", line):
            if LOCAL_VALIDATION_EXTERNAL_BOUNDARY_RE.search(clause):
                continue
            for pattern in STALE_LOCAL_VALIDATION_CLAIM_PATTERNS:
                match = re.search(pattern, clause, flags=re.IGNORECASE)
                if match:
                    claims.append(match.group(0))
                    break
    return claims


def check_local_runtime_validation_contract(errors: list[str]) -> None:
    """Canonical docs must not deny the completed local real-dependency validation."""
    for name in LOCAL_VALIDATION_DOCS:
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "LOCAL_REAL_VALIDATION" not in text:
            fail(
                errors,
                f"{name}: must classify the completed Redis/nginx/ES/Prometheus validation as LOCAL_REAL_VALIDATION",
            )
        claims = sorted(set(stale_local_validation_claims(text)))
        if claims:
            fail(
                errors,
                f"{name}: stale 'not really validated' local-runtime claim: {claims}",
            )


def check_metrics_auth_contract(errors: list[str]) -> None:
    """Operator docs must state that /api/metrics requires authentication."""
    for name in (
        "docs/operations-guide.md",
        "docs/pre-launch-checklist.md",
        "docs/deployment-guide.md",
    ):
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if not re.search(r"Bearer|需要认证|需要身份|require_identity", text):
            fail(errors, f"{name}: must document that /api/metrics requires authentication")


def check_uvicorn_proxy_headers_disabled(errors: list[str]) -> None:
    """app.py must disable uvicorn's own X-Forwarded-For handling.

    The application-level TRUSTED_PROXIES policy in api.routes_auth is only
    authoritative when uvicorn does not pre-trust XFF for its default
    ``forwarded_allow_ips`` peers.
    """
    app = ROOT / "app.py"
    if not app.exists():
        return
    text = app.read_text(encoding="utf-8")
    if "uvicorn.run" in text and "proxy_headers=False" not in text:
        fail(
            errors,
            "app.py: uvicorn.run must set proxy_headers=False so the api.routes_auth TRUSTED_PROXIES policy is authoritative",
        )


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
    check_metrics_auth_contract(errors)
    check_rbac_mask_contract(errors)
    check_docs_index(errors)
    check_ragas_failure_contract(errors)
    check_local_runtime_validation_contract(errors)
    check_uvicorn_proxy_headers_disabled(errors)

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
