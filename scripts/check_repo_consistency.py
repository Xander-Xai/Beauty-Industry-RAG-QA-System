#!/usr/bin/env python3
"""Check repository metadata, links, and drift-prone documentation claims.

This guard is intentionally conservative: it fails on broken local references,
runtime-artifact leaks, version drift, documented-but-missing offline CLI
subcommands, stale "offline ingestion is missing" claims in current operator
docs, superseded governance/contract claims in canonical docs,
post-merge reconciliation-phase wording (pending candidate / awaiting merge /
stale latest-merged-main references), current docs that still present the
retired dual-4B topology, and an invalid/absent repository truth audit. It also
ties a stated Kubernetes static-check count back to the test module that defines
those checks, so a summary cannot keep counting the manifests after a check is
added. It does not flag historical CHANGELOG text or historical implementation
plans.

It also enforces one evidence vocabulary. ``docs/interview-evidence-map.md``
owns the canonical taxonomy, and every current interview-facing or
repository-truth document must classify its claims with those levels and no
others; the docs index inventory must equal that taxonomy exactly. A retired
status word such as ``VERIFIED`` or ``PARTIAL`` is not a level, and a run
outcome such as ``BLOCKED`` does not satisfy a classification. The legal levels
are parsed out of the vocabulary table rather than restated here, so the code can
never hold a second copy that quietly diverges.

The truth audit is expected to resolve its candidate from ``HEAD`` and to carry
an ISO ``YYYY-MM-DD`` verification date. The date is validated for shape only;
the guard never hardcodes a specific date or depends on the current date, a
GitHub API, or wall-clock state, so runs stay deterministic.

The recorded reconciliation lineage (completed issues, the current open scope,
and the external validation trackers) is a **snapshot of GitHub state written
down by a human after re-querying the GitHub API**, not a live observation. The
guard reads only the working tree, so it can prove the document and the guard
agree with each other; it cannot and does not claim to know whether an issue is
open on GitHub right now. What it does enforce is that a transition nobody
recorded — closing the current issue, dropping a tracker, describing a completed
issue as the work happening now — cannot pass silently. It also treats "no open
reconciliation issue" as a legitimate state that must be stated outright rather
than a gap to be filled by reviving a closed issue.
"""

from __future__ import annotations

import argparse
import ast
import datetime
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DOCS = [
    ROOT / "README.md",
    ROOT / "PRD.md",
    *sorted((ROOT / "docs").glob("*.md")),
    # Validation records and the benchmark artifact contract make the same
    # evidence claims as the guides, so they are held to the same guard. They
    # were previously unguarded, which left the most claim-heavy docs free to
    # drift.
    *sorted((ROOT / "docs" / "validation").glob("*.md")),
    # Nested current docs that are not validation records. The demo-capture
    # README is operator-facing and claim-heavy in exactly the way the guards
    # above exist to catch, so it is named here rather than left in a nested
    # directory where no glob would pick it up.
    *sorted((ROOT / "docs" / "demo").glob("*.md")),
    ROOT / "artifacts/benchmarks/README.md",
]

# --------------------------------------------------------------------------
# Documentation classification invariant
# --------------------------------------------------------------------------
#
# Every Markdown file under `docs/` must be exactly one of two things:
#
#   A. current/canonical documentation, listed in CANONICAL_DOCS and therefore
#      checked by the guards in this file;
#   B. historical documentation under `docs/archive/**`, whose opening lines
#      mark it as historical and refuse it as a current source.
#
# The third state — a Markdown file that sits under `docs/`, belongs to neither
# set, and therefore has no owner and no guard — is what this invariant exists
# to stop. Such a file drifts freely: nothing checks it, and nothing says it is
# history, so a stale claim in it is indistinguishable from current truth.
#
# The invariant is a statement about *placement*, never about a document's
# contents. It therefore cannot read a historical sentence inside a current doc
# as a current claim, and it never inspects `CHANGELOG.md` history at all — that
# file lives outside `docs/` and its release narration is history by
# construction.

ARCHIVE_DOC_ROOT = ROOT / "docs" / "archive"

#: The banner belongs in the file's opening lines, not in an appendix a reader
#: has to go looking for.
ARCHIVE_BANNER_LINES = 20

#: Word-level marker that the file is history rather than current truth.
ARCHIVE_HISTORICAL_MARKER_RE = re.compile(
    r"\bhistor(?:y|ical)\b|\barchiv(?:e|ed|al)\b|\bsuperseded\b|\bobsolete\b|"
    r"\bdeprecated\b|\bno\s+longer\s+current\b|"
    r"历史|归档|已废弃|已被取代|不再作为",
    re.IGNORECASE,
)

#: The refusal half of the banner. A bare "not" is deliberately *not* enough on
#: its own: the banner has to say the file must not be used as a source, not
#: merely contain a negation somewhere.
ARCHIVE_NON_AUTHORITY_NEGATION_RE = re.compile(
    r"must\s+not|shall\s+not|may\s+not|do\s+not|does\s+not|don't|doesn't|did\s+not|"
    r"cannot|can't|never|no\s+longer|not\s+(?:a|an|the|be|used|treated|relied|cited)|"
    r"不得|不应|禁止|不能|不是当前|不再是|不再作为",
    re.IGNORECASE,
)

#: What the file must not be used as a source of: capability, architecture,
#: metric or validation claims — plus the generic authority words those four
#: share.
ARCHIVE_AUTHORITY_NOUN_RE = re.compile(
    r"\bcapabilit(?:y|ies)\b|\barchitectur(?:e|al)\b|\bmetrics?\b|\bvalidation\b|"
    r"\bsource(?:s)?\b|\bevidence\b|\bauthorit(?:y|ative)\b|\bcanonical\b|"
    r"能力|架构|指标|验证|来源|证据",
    re.IGNORECASE,
)

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

# A zero match only counts as the retired score fallback when the surrounding
# window refers to a score/result/report/fallback or to an unavailable
# evaluator. This keeps truthful statements such as "returns zero failed
# samples" or "returns zero exit status" out of the guard.
_RAGAS_ZERO_CONTEXT_RE = re.compile(
    r"score|scores|result|results|report|reports|fallback|零分|_warning|warning|"
    r"unavailable|missing|not\s+installed|缺少|缺失|不可用|未安装",
    re.IGNORECASE,
)

# Two independent RAGAS failure contracts. They must each be documented, and a
# bare "UNAVAILABLE" must not satisfy the non-zero/failure-status half.
RAGAS_FAILURE_STATUS_REQUIRED_RE = re.compile(
    r"fail(?:s|ed)?[- ]?fast|非\s*0\s*退出|非零退出|非零状态|"
    r"non-?zero\s+(?:status|exit(?:\s+code)?|code)|退出码\s*[2-5]",
    re.IGNORECASE,
)
RAGAS_NO_REPORT_REQUIRED_RE = re.compile(
    r"不生成[^\n]{0,16}(?:质量)?(?:报告|report)|不写[^\n]{0,8}(?:报告|report)|"
    r"no\s+quality\s+report|never\s+(?:write|emit|produce)[^\n]{0,20}report|"
    r"does\s+not\s+produce[^\n]{0,24}report|doesn't\s+produce[^\n]{0,24}report|"
    r"don't\s+produce[^\n]{0,24}report|must\s+not\s+produce[^\n]{0,24}report|"
    r"will\s+not\s+produce[^\n]{0,24}report|won't\s+produce[^\n]{0,24}report|"
    r"without\s+producing[^\n]{0,24}report",
    re.IGNORECASE,
)

# Only clauses that describe an unavailable/failed *evaluator* count as the
# failure clause. Availability wording must bind to the evaluator, dependency or
# credentials (not unrelated data), and the guarantee wording itself (`fail fast`,
# `非零`, exit codes) is excluded so a successful-run statement cannot bootstrap
# its own failure scope.
_RAGAS_AVAIL = r"(?:unavailable|missing|not\s+installed)"
_RAGAS_AVAIL_CN = r"(?:缺少|缺失|不可用|未安装)"
_RAGAS_ANCHOR = r"(?:evaluator|dependenc(?:y|ies)|credential(?:s)?|api\s+key)"
_RAGAS_ANCHOR_CN = r"(?:evaluator|评估器|依赖|凭据)"
RAGAS_FAILURE_CONDITION_RE = re.compile(
    rf"(?:{_RAGAS_ANCHOR}|{_RAGAS_ANCHOR_CN})[^\n]{{0,24}}(?:{_RAGAS_AVAIL}|{_RAGAS_AVAIL_CN})|"
    rf"(?:{_RAGAS_AVAIL}|{_RAGAS_AVAIL_CN})[^\n]{{0,24}}(?:{_RAGAS_ANCHOR}|{_RAGAS_ANCHOR_CN})|"
    rf"ragas\s+(?:is\s+)?(?:unavailable|missing|not\s+installed)|"
    rf"ragas\s*(?:{_RAGAS_AVAIL_CN})|"
    rf"(?:unavailable|not\s+installed|{_RAGAS_AVAIL_CN})[^\n]{{0,8}}ragas",
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

# The LOCAL_REAL_VALIDATION marker must be attached to the actual completed
# dependencies and an affirmative validation statement, so silently deleting the
# evidence claim (leaving only a glossary mention) is detected.
_LOCAL_VALIDATION_AFFIRMATIVE_RE = re.compile(
    r"validated|verified|完成|已验证|验证|执行|已",
    re.IGNORECASE,
)
_VALIDATION_AFFIRMATIVE_NEGATION_BEFORE_RE = re.compile(
    r"not\s+(?:\w+\s+){0,2}$|never\s+(?:\w+\s+){0,2}$|"
    r"尚未\s*$|未能\s*$|还未\s*$|未\s*$|无法\s*$|不能\s*$",
    re.IGNORECASE,
)


def _affirmative_validation_in(window: str) -> bool:
    """True when ``window`` contains a non-negated completed-validation phrase."""
    for match in _LOCAL_VALIDATION_AFFIRMATIVE_RE.finditer(window):
        prefix = window[max(0, match.start() - 24) : match.start()]
        if _VALIDATION_AFFIRMATIVE_NEGATION_BEFORE_RE.search(prefix):
            continue
        return True
    return False


STALE_LOCAL_VALIDATION_CLAIM_PATTERNS = [
    r"(?:Redis|反向代理|代理|Elasticsearch|Prometheus|(?<![A-Za-z])ES(?![A-Za-z]))[^\n]{0,24}"
    r"(?:尚待|仍待|仍需|尚未|还未|未[^\n]{0,6}(?:验证|验收))",
    r"(?:尚待|仍待|仍需|尚未|还未)[^\n]{0,16}"
    r"(?:Redis|反向代理|Elasticsearch|Prometheus)[^\n]{0,16}(?:验收|验证)",
    r"(?:Redis|proxy|Elasticsearch|Prometheus)[^\n]{0,40}"
    r"(?:not\s+(?:yet\s+)?(?:been\s+)?(?:actually\s+)?|never\s+(?:been\s+)?(?:actually\s+)?)(?:validated|verified)",
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
    # Retrieval quality is now measured by a real harness, so its classification
    # must stay visible in the audit instead of living only in prose.
    "Retrieval benchmark",
    # Enterprise-readiness areas. Each has a framework state and a result state
    # that must stay separable, so each needs its own row.
    "OpenTelemetry tracing",
    "OTLP export",
    "Prometheus alerting",
    "Grafana dashboard",
    "SLO + incident runbook",
    "Structured audit trail",
    "Performance evidence",
    # The second, unwired threshold engine. Without its own row the audit cannot
    # state that it is not the canonical alerting contract.
    "In-process AlertingManager",
    # The frontend carries three separable evidence states (client contract, CI
    # build, end-to-end runtime) plus deployment. Without a row the audit cannot
    # keep them apart, which is how "built" came to be filed as pending while CI
    # was already building it.
    "Frontend contract",
}


# --------------------------------------------------------------------------
# Evidence guards: claims that require an artifact before they may be stated
# --------------------------------------------------------------------------

# The retrieval benchmark harness exists, but no run artifact is committed. A
# benchmark *result* therefore requires a checked-in artifact; without one the
# only true statement is that the framework is implemented. This is a fact about
# the working tree, so the guard derives it from disk instead of hardcoding a
# PR number, an issue state or a date.
BENCHMARK_ARTIFACT_GLOB = "artifacts/benchmarks/*/metadata.json"

# Docs that must keep the framework/result split visible. A reader who sees only
# "Recall@10" deserves to know whether a number can exist yet.
BENCHMARK_CLASSIFICATION_DOCS = [
    "README.md",
    "docs/README.md",
    "docs/interview-evidence-map.md",
    "docs/repository-truth-audit.md",
    "artifacts/benchmarks/README.md",
]

_BENCHMARK_SUBJECT_RE = re.compile(
    r"retrieval\s+benchmark|benchmark|检索(?:基准|评测)|Recall@|HitRate@|NDCG@|MRR@",
    re.IGNORECASE,
)
_FRAMEWORK_CLASSIFIED_RE = re.compile(r"REPO_VERIFIED|已实现|implemented|framework\s*=?\s*", re.IGNORECASE)
_RESULT_PENDING_RE = re.compile(
    r"PENDING|尚未|未(?!来)|尚未执行|not\s+(?:yet\s+)?(?:available|produced|exist)", re.IGNORECASE
)


def benchmark_artifact_exists() -> bool:
    """True when a committed benchmark run artifact is present on disk."""
    return any(ROOT.glob(BENCHMARK_ARTIFACT_GLOB))


def benchmark_classification_errors(name: str, text: str) -> list[str]:
    """Return classification errors for one document.

    Requires that a doc mentioning the retrieval benchmark either says the
    framework is implemented or says no result exists yet. While no artifact is
    on disk, an affirmative result claim is an error regardless of phrasing.
    """
    errors: list[str] = []
    has_subject = _BENCHMARK_SUBJECT_RE.search(text) is not None
    if not has_subject:
        return errors

    result_claim = bool(re.search(r"(?:result|结果|指标|metric)", text, re.IGNORECASE))
    if not result_claim:
        return errors

    # Only enforce the split where the doc discusses the benchmark's own state.
    state_window = "\n".join(
        line
        for line in text.splitlines()
        if _BENCHMARK_SUBJECT_RE.search(line)
        or re.search(r"(?:REPO_VERIFIED|PENDING|framework|result|结果)", line, re.IGNORECASE)
    )
    if not _FRAMEWORK_CLASSIFIED_RE.search(state_window) and not _RESULT_PENDING_RE.search(state_window):
        errors.append(
            f"{name}: discusses the retrieval benchmark without classifying it "
            "(framework REPO_VERIFIED vs result PENDING)"
        )
    return errors


# Markdown emphasis is formatting, not meaning. "is not verified", "is **not**
# verified", "is not **verified**" and "is **not** **verified**" are one and the same
# denial, so the negation matchers below must not care which of them a document
# happens to use. They used a plain `\s+` at the junction between a negation word and
# its complement; emphasis delimiters sitting in that gap silently broke the match and
# turned a truthful denial into a reported claim.
#
# _EMPHASIS_GAP accepts whitespace and emphasis delimiters in those junctions only.
# It is delimiters-only by construction: no word, digit or identifier character is
# ever consumed, so REPO_VERIFIED, LOCAL_REAL_VALIDATION, HISTORICAL_PRODUCTION and
# the rag_* metric series keep their underscores. It also still requires at least one
# separator character, so "notvalidated" does not read as "not validated", and it does
# not relax the leading-context / line-window boundary in forbidden_evidence_claims().
_EMPHASIS_GAP = r"(?:\s+[*_]*|[*_]+\s*)+"

# A sentence that frames the match as a claim to avoid, not as current truth.
# These docs are *required* to write the denial, so the frame is a signal to
# skip rather than a signal to fail.
_PROHIBITION_FRAME_RE = re.compile(
    r"\bClaiming\b|\bClaims?\b|\bSays?\b|\bDo" + _EMPHASIS_GAP + r"not\b|\bDon't\b|\bNever\b|"
    r"\bMust" + _EMPHASIS_GAP + r"not\b|"
    r"不能说|不得|不要(?:说|声称)|不应(?:说|声称)",
    re.IGNORECASE,
)

# Absence / not-yet wording in the neighbourhood of the match. Covers both the
# "no result exists" form and the specific "no exporter configured" form that
# denies a closed tracing loop.
#
# Two junctions here carry no separator at all and must not be routed through the
# mandatory-gap rule above: the `un` prefix of "unconfigured", and the contraction
# in "doesn't" / "don't". Both are spelled without a space in ordinary English, so
# they are spelled out as their own zero-gap alternatives. _EMPHASIS_GAP itself is
# unchanged, so every other junction still demands a separator and "notvalidated"
# still does not read as "not validated".
_NO_EVIDENCE_NEGATION_RE = re.compile(
    r"PENDING|EXTERNAL_MODEL_ASSET_REQUIRED|"
    r"未(?:有|能|执行|验证|产生|配置|运行)|尚未|没有|无可|不(?:会|能|得|是)|"
    r"not" + _EMPHASIS_GAP + r"(?:yet" + _EMPHASIS_GAP + r")?"
    r"(?:validated|verified|measured|available|produced|configured|"
    r"reproduced|reproducible|closed)|"
    r"no" + _EMPHASIS_GAP + r"(?:such|exporter|OTLP|closed|real" + _EMPHASIS_GAP + r"benchmark|artifact)|"
    r"exporter[^\n]{0,12}(?:not" + _EMPHASIS_GAP + r"|un)configured|"
    r"never|cannot|can't|without|must" + _EMPHASIS_GAP + r"not|"
    r"do(?:es)?" + _EMPHASIS_GAP + r"not|do(?:es)?" + _EMPHASIS_GAP + r"n't|do(?:es)?n't|"
    r"design" + _EMPHASIS_GAP + r"target|目标|blocked",
    re.IGNORECASE,
)


def forbidden_evidence_claims(text: str) -> list[str]:
    """Return claims of real-model / real-result validation that have no artifact.

    Deliberately narrow: it matches only an affirmative "validated / 验证通过 /
    closed loop / score = N" assertion about an external asset or a measured
    result, and it stays away from metric *names* (which are legitimate) and from
    any sentence that itself says the thing is pending or absent.
    """
    claims: list[str] = []
    result_number = re.compile(
        r"(?:Recall|HitRate|MRR|NDCG|F1)(?:@\d+)?\s*(?:=|:|为|＝|：)\s*0?\.\d+|"
        r"(?:Recall|HitRate|MRR|NDCG)@\d+\s*[:：]\s*\d+"
    )
    ragas_score = re.compile(
        r"RAGAS[^\n]{0,40}?(?:faithfulness|answer_relevancy|context_precision|"
        r"context_recall|忠实度|相关性)[^\n]{0,12}?(?:score|得分|分数|为|=)\s*\d+(?:\.\d+)?|"
        r"(?:faithfulness|answer_relevancy|context_precision|context_recall)[^\n]{0,8}?"
        r"(?:score|得分|分数)\s*[:：=]?\s*\d+(?:\.\d+)?"
    )
    external_validated = re.compile(
        r"(?:真实\s*)?(?:BGE|CLIP|PaddleOCR|Paddle\s*OCR)[^\n]{0,30}"
        r"(?:已(?:经)?(?:完成|通过)?验证|验证(?:通过|完成)|已运行|实测通过)|"
        r"real\s+(?:configured\s+)?(?:BGE|CLIP|PaddleOCR)[^\n]{0,30}"
        r"(?:validated|verified|passed|ran\s+successfully)",
        re.IGNORECASE,
    )
    tracing_closed_loop = re.compile(
        r"(?:OpenTelemetry|OTel|Jaeger|OTLP)[^\n]{0,40}?(?:已(?:经)?导出|导出(?:成功|已闭环)|closed[- ]?loop|闭环(?:验证|完成)?|"
        r"export(?:ed|s)?\s+(?:verified|confirmed))",
        re.IGNORECASE,
    )
    load_verified = re.compile(
        r"(?:已(?:经)?)?(?:实测|测量|验证|压测)[^\n]{0,16}?(?:QPS|吞吐|throughput|P9[59]|TTFT)[^\n]{0,12}?(?:达标|通过|已达成)|"
        r"(?:QPS|throughput|P9[59])[^\n]{0,16}?(?:verified|validated|measured|achieved)\s+(?:in|at)\s+production",
        re.IGNORECASE,
    )

    for line in text.splitlines():
        for pattern in (result_number, ragas_score, external_validated, tracing_closed_loop, load_verified):
            for match in pattern.finditer(line):
                # Leading context is capped at 50 characters so a denial earlier in
                # the same sentence does not retroactively excuse a later claim.
                # The window then runs to the end of the line, because a markdown
                # table row puts its classification after the claim and the row is
                # often longer than 50 characters.
                window = line[max(0, match.start() - 50) :]
                prefix = line[max(0, match.start() - 60) : match.start()]
                # A "do not claim this" frame in the same bullet is the opposite of
                # an overclaim: these documents are required to state the denial.
                if _PROHIBITION_FRAME_RE.search(prefix):
                    continue
                if _NO_EVIDENCE_NEGATION_RE.search(window):
                    continue
                if HISTORICAL_MARKERS.search(line):
                    continue
                claims.append(match.group(0))
                break
    return claims


def check_evidence_classification_guards(errors: list[str]) -> None:
    """Framework/result classification and forbidden real-evidence claims."""
    artifact = benchmark_artifact_exists()

    for name in BENCHMARK_CLASSIFICATION_DOCS:
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        problems = benchmark_classification_errors(name, text)
        errors.extend(problems)

        if not artifact:
            claims = sorted(set(forbidden_evidence_claims(text)))
            if claims:
                errors.append(
                    f"{name}: claims a real-model / real-result validation with no benchmark artifact on disk: {claims}"
                )

    if artifact:
        # An artifact exists: the honest statement is that a result is available,
        # so a lingering "result = PENDING" line becomes the drift instead.
        for name in BENCHMARK_CLASSIFICATION_DOCS:
            path = ROOT / name
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                if not _BENCHMARK_SUBJECT_RE.search(line):
                    continue
                if re.search(
                    r"(?:no|not)\s+(?:real\s+)?benchmark\s+artifact[^\n]{0,40}(?:exists|committed)|"
                    r"没有(?:可复现的)?(?:真实)?benchmark\s*artifact",
                    line,
                    re.IGNORECASE,
                ):
                    errors.append(f"{name}: says no benchmark artifact exists, but one is on disk")


# The v2.5 label names a historical working milestone. It must never be upgraded
# into a release claim without a real release, which would then contradict the
# config/CHANGELOG invariant checked separately.
_WORKING_MILESTONE_MARKER_RE = re.compile(
    r"working\s+milestone|working-milestone|development[- ]phase|开发阶段|工作里程碑|历史\s*v?2\.5|"
    r"historical\s+working",
    re.IGNORECASE,
)


def v25_release_claims(text: str) -> list[str]:
    """Return lines presenting the v2.5 working-milestone label as a release."""
    claims: list[str] = []
    for line in text.splitlines():
        if not re.search(r"\bv?2\.5\b", line):
            continue
        if _WORKING_MILESTONE_MARKER_RE.search(line):
            continue
        # An explicit denial ("v2.5 is not a release") is the desired wording.
        if re.search(
            r"not\s+(?:a|the)\s+(?:formal\s+)?release|不是.{0,6}正式|非正式|no\s+release|不是.{0,4}版本",
            line,
            re.IGNORECASE,
        ):
            continue
        if re.search(
            r"(?:released?|发布(?:版|版本)?|tag(?:ged)?|release[^\n]{0,20}version)[^\n]{0,20}\bv?2\.5\b|"
            r"\bv?2\.5\b[^\n]{0,20}(?:is\s+the\s+(?:current\s+)?(?:release|runtime\s+version)|"
            r"released?\s+on|已发布|正式版)",
            line,
            re.IGNORECASE,
        ):
            claims.append(line.strip())
    return claims


def check_version_label_semantics(errors: list[str]) -> None:
    """v2.5 must stay a working-milestone label, never a formal runtime release."""
    for name in (
        "README.md",
        "PRD.md",
        "CHANGELOG.md",
        "docs/README.md",
        "docs/repository-truth-audit.md",
        "docs/interview-architecture-baseline.md",
        "docs/deployment-guide.md",
        "docs/operations-guide.md",
    ):
        path = ROOT / name
        if not path.exists():
            continue
        claims = v25_release_claims(path.read_text(encoding="utf-8"))
        if claims:
            errors.append(f"{name}: presents v2.5 as a formal release/runtime release: {claims}")


# The PRD states latency/QPS targets. They must stay labelled as design targets:
# a target silently promoted to a measurement is exactly the drift the audit
# exists to prevent.
_PRD_DESIGN_TARGET_PATTERNS = [
    r"design\s+target",
    r"目标(?:值)?",
    r"不是(?:实测|生产)",
    r"not\s+(?:a\s+)?(?:measured|production)",
    r"PENDING",
]


def check_prd_design_targets(errors: list[str]) -> None:
    """PRD performance figures must remain design targets, never repo benchmarks."""
    path = ROOT / "PRD.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    if not any(re.search(pattern, text, re.IGNORECASE) for pattern in _PRD_DESIGN_TARGET_PATTERNS):
        fail(
            errors,
            "PRD.md: latency/QPS figures must be explicitly labelled design targets, not measured results",
        )
    claims = sorted(set(forbidden_evidence_claims(text)))
    if claims:
        fail(
            errors,
            f"PRD.md: presents an unevidenced real result or model validation: {claims}",
        )


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
        if normalized.startswith("models/"):
            continue  # operator-supplied model assets, not tracked in this repository
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
        # The index is where a reader looks to learn where a doc belongs, so it
        # has to state the two-way split that the classification guard enforces.
        "docs/archive/",
    ]
    for token in required:
        if token not in text:
            fail(errors, f"docs/README.md must index {token!r}")


#: The only two classifications a Markdown file under `docs/` may have.
CANONICAL = "canonical"
HISTORICAL = "historical"
UNCLASSIFIED = "unclassified"


def _doc_key(path: Path) -> str:
    """Return a comparison key that ignores redundant separators and ``./``.

    ``Path.resolve`` is deliberately avoided: it follows symlinks, so the same
    file would get two different keys depending on which path spelled it.
    """
    return os.path.normpath(str(path))


def _is_within(key: str, parent_key: str) -> bool:
    """True when ``key`` is ``parent_key`` itself or lives underneath it."""
    return key == parent_key or key.startswith(parent_key + os.sep)


def archive_banner_errors(name: str, text: str) -> list[str]:
    """Return errors for an archived document whose banner is missing or too weak.

    A banner has to carry two independent halves, and both are checked inside
    the file's opening lines:

    1. an explicit historical/superseded marker, so a reader knows the file is
       not current;
    2. an explicit refusal to be used as a current capability / architecture /
       metric / validation source.

    Half 1 alone is the failure this guard exists to catch: the retired
    dual-4B topology and the missing-RAGAS zero fallback are both *correct*
    statements about the past, and both become false the moment a reader
    mistakes them for the present. "Historical" without the refusal still reads
    as a source.
    """
    errors: list[str] = []
    banner = "\n".join(text.splitlines()[:ARCHIVE_BANNER_LINES])

    if not ARCHIVE_HISTORICAL_MARKER_RE.search(banner):
        errors.append(
            f"{name}: archived document has no historical/superseded marker in its first {ARCHIVE_BANNER_LINES} lines"
        )

    refuses_authority = any(
        ARCHIVE_NON_AUTHORITY_NEGATION_RE.search(line) and ARCHIVE_AUTHORITY_NOUN_RE.search(line)
        for line in banner.splitlines()
    )
    if not refuses_authority:
        errors.append(
            f"{name}: archived document must declare that it is not a current "
            f"capability / architecture / metric / validation source (no such declaration "
            f"in its first {ARCHIVE_BANNER_LINES} lines)"
        )
    return errors


def docs_markdown_files(root: Path) -> list[Path]:
    """Return every ``docs/**/*.md`` file, sorted, under ``root``.

    Skipped only for paths inside ``.git``; the archive tree is included on
    purpose, since it is one of the two valid classifications rather than an
    exception to the rule.
    """
    docs_root = root / "docs"
    if not docs_root.is_dir():
        return []
    return sorted(path for path in docs_root.rglob("*.md") if path.is_file())


def doc_classification(path: Path, canonical: Iterable[Path], root: Path) -> str:
    """Classify one Markdown file under ``docs/`` as canonical or historical.

    Canonical is checked first, so the existing guard scope is unchanged for
    every document already listed in ``CANONICAL_DOCS``.
    """
    key = _doc_key(path)
    if any(key == _doc_key(entry) for entry in canonical):
        return CANONICAL
    if _is_within(key, _doc_key(root / "docs" / "archive")):
        return HISTORICAL
    return UNCLASSIFIED


def docs_classification_errors(root: Path, canonical: Iterable[Path]) -> list[str]:
    """Return errors for Markdown under ``docs/`` that is neither current nor archived.

    Deterministic and content-blind: it walks the tree, compares placement, and
    — for archived files only — reads the opening banner. It never inspects
    claim wording, so a historical sentence in a current doc is not an error,
    and ``CHANGELOG.md`` is out of scope entirely because it is not under
    ``docs/``.
    """
    errors: list[str] = []
    canonical_keys = {_doc_key(entry) for entry in canonical}
    archive_key = _doc_key(root / "docs" / "archive")

    for path in docs_markdown_files(root):
        key = _doc_key(path)
        name = os.path.relpath(key, _doc_key(root))
        if key in canonical_keys and _is_within(key, archive_key):
            errors.append(
                f"{name}: is classified as both canonical and historical; a document under "
                "docs/archive/ must be historical only"
            )
            continue

        classification = doc_classification(path, canonical_keys, root)
        if classification == UNCLASSIFIED:
            errors.append(
                f"{name}: unclassified documentation; a Markdown file under docs/ must be "
                "either current (listed in CANONICAL_DOCS, so the consistency guards check it) "
                "or historical (moved under docs/archive/ with an explicit historical banner)"
            )
        elif classification == HISTORICAL:
            errors.extend(archive_banner_errors(name, path.read_text(encoding="utf-8")))
    return errors


def check_docs_classification(errors: list[str], root: Path | None = None) -> None:
    """No Markdown under ``docs/`` may be neither canonical nor archived."""
    base = ROOT if root is None else root
    errors.extend(docs_classification_errors(base, CANONICAL_DOCS))


_RAGAS_CLAUSE_BOUNDARY_RE = re.compile(r"[；;。，,：:]")


def _clause_start(line: str, index: int) -> int:
    """Return the start of the clause containing ``index`` on ``line``."""
    start = 0
    for boundary in _RAGAS_CLAUSE_BOUNDARY_RE.finditer(line[:index]):
        start = boundary.end()
    return start


def _clause_end(line: str, index: int) -> int:
    """Return the end of the clause containing ``index`` on ``line``."""
    boundary = _RAGAS_CLAUSE_BOUNDARY_RE.search(line, index)
    return boundary.start() if boundary else len(line)


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
        for pattern in RAGAS_ZERO_FALLBACK_CLAIM_PATTERNS:
            found = False
            for match in re.finditer(pattern, line, flags=re.IGNORECASE):
                clause = line[_clause_start(line, match.start()) : _clause_end(line, match.end())]
                if RAGAS_HISTORICAL_MARKERS.search(clause):
                    continue
                context = line[max(0, match.start() - 40) : match.end() + 40]
                if not _RAGAS_ZERO_CONTEXT_RE.search(context):
                    continue
                denial_window = line[_clause_start(line, match.start()) : match.end()]
                if any(
                    re.search(denial, denial_window, flags=re.IGNORECASE)
                    for denial in RAGAS_ZERO_FALLBACK_DENIAL_PATTERNS
                ):
                    continue
                claims.append(match.group(0))
                found = True
                break
            if found:
                break
    return claims


_SENTENCE_SPLIT_RE = re.compile(r"[；;。！!？?\n]+")
_RAGAS_COMMA_SPLIT_RE = re.compile(r"[，,]+")
_LOCAL_VALIDATION_COMMA_SPLIT_RE = re.compile(r"[，,：:]+")
# Contrast conjunctions change scope, so a leading condition must not propagate
# through them.
_CONTRAST_SPLIT_RE = re.compile(
    r"\bbut\b|\bwhereas\b|\bhowever\b|\balthough\b|\bthough\b|然而|不过|但是|(?<!不)(?<!非)但", re.IGNORECASE
)

# A leading qualifier (subordinate condition or scope phrase) governs the whole
# sentence that follows it, so it must not be split away from its guarantees or
# boundary. Sentences that do not start with a qualifier are split normally.
_LEADING_QUALIFIER_RE = re.compile(
    r"^\s*(?:when|if|while|unless|because|since|given|for|in\s+case|provided|"
    r"in\s+production|in\s+prod|对于|关于|针对|在|当|若|如果|假如|一旦|鉴于)",
    re.IGNORECASE,
)


def _split_clauses(line: str, comma_split_re: re.Pattern[str]) -> list[str]:
    """Split a line into clauses, keeping leading qualifiers attached.

    Sentences are split on terminal punctuation first. Within a sentence, if the
    leading clause is a subordinate condition (or ends with ``时`` / ``的话``),
    the whole sentence is kept together so the qualifier governs every
    coordinated clause.
    """
    segments: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(line):
        for sub_sentence in _CONTRAST_SPLIT_RE.split(sentence):
            if not sub_sentence.strip():
                continue
            pieces = [piece for piece in comma_split_re.split(sub_sentence) if piece.strip()]
            if not pieces:
                continue
            first = pieces[0]
            if _LEADING_QUALIFIER_RE.match(first) or first.rstrip().endswith(("时", "的话")):
                segments.append("，".join(pieces))
            else:
                segments.extend(pieces)
    return segments


# A status guarantee is negated when an auxiliary/negation directly governs it,
# e.g. "does not fail fast", "will not return a non-zero status", "fails to
# return a non-zero status", "is unable to return", "不会 fail fast",
# "不返回非零状态". A bare "not installed" earlier in the segment does not count.
_STATUS_NEGATION_BEFORE_RE = re.compile(
    r"(?:does|do|will|would|should|could|can|is|are|was|were)\s+not\s+(?:\w+\s+){0,3}$|"
    r"(?:can't|won't|isn't|aren't|wasn't|weren't|couldn't|shouldn't|wouldn't|doesn't|don't|didn't)\s+"
    r"(?:\w+\s+){0,3}$|"
    r"(?:never|not)(?!\s+only)\s+(?:\w+\s+){0,3}$|"
    r"(?:is|are|was|were)?\s*not\s+(?:guaranteed|required|expected)\s+to\s+(?:\w+\s+){0,3}$|"
    r"(?:fails?|failed)\s+to\s+(?:\w+\s+){0,3}$|"
    r"(?:is|are|was|were)?\s*unable\s+to\s+(?:\w+\s+){0,3}$|"
    r"(?:不(?!但)|未|无法|不能|不会|未能)[^\n]{0,4}$",
    re.IGNORECASE,
)


def _ragas_failure_segments(text: str) -> list[str]:
    """Return clauses that describe an unavailable/failed evaluator.

    Clauses are split on sentence and comma punctuation, but a leading
    subordinate condition is merged with the clause it governs so the guarantees
    stay attached to the evaluator-failure condition.
    """
    segments: list[str] = []
    for line in text.splitlines():
        for segment in _split_clauses(line, _RAGAS_COMMA_SPLIT_RE):
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
        prefix = segment[max(0, match.start() - 40) : match.start()]
        if _STATUS_NEGATION_BEFORE_RE.search(prefix):
            continue
        return True
    return False


# A no-report guarantee is denied when the suppression itself is negated, e.g.
# "this does not mean no quality report", "并非不生成报告". The affirmative forms
# ("does not produce a report", "不生成报告") include the negation in the match
# itself, so only a meta-negation before the phrase counts.
_REPORT_NEGATION_BEFORE_RE = re.compile(
    r"(?:does|do|did)\s+not\s+(?:guarantee|assert|imply|ensure|mean)\s+(?:\w+\s+){0,2}$|"
    r"(?:doesn't|don't|didn't)\s+(?:guarantee|assert|imply|ensure|mean)\s+(?:\w+\s+){0,2}$|"
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
        for clause in _split_clauses(line, _LOCAL_VALIDATION_COMMA_SPLIT_RE):
            if LOCAL_VALIDATION_EXTERNAL_BOUNDARY_RE.search(clause):
                continue
            for pattern in STALE_LOCAL_VALIDATION_CLAIM_PATTERNS:
                match = re.search(pattern, clause, flags=re.IGNORECASE)
                if match:
                    claims.append(match.group(0))
                    break
    return claims


_LOCAL_VALIDATION_REQUIRED_DEPS = [
    re.compile(r"Redis", re.IGNORECASE),
    re.compile(r"nginx|反向代理", re.IGNORECASE),
    re.compile(r"Elasticsearch|(?<![A-Za-z])ES(?![A-Za-z])", re.IGNORECASE),
    re.compile(r"Prometheus", re.IGNORECASE),
]
_VALIDATION_SENTENCE_SPLIT_RE = re.compile(r"[；;。！!？?]+|\.(?=\s|$)")


def _local_validation_affirmative_scope(text: str) -> bool:
    """True when a sentence near the marker affirms all required local validations."""
    marker = "LOCAL_REAL_VALIDATION"
    normalized = text.replace("\n", " ")
    for match in re.finditer(marker, normalized):
        window = normalized[max(0, match.start() - 800) : match.end() + 800]
        for sentence in _VALIDATION_SENTENCE_SPLIT_RE.split(window):
            if not _affirmative_validation_in(sentence):
                continue
            if all(dependency.search(sentence) for dependency in _LOCAL_VALIDATION_REQUIRED_DEPS):
                return True
    return False


def check_local_runtime_validation_contract(errors: list[str]) -> None:
    """Canonical docs must affirm the completed local real-dependency validation."""
    for name in LOCAL_VALIDATION_DOCS:
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        marker = "LOCAL_REAL_VALIDATION"
        if marker not in text:
            fail(
                errors,
                f"{name}: must classify the completed Redis/nginx/ES/Prometheus validation as LOCAL_REAL_VALIDATION",
            )
        elif not _local_validation_affirmative_scope(text):
            fail(
                errors,
                f"{name}: must affirmatively state that Redis/nginx/Elasticsearch/Prometheus were validated locally",
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


# ═══════════════════════════════════════════════════════════════════════════
# Enterprise-readiness evidence guards
# ═══════════════════════════════════════════════════════════════════════════
#
# Each capability below has a *framework* state and a *result* state that must
# never be collapsed into one another. "Alert rules exist" is not "alerts fired";
# "an exporter is implemented" is not "tracing is closed-loop"; "a performance
# artifact harness exists" is not "QPS is verified". The presence of each result
# artifact is derived from the working tree, so the required wording follows the
# repository's real state rather than a hardcoded expectation.

PERFORMANCE_ARTIFACT_GLOB = "artifacts/performance/*/metadata.json"


def performance_artifact_exists() -> bool:
    """True when a performance run artifact is committed to the working tree."""
    return any(ROOT.glob(PERFORMANCE_ARTIFACT_GLOB))


#: Fields an OTLP closed-loop artifact must carry, and what each one has to look
#: like. Deliberately a floor: enough structure to mean "this claims to be a
#: completed run whose backend answered a query for one trace", and nothing more.
OTEL_EVIDENCE_SCHEMA_VERSION = 1
OTEL_EVIDENCE_TYPE = "otel_closed_loop"
OTEL_EVIDENCE_STATUS_EXECUTED = "EXECUTED"

#: A W3C/OTel trace id: 32 hex characters.
_OTEL_TRACE_ID_RE = re.compile(r"[0-9a-fA-F]{32}")


def _is_plain_int(value: object) -> bool:
    """True for a real integer. ``bool`` is excluded even though ``True == 1``."""
    return isinstance(value, int) and not isinstance(value, bool)


def is_valid_otel_runtime_evidence(payload: object) -> bool:
    """True when a decoded artifact satisfies the minimum closed-loop contract.

    A readable non-empty JSON object is not on its own evidence of anything, so a
    candidate has to state, in a checkable way, that it belongs to this evidence
    type, that the run actually completed, which backend answered, which trace it
    concerns, and that the query returned at least one span. Unknown extra fields
    are allowed so the contract can be tightened later without invalidating
    artifacts already written.

    This is structural validation only. It cannot show that the file was not
    hand-written, that the backend was really reached, or that the trace
    corresponds to any real request — those are provenance questions and are
    deliberately out of scope.
    """
    if not isinstance(payload, dict):
        return False
    version = payload.get("schema_version")
    if not _is_plain_int(version) or version != OTEL_EVIDENCE_SCHEMA_VERSION:
        return False
    if payload.get("evidence_type") != OTEL_EVIDENCE_TYPE:
        return False
    if payload.get("status") != OTEL_EVIDENCE_STATUS_EXECUTED:
        return False
    backend = payload.get("backend")
    if not isinstance(backend, str) or not backend.strip():
        return False
    trace_id = payload.get("trace_id")
    if not isinstance(trace_id, str) or not _OTEL_TRACE_ID_RE.fullmatch(trace_id):
        return False
    span_count = payload.get("queried_span_count")
    return _is_plain_int(span_count) and span_count > 0


def otel_runtime_evidence_exists() -> bool:
    """True when an OTLP closed-loop run has been recorded as a valid artifact.

    A closed-loop claim requires a recorded trace artifact. This repository keeps
    such evidence under ``monitoring/evidence/`` when a local run has actually
    happened; nothing is recorded today, which is why the runtime closed loop is
    PENDING rather than verified.

    Discovery is deliberately non-recursive and limited to ``*.json`` directly
    inside the directory: a hand-made ``monitoring/evidence/tmp/debug/x.json`` or
    a screenshot must not silently promote the top-level state. An unreadable or
    malformed file is skipped rather than raised, so one bad file can neither
    crash the checker nor become evidence just by existing, and it never blocks a
    valid sibling from being found. Beyond readability and parseability a
    candidate must satisfy :func:`is_valid_otel_runtime_evidence`.
    """
    evidence = ROOT / "monitoring" / "evidence"
    if not evidence.is_dir():
        return False
    for candidate in sorted(evidence.glob("*.json")):
        try:
            if not candidate.is_file():
                continue
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            # Not readable evidence. Skip it; keep looking for a real artifact.
            continue
        if is_valid_otel_runtime_evidence(payload):
            return True
    return False


def audit_action_events_exist() -> bool:
    """True when structured business-action audit events are implemented."""
    module = ROOT / "common/audit.py"
    if not module.exists():
        return False
    text = module.read_text(encoding="utf-8")
    return "def audit_event(" in text and "KNOWN_ACTIONS" in text


def prometheus_alert_rules_exist() -> bool:
    """True when alert rules are configured in the repository."""
    return (ROOT / "monitoring/prometheus/alerts.yml").is_file()


def grafana_dashboard_exists() -> bool:
    """True when a dashboard JSON is committed."""
    return (ROOT / "monitoring/grafana/dashboards").is_dir() and any(
        (ROOT / "monitoring/grafana/dashboards").glob("*.json")
    )


def slo_runbook_exists() -> bool:
    return (ROOT / "docs/slo-runbook.md").is_file()


def otlp_exporter_implemented() -> bool:
    """True when an opt-in OTLP exporter path is implemented."""
    module = ROOT / "monitoring/otel_exporter.py"
    return module.is_file() and "def build_span_processor(" in module.read_text(encoding="utf-8")


def enterprise_claim_errors(name: str, text: str) -> list[str]:
    """Return claims that outrun the evidence that exists for them."""
    errors: list[str] = []
    line_claims: list[str] = []

    def scan(patterns: list[str], evidence_present: bool, subject: str) -> None:
        """Flag matches unless the evidence they depend on is present.

        ``evidence_present=True`` means the claim is allowed, so the scan is
        skipped. An unconditional claim is checked by passing ``False`` with no
        artifact behind it — which is exactly the case for "an alert fired in
        production" and "an SLO was met": no repository artifact could ever
        justify either.
        """
        if evidence_present:
            return
        for pattern in patterns:
            for line in text.splitlines():
                match = re.search(pattern, line, flags=re.IGNORECASE)
                if not match:
                    continue
                if _NO_EVIDENCE_NEGATION_RE.search(line):
                    continue
                if _PROHIBITION_FRAME_RE.search(line):
                    continue
                if _PENDING_BOUNDARY_RE.search(line):
                    continue
                line_claims.append(f"{subject}: {match.group(0)!r}")

    # 1. Performance result claims with no committed artifact.
    scan(
        [
            r"(?:throughput|QPS)[^\n]{0,20}(?:verified|validated|measured|achieved|达标)",
            r"(?:P95|P99|p95|p99)[^\n]{0,16}(?:verified|validated|实测|达标|已达成)",
            r"load\s+test[^\n]{0,24}(?:result|proves|demonstrates)",
        ],
        performance_artifact_exists(),
        "performance result",
    )

    # 2. Tracing closed-loop claims with no recorded runtime evidence.
    scan(
        [
            r"(?:OTLP|OpenTelemetry|Jaeger)[^\n]{0,40}(?:closed[- ]?loop|闭环(?:验证|完成)?)(?:[^\n]{0,10}(?:validated|verified|完成))?",
            r"(?:span|trace)[^\n]{0,24}(?:exported|export)[^\n]{0,16}(?:verified|confirmed|已查询到)",
        ],
        otel_runtime_evidence_exists(),
        "OTLP closed loop",
    )

    # 3. Alerts claimed as configured when no rule file exists.
    scan(
        [
            r"(?:Prometheus|monitoring)[^\n]{0,30}alert(?:ing|s)?[^\n]{0,16}(?:configured|configured|in place|已配置|已启用)"
        ],
        prometheus_alert_rules_exist(),
        "alert rules",
    )

    # 4. Alerts claimed as having fired in production.
    scan(
        [
            r"(?:alerts?|\w*Alert\w*|Rag[A-Z]\w*)[^\n]{0,24}(?:fired|triggered)[^\n]{0,24}(?:production|线上|生产)",
            r"(?:production|线上|生产)[^\n]{0,24}(?:alerts?|\w*Alert\w*|Rag[A-Z]\w*)[^\n]{0,16}(?:fired|triggered)",
        ],
        # Unconditional: no committed artifact could justify this claim.
        False,
        "alerts fired",
    )

    # 5. Dashboard claimed as available with no dashboard committed.
    scan(
        [r"Grafana[^\n]{0,24}dashboard[^\n]{0,16}(?:available|provided|imported|已导入|可用)"],
        grafana_dashboard_exists(),
        "Grafana dashboard",
    )

    # 6. Enterprise audit claimed as implemented with no action events.
    scan(
        [r"(?:structured|企业级)[^\n]{0,16}audit[^\n]{0,24}(?:implemented|已实现|in place)"],
        audit_action_events_exist(),
        "enterprise audit",
    )

    # 7. SLO claimed as met rather than targeted.
    scan(
        [
            r"(?:availability|可用性)[^\n]{0,24}(?:currently|已)?(?:achieves|meets|达到|reached)[^\n]{0,12}99",
            r"(?:achieves|meets|reached|达到)[^\n]{0,12}99(?:\.\d+)?\s*%?[^\n]{0,16}(?:availability|可用性)",
            r"SLO[^\n]{0,16}(?:is|was)\s+met",
            r"(?:latency|延迟)[^\n]{0,16}(?:is|was)\s+met",
        ],
        # Unconditional: an achieved SLO needs production history, which this
        # repository does not and must not invent.
        False,
        "SLO achieved",
    )

    if line_claims:
        errors.append(f"{name}: claims outrun the available evidence: {line_claims}")
    return errors


# Explicit "this is a target, not a result" wording that exempts a line from the
# guards above. Broader than _NO_EVIDENCE_NEGATION_RE because it also accepts the
# target/design vocabulary these documents legitimately use.
_PENDING_BOUNDARY_RE = re.compile(
    r"DESIGN_TARGET|design\s+target|目标(?:值)?|PENDING|not\s+measured|未(?:测量|验证)|"
    r"still\s+pending|尚未|no\s+artifact|没有.{0,8}artifact|framework\s*=|"
    r"HISTORICAL_PRODUCTION|REPO_VERIFIED|LOCAL_REAL_VALIDATION|"
    # An explicit denial of the claim itself: "no alert has fired in production",
    # "no SLO has been met". These documents are required to write the denial,
    # so it must not be read as the claim.
    r"\bno\s+[A-Za-z][\w\s]{0,30}(?:fired|triggered|achieved|been\s+met|has\s+been\s+met)\b|"
    r"\bnot\s+[A-Za-z][\w\s]{0,30}(?:fired|triggered|been\s+met)\b",
    re.IGNORECASE,
)


def check_enterprise_readiness_contracts(errors: list[str]) -> None:
    """Keep every enterprise-readiness claim tied to the evidence that exists."""
    for name in (
        "README.md",
        "PRD.md",
        "docs/README.md",
        "docs/operations-guide.md",
        "docs/pre-launch-checklist.md",
        "docs/slo-runbook.md",
        "docs/repository-truth-audit.md",
        "docs/interview-evidence-map.md",
        "docs/interview-architecture-baseline.md",
        "docs/deployment-guide.md",
    ):
        path = ROOT / name
        if not path.exists():
            continue
        errors.extend(enterprise_claim_errors(name, path.read_text(encoding="utf-8")))


def check_enterprise_readiness_coverage(errors: list[str]) -> None:
    """The implemented capabilities must be discoverable from the docs index."""
    index = ROOT / "docs/README.md"
    if index.exists():
        text = index.read_text(encoding="utf-8")
        if "slo-runbook.md" not in text:
            fail(errors, "docs/README.md must index the SLO/runbook document")

    runbook = ROOT / "docs/slo-runbook.md"
    if runbook.exists():
        text = runbook.read_text(encoding="utf-8")
        if "DESIGN_TARGET" not in text:
            fail(errors, "docs/slo-runbook.md must classify its objectives as DESIGN_TARGET")
        # A runbook that claims to be a contract must also refuse to claim it was met.
        if not _SLO_TARGET_BOUNDARY_RE.search(text):
            fail(
                errors,
                "docs/slo-runbook.md must state that no SLO has been met (no achieved-SLO claim)",
            )

    alerts = ROOT / "monitoring/prometheus/alerts.yml"
    if alerts.exists():
        text = alerts.read_text(encoding="utf-8")
        if "DESIGN_TARGET" not in text:
            fail(errors, "monitoring/prometheus/alerts.yml must label its thresholds DESIGN_TARGET")
        if "NOT VALIDATED IN PRODUCTION" not in text.upper():
            fail(
                errors,
                "monitoring/prometheus/alerts.yml must state that alerting is not validated in production",
            )

    performance_readme = ROOT / "artifacts/performance/README.md"
    if performance_readme.exists():
        text = performance_readme.read_text(encoding="utf-8")
        if "Not executed is not zero" not in text:
            fail(errors, "artifacts/performance/README.md must state the null-not-zero rule")
        for status in ("EXECUTED", "PARTIAL", "BLOCKED"):
            if status not in text:
                fail(errors, f"artifacts/performance/README.md must document the {status} status")


_SLO_TARGET_BOUNDARY_RE = re.compile(
    r"no\s+SLO\s+has\s+been\s+met|not\s+a\s+report|is\s+a\s+\*\*contract\*\*",
    re.IGNORECASE,
)


# ── 1d. the legacy Jaeger thrift-agent config must not come back ───────────
#
# The canonical export path is OpenTelemetry → OTLP. The Jaeger *backend* in
# docker-compose.observability.yml is a current, working part of that path: it
# receives OTLP on 4317/4318. Only the older thrift-*agent* configuration is
# retired — JAEGER_AGENT_HOST/PORT and config.json's monitoring.jaeger block —
# and that had no canonical reader at all, so shipping it only invited the
# question "which exporter does this actually use?".
#
# The target here is that agent *configuration*, never the word "jaeger". A guard
# that matched the bare token would delete the working OTLP backend along with the
# dead config, so the patterns below are all agent-specific, and the guard also
# asserts the backend is still there.

#: Agent-specific legacy markers. Deliberately never a bare "jaeger".
_LEGACY_JAEGER_AGENT_RE = re.compile(
    r"JAEGER_AGENT_(?:HOST|PORT)|"
    r"monitoring\s*[.]\s*jaeger|"
    r"\bagent_(?:host|port)\b|"
    r"opentelemetry-exporter-jaeger",
    re.IGNORECASE,
)

#: Where legacy agent configuration could be reintroduced. The guard's own source
#: is not in this list, so naming the markers above cannot trip the guard.
_LEGACY_JAEGER_AGENT_SURFACES = (
    "config.json",
    ".env.example",
    "README.md",
    "PRD.md",
    "docs/README.md",
    "docs/operations-guide.md",
    "docs/deployment-guide.md",
    "docs/pre-launch-checklist.md",
    "docs/slo-runbook.md",
    "docs/repository-truth-audit.md",
    "docs/interview-architecture-baseline.md",
    "docs/interview-evidence-map.md",
)


def legacy_jaeger_agent_errors(name: str, text: str) -> list[str]:
    """Return errors for a file that reintroduces the retired Jaeger agent config."""
    errors: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = _LEGACY_JAEGER_AGENT_RE.search(line)
        if match:
            errors.append(
                f"{name}:{line_number}: reintroduces the retired Jaeger thrift-agent "
                f"configuration ({match.group(0)!r}); the canonical export path is "
                "OpenTelemetry → OTLP, so drop this instead of documenting it"
            )
    return errors


def check_legacy_jaeger_agent_config_is_absent(errors: list[str]) -> None:
    """Retired agent config stays retired, while the OTLP backend stays working."""
    for name in _LEGACY_JAEGER_AGENT_SURFACES:
        path = ROOT / name
        if not path.exists():
            continue
        errors.extend(legacy_jaeger_agent_errors(name, path.read_text(encoding="utf-8")))

    # The point of retiring the agent config is not to retire Jaeger. The optional
    # overlay must keep a backend that receives OTLP, otherwise a future "cleanup"
    # aimed at the dead config silently removes a working capability.
    overlay = ROOT / "docker-compose.observability.yml"
    if not overlay.exists():
        return
    text = overlay.read_text(encoding="utf-8")
    try:
        import yaml

        services = set(yaml.safe_load(text).get("services", {}))
    except Exception as exc:  # pragma: no cover - malformed compose is CI's other job
        fail(errors, f"cannot parse docker-compose.observability.yml for the Jaeger backend check: {exc}")
        return
    if "jaeger" not in services:
        fail(
            errors,
            "docker-compose.observability.yml must keep the optional jaeger backend: it receives "
            "OTLP spans and is not the retired thrift-agent configuration",
        )


def check_observability_is_optional(errors: list[str]) -> None:
    """The canonical deployment must not require the observability stack."""
    base = ROOT / "docker-compose.yml"
    overlay = ROOT / "docker-compose.observability.yml"
    if not base.exists() or not overlay.exists():
        return
    import yaml

    try:
        base_services = set(yaml.safe_load(base.read_text(encoding="utf-8")).get("services", {}))
        overlay_services = set(yaml.safe_load(overlay.read_text(encoding="utf-8")).get("services", {}))
    except Exception as exc:  # pragma: no cover - malformed compose is CI's other job
        fail(errors, f"cannot parse compose files for the optionality check: {exc}")
        return

    if base_services & overlay_services:
        fail(
            errors,
            "docker-compose.observability.yml must not redefine canonical services: "
            f"{sorted(base_services & overlay_services)}",
        )
    for required in ("prometheus", "jaeger", "grafana"):
        if required not in overlay_services:
            fail(errors, f"docker-compose.observability.yml must provide {required} behind the overlay")


# ═══════════════════════════════════════════════════════════════════════════
# Post-#21 drift guards
# ═══════════════════════════════════════════════════════════════════════════
#
# Three classes of drift survived PR #21 because no guard covered them:
#   1. docs denying the *existence* of an implemented capability;
#   2. operational docs naming a `rag_*` series the collector never emits;
#   3. the audit tracker losing the implemented / externally-pending distinction.
# Each is derived from the working tree, never from GitHub or the clock.


# ── 1. exporter existence vs exporter default state ─────────────────────────
#
# "no exporter configured" is true of a default install and false as a statement
# about the implementation. Free text cannot separate those two on one line, so
# instead of trying to outlaw the phrase this guard requires the *implementation*
# to be named wherever tracing/exporter is discussed. A doc that only ever says an
# exporter is absent is the drift; a doc that names `monitoring/otel_exporter.py`
# or `OTEL_EXPORT_ENABLED` has made the default-off scope explicit.

#: Tokens that name the exporter implementation or its switch.
_EXPORTER_IMPLEMENTATION_TOKENS = (
    "monitoring/otel_exporter.py",
    "otel_exporter",
    "OTEL_EXPORT_ENABLED",
    "requirements-otel.txt",
    "OTLP",
    "otlp",
)

#: Docs whose tracing/exporter discussion must name the implementation.
EXPORTER_TRUTH_DOCS = (
    "README.md",
    "docs/README.md",
    "docs/repository-truth-audit.md",
    "docs/interview-architecture-baseline.md",
    "docs/interview-evidence-map.md",
    "docs/operations-guide.md",
    "docs/slo-runbook.md",
    "docs/pre-launch-checklist.md",
)

_TRACING_SUBJECT_RE = re.compile(
    r"OpenTelemetry|OTel|OTLP|Jaeger|exporter|追踪|导出",
    re.IGNORECASE,
)


def exporter_truth_claim_errors(name: str, text: str) -> list[str]:
    """Return errors for a doc that discusses tracing without naming the exporter."""
    if not _TRACING_SUBJECT_RE.search(text):
        return []
    if any(token in text for token in _EXPORTER_IMPLEMENTATION_TOKENS):
        return []
    return [
        f"{name}: discusses tracing/export but never names the exporter implementation "
        "(monitoring/otel_exporter.py / OTEL_EXPORT_ENABLED / requirements-otel.txt); "
        "an exporter that exists must not be described as absent"
    ]


def check_exporter_truth_contract(errors: list[str]) -> None:
    """The implemented exporter may not be documented as non-existent."""
    if not otlp_exporter_implemented():
        return
    for name in EXPORTER_TRUTH_DOCS:
        path = ROOT / name
        if not path.exists():
            continue
        errors.extend(exporter_truth_claim_errors(name, path.read_text(encoding="utf-8")))


# The interview baseline is the single architecture truth document, so it must
# carry both halves of the exporter split explicitly rather than by implication.
_BASELINE_EXPORTER_IMPLEMENTATION_RE = re.compile(
    r"OTEL_EXPORT_ENABLED|monitoring/otel_exporter\.py|requirements-otel\.txt",
)
_BASELINE_CLOSED_LOOP_PENDING_RE = re.compile(
    r"PENDING|待验证|pending|未验证",
    re.IGNORECASE,
)


def check_interview_baseline_exporter_split(errors: list[str]) -> None:
    """The interview baseline must state exporter implementation AND pending loop."""
    path = ROOT / "docs/interview-architecture-baseline.md"
    if not path.exists() or not otlp_exporter_implemented():
        return
    text = path.read_text(encoding="utf-8")
    if not _TRACING_SUBJECT_RE.search(text):
        return
    if not _BASELINE_EXPORTER_IMPLEMENTATION_RE.search(text):
        fail(errors, "docs/interview-architecture-baseline.md: must name the OTLP exporter implementation")
    if not _BASELINE_CLOSED_LOOP_PENDING_RE.search(text):
        fail(
            errors,
            "docs/interview-architecture-baseline.md: must record the OTLP runtime closed loop as pending",
        )


# ── 1b. one canonical row per capability in the evidence map ────────────────
#
# The capability table is the interview contract: every row is one claim with
# one level. A capability listed twice is worse than a missing row, because the
# two rows can disagree about the level and an interviewer quoting either one is
# quoting an arbitrary pick. This guard derives the capability names from the
# table itself, so it holds for any capability, present or future.

INTERVIEW_EVIDENCE_MAP = "docs/interview-evidence-map.md"
_VOCABULARY_SECTION = "## Classification vocabulary"
_CAPABILITY_SECTION = "## Capability evidence"


# One small markdown table reader, shared by every guard that inspects a
# `## section` -> table -> column shape in this document. It is deliberately not a
# general markdown parser: the tables here are flat pipe tables, and a full parser
# would be a dependency and a second source of formatting rules.
def _section_index(lines: list[str], heading: str) -> int | None:
    """Index of an exact `## heading`, or None when the section is absent."""
    return next((i for i, line in enumerate(lines) if line.strip() == heading), None)


def _row_cells(line: str) -> list[str]:
    return [part.strip() for part in line.strip("|").split("|")]


def _header_row_index(lines: list[str], section_index: int, first_column: str) -> int | None:
    """Index of the header row of the first table in a section whose first column matches."""
    prefix = f"| {first_column.lower()} |"
    return next(
        (i for i in range(section_index + 1, len(lines)) if lines[i].strip().lower().startswith(prefix)),
        None,
    )


def _table_cells(lines: list[str], header_index: int) -> Iterator[tuple[int, list[str]]]:
    """Yield (1-based line number, cells) for the data rows of one contiguous table.

    Reading stops at the first non-blank line that is not a `|` row, so a table
    further down the same document is prose here, not a continuation of this one.
    """
    for offset in range(header_index + 1, len(lines)):
        line = lines[offset]
        if not line.startswith("|"):
            if line.strip():
                return
            continue
        if "---" in line:
            continue
        yield offset + 1, _row_cells(line)


def _column_index(cells: list[str], name: str) -> int | None:
    return next((i for i, cell in enumerate(cells) if cell.lower() == name), None)


def duplicate_capability_errors(name: str, text: str) -> list[str]:
    """Return errors for capability names repeated in the main capability table."""
    lines = text.splitlines()
    section = _section_index(lines, _CAPABILITY_SECTION)
    if section is None:
        return [f"{name}: is missing the {_CAPABILITY_SECTION!r} section"]

    # Only the contiguous run of `|` lines that starts at the capability header is
    # the main table; tables further down the document are prose, not capability
    # rows, so parsing stops at the first non-table line after the header.
    header = _header_row_index(lines, section, "capability")
    if header is None:
        return [f"{name}: {_CAPABILITY_SECTION!r} has no '| Capability |' header row"]

    first_seen: dict[str, int] = {}
    errors: list[str] = []
    for line_number, cells in _table_cells(lines, header):
        if not cells or not cells[0]:
            continue
        # Case-folded so a `Redis session` / `Redis Session` pair is one capability.
        key = cells[0].casefold()
        if key in first_seen:
            errors.append(
                f"{name}: capability {cells[0]!r} has more than one row in "
                f"{_CAPABILITY_SECTION!r} (lines {first_seen[key]} and {line_number}); "
                "merge them into a single canonical row"
            )
            continue
        first_seen[key] = line_number
    return errors


def check_capability_rows_are_unique(errors: list[str]) -> None:
    """The interview evidence map must give each capability exactly one row."""
    path = ROOT / INTERVIEW_EVIDENCE_MAP
    if not path.exists():
        return
    errors.extend(duplicate_capability_errors(INTERVIEW_EVIDENCE_MAP, path.read_text(encoding="utf-8")))


# ── 1c. capability rows may only use levels the vocabulary defines ──────────
#
# The classification vocabulary is this document's own source of truth for which
# evidence levels exist. A capability row naming a level the vocabulary does not
# define makes the taxonomy contradict itself — the failure that shipped
# `HISTORICAL` in the Jaeger row before the vocabulary caught up. The legal set is
# parsed out of the vocabulary table rather than kept as a Python allow-list, so
# adding a level is a documentation-only edit and a new level can never be silently
# absent from the code's idea of what is legal.
#
# Scope is the main table's Level column only. Prose, code fences and the other
# tables in this document legitimately mention non-level tokens (`EXECUTED`,
# `OTEL_EXPORT_ENABLED`, the `rag_*` series), and validating those would be noise.

#: A backticked identifier, which is how both tables spell a level.
_BACKTICK_TOKEN_RE = re.compile(r"`([A-Za-z][A-Za-z0-9_]*)`")


def undefined_evidence_level_errors(name: str, text: str) -> list[str]:
    """Return errors for capability rows using an evidence level the vocabulary lacks.

    Every backticked token in a main-table Level cell is checked, so a cell such as
    "`REPO_VERIFIED` (framework) / `PENDING` (result)" validates each level on its own
    and one defined level cannot excuse an undefined one beside it.
    """
    lines = text.splitlines()

    # ── the vocabulary, which defines what a level is
    vocabulary_section = _section_index(lines, _VOCABULARY_SECTION)
    if vocabulary_section is None:
        return [f"{name}: is missing the {_VOCABULARY_SECTION!r} section"]
    vocabulary_header = _header_row_index(lines, vocabulary_section, "level")
    if vocabulary_header is None:
        return [f"{name}: could not parse evidence vocabulary: {_VOCABULARY_SECTION!r} has no '| Level |' header row"]
    level_column = _column_index(_row_cells(lines[vocabulary_header]), "level")
    if level_column is None:
        return [f"{name}: could not parse evidence vocabulary: the vocabulary table has no 'Level' column"]
    defined: set[str] = set()
    for _, cells in _table_cells(lines, vocabulary_header):
        if level_column < len(cells):
            defined.update(_BACKTICK_TOKEN_RE.findall(cells[level_column]))
    if not defined:
        # An empty vocabulary must not read as "everything is allowed" or "nothing to
        # check" — either would silently disable this guard.
        return [f"{name}: could not parse evidence vocabulary: the Level column defines no backticked evidence level"]

    # ── the capability table, which may only use them
    capability_section = _section_index(lines, _CAPABILITY_SECTION)
    if capability_section is None:
        return [f"{name}: is missing the {_CAPABILITY_SECTION!r} section"]
    capability_header = _header_row_index(lines, capability_section, "capability")
    if capability_header is None:
        return [f"{name}: {_CAPABILITY_SECTION!r} has no '| Capability |' header row"]
    capability_level_column = _column_index(_row_cells(lines[capability_header]), "level")
    if capability_level_column is None:
        return [f"{name}: {_CAPABILITY_SECTION!r} has no 'Level' column"]

    rows = list(_table_cells(lines, capability_header))
    if not rows:
        return [f"{name}: could not parse the {_CAPABILITY_SECTION!r} main table: it has no capability rows"]

    errors: list[str] = []
    for line_number, cells in rows:
        if capability_level_column >= len(cells):
            continue
        for level in _BACKTICK_TOKEN_RE.findall(cells[capability_level_column]):
            if level in defined:
                continue
            errors.append(
                f"{name}: undefined evidence level in {_CAPABILITY_SECTION!r}: {level} (line {line_number}); "
                f"define it in {_VOCABULARY_SECTION!r} or use one of {sorted(defined)}"
            )
    return errors


def check_evidence_levels_are_defined(errors: list[str]) -> None:
    """Every Capability evidence level must be defined by the classification vocabulary."""
    path = ROOT / INTERVIEW_EVIDENCE_MAP
    if not path.exists():
        return
    errors.extend(undefined_evidence_level_errors(INTERVIEW_EVIDENCE_MAP, path.read_text(encoding="utf-8")))


# ── 1d. one canonical taxonomy for every current document ──────────────────
#
# The evidence map owns the vocabulary. A second, parallel status vocabulary in
# another current document is the drift this guard removes: two vocabularies for
# one kind of claim means two documents can disagree about the same evidence, and
# a reader cannot tell which one is authoritative. That is exactly how
# `docs/repository-truth-audit.md` ended up with `VERIFIED`/`PARTIAL`/`STALE`
# beside the evidence map's own levels.
#
# The legal levels are read out of the vocabulary table instead of being restated
# in Python. A Python copy would be a third place to forget to update, which is how
# the two vocabularies diverged in the first place.

#: The section of the evidence map that declares run outcomes, i.e. tokens that
#: describe one execution rather than an evidence level.
_RUN_OUTCOME_SECTION = "## Run outcomes that are not evidence levels"

#: Column headers that carry an evidence classification. Deliberately narrow: a
#: `Claim`, `Evidence basis` or prose cell is not a classification, and validating
#: those would reject honest prose. The Chinese headers are included because this
#: repository documents its own architecture baseline bilingually, and a
#: Chinese-labelled status column is where a second vocabulary would reappear.
EVIDENCE_COLUMN_HEADERS = frozenset(
    {
        "level",
        "status",
        "classification",
        "evidence level",
        "evidence classification",
        "truth status",
        "等级",
        "状态",
    }
)

#: A classification token: backticked upper snake case. This is the only shape an
#: evidence level is written in, so a lowercase identifier or a prose sentence can
#: never be mistaken for one.
_CLASSIFICATION_TOKEN_RE = re.compile(r"`([A-Z][A-Z0-9_]+)`")

#: A vocabulary token, which may contain single spaces so that a multi-word run
#: outcome such as `NOT RUN` is still parsed as one declared token.
_VOCABULARY_TOKEN_RE = re.compile(r"`([A-Za-z][A-Za-z0-9_]*(?: [A-Za-z][A-Za-z0-9_]*)*)`")

#: A table row separator, e.g. `|---|---|`.
_ROW_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")

#: The canonical level meaning "a superseded artifact in this repository". Named
#: separately because the guard below asserts a *semantic* rule about it, not a
#: vocabulary rule.
SUPERSEDED_LEVEL = "HISTORICAL"


def _evidence_vocabulary_tokens(text: str, heading: str, first_column: str) -> set[str]:
    """Backticked tokens of the first column of the first table under ``heading``."""
    lines = text.splitlines()
    section = _section_index(lines, heading)
    if section is None:
        return set()
    header = _header_row_index(lines, section, first_column)
    if header is None:
        return set()
    column = _column_index(_row_cells(lines[header]), first_column)
    if column is None:
        return set()
    tokens: set[str] = set()
    for _, cells in _table_cells(lines, header):
        if column < len(cells):
            tokens.update(_VOCABULARY_TOKEN_RE.findall(cells[column]))
    return tokens


def canonical_evidence_levels() -> set[str]:
    """The evidence levels this repository defines, parsed from the canonical table."""
    path = ROOT / INTERVIEW_EVIDENCE_MAP
    if not path.exists():
        return set()
    return _evidence_vocabulary_tokens(path.read_text(encoding="utf-8"), _VOCABULARY_SECTION, "level")


def run_outcome_tokens() -> set[str]:
    """Tokens that describe one execution rather than an evidence level."""
    path = ROOT / INTERVIEW_EVIDENCE_MAP
    if not path.exists():
        return set()
    return _evidence_vocabulary_tokens(path.read_text(encoding="utf-8"), _RUN_OUTCOME_SECTION, "token")


def _pipe_table_blocks(lines: list[str]) -> Iterator[tuple[int, int]]:
    """Yield (start, end) line indices of each maximal run of consecutive `|` rows."""
    start: int | None = None
    for index, line in enumerate(lines):
        if line.startswith("|"):
            if start is None:
                start = index
        elif start is not None:
            yield start, index
            start = None
    if start is not None:
        yield start, len(lines)


def evidence_classification_errors(name: str, text: str, legal: set[str]) -> list[str]:
    """Return errors for classification cells naming something outside ``legal``.

    Every classification column of every table in the document is checked, so a
    second status vocabulary cannot be introduced by adding a table to a document
    that already had one, and no document is exempt because its level column
    happens to be spelled differently.
    """
    if not legal:
        return [
            f"{name}: cannot validate evidence classifications: the canonical vocabulary in "
            f"{INTERVIEW_EVIDENCE_MAP} is missing or unparsable"
        ]

    lines = text.splitlines()
    errors: list[str] = []
    classified_tables = 0
    for start, _ in _pipe_table_blocks(lines):
        columns = _row_cells(lines[start])
        if any(_ROW_SEPARATOR_CELL_RE.match(cell) for cell in columns):
            continue
        classified = [position for position, cell in enumerate(columns) if cell.lower() in EVIDENCE_COLUMN_HEADERS]
        if not classified:
            continue
        classified_tables += 1
        for line_number, cells in _table_cells(lines, start):
            for position in classified:
                if position >= len(cells):
                    errors.append(
                        f"{name}: line {line_number}: malformed row; the evidence classification column is missing"
                    )
                    continue
                cell = cells[position]
                tokens = _CLASSIFICATION_TOKEN_RE.findall(cell)
                for token in tokens:
                    if token in legal:
                        continue
                    errors.append(
                        f"{name}: line {line_number}: {token!r} is not a canonical evidence level; "
                        f"use one of {sorted(legal)}, or declare {token!r} as a run outcome in "
                        f"{_RUN_OUTCOME_SECTION!r}"
                    )
                # An opaque status written in any language carries no token at all,
                # which an upper-snake token rule alone would let through.
                if not tokens:
                    errors.append(
                        f"{name}: line {line_number}: the evidence classification cell {cell!r} names no "
                        f"canonical evidence level; use one of {sorted(legal)}"
                    )

    # A document that stops classifying evidence loses the guard entirely, so
    # renaming the column away is itself the drift this catches.
    if classified_tables == 0:
        errors.append(
            f"{name}: has no table with an evidence classification column "
            f"({'/'.join(sorted(EVIDENCE_COLUMN_HEADERS))}); a document that classifies claims must "
            "carry the canonical vocabulary"
        )
    return errors


#: Current, interview-facing and repository-truth documentation.
#:
#: The superseded plans formerly stored under `docs/superpowers/` — a *historical
#: path*, removed from the current branch and preserved only in Git history — are
#: deliberately absent here. The rationale still holds: a superseded plan is not
#: evidence and is not held to the current taxonomy. It is recorded as a
#: historical path rather than a live one so that no reader of this file expects
#: to open that directory, and so a reference to it cannot pass for a current
#: document. See `check_removed_plan_path_is_historical`.
EVIDENCE_VOCABULARY_DOCS = (
    "docs/interview-evidence-map.md",
    "docs/interview-architecture-baseline.md",
    "docs/repository-truth-audit.md",
    "docs/open-source-hardcoding-audit.md",
    "docs/validation/v2.5-runtime-security-validation.md",
    "docs/validation/real-ragas-evaluation.md",
)


def check_evidence_vocabulary_is_canonical(errors: list[str]) -> None:
    """No current document may classify evidence with a non-canonical level."""
    legal = canonical_evidence_levels()
    for name in EVIDENCE_VOCABULARY_DOCS:
        path = ROOT / name
        if not path.exists():
            fail(errors, f"{name} is missing")
            continue
        errors.extend(evidence_classification_errors(name, path.read_text(encoding="utf-8"), legal))


#: The docs index section whose bullet list is the repository's vocabulary
#: inventory. It must enumerate the canonical levels exactly: an inventory that
#: drifts from the taxonomy is how a reader ends up trusting the wrong one.
_DOCS_INDEX_VOCABULARY_SECTION = "## Evidence vocabulary"

_BULLET_ITEM_RE = re.compile(r"^\s*[-*]\s")


def docs_index_vocabulary_errors(name: str, text: str, levels: set[str]) -> list[str]:
    """Return errors for an inventory that is not exactly the canonical vocabulary."""
    if not levels:
        return [
            f"{name}: cannot check the vocabulary inventory: the canonical vocabulary in "
            f"{INTERVIEW_EVIDENCE_MAP} is missing or unparsable"
        ]
    lines = text.splitlines()
    section = _section_index(lines, _DOCS_INDEX_VOCABULARY_SECTION)
    if section is None:
        return [f"{name}: is missing the {_DOCS_INDEX_VOCABULARY_SECTION!r} section"]

    # Only the bullet list is the inventory. The prose under it explains where the
    # vocabulary lives and which tokens are run outcomes, and those tokens are
    # named on purpose.
    listed: set[str] = set()
    for line in lines[section + 1 :]:
        if line.startswith("#"):
            break
        if _BULLET_ITEM_RE.match(line):
            listed.update(_VOCABULARY_TOKEN_RE.findall(line))

    errors: list[str] = []
    for token in sorted(levels - listed):
        errors.append(f"{name}: {_DOCS_INDEX_VOCABULARY_SECTION!r} omits the canonical evidence level {token!r}")
    for token in sorted(listed - levels):
        errors.append(
            f"{name}: {_DOCS_INDEX_VOCABULARY_SECTION!r} lists {token!r}, which is not a canonical "
            f"evidence level; the inventory must match the vocabulary in {INTERVIEW_EVIDENCE_MAP} exactly"
        )
    return errors


def check_docs_index_vocabulary(errors: list[str]) -> None:
    """docs/README.md's vocabulary inventory must equal the canonical vocabulary."""
    path = ROOT / "docs/README.md"
    if not path.exists():
        fail(errors, "docs/README.md is missing")
        return
    errors.extend(
        docs_index_vocabulary_errors("docs/README.md", path.read_text(encoding="utf-8"), canonical_evidence_levels())
    )


#: The canonical classification vocabulary, resolved from the document at import
#: time. It is empty when the evidence map cannot be read or parsed, and every
#: guard that uses it treats empty as "cannot validate", so a damaged vocabulary
#: fails closed instead of silently accepting anything.
_CLASSIFICATION_TOKENS = frozenset(canonical_evidence_levels())


# ── 2. operational `rag_*` references ───────────────────────────────────────
#
# The exporter publishes raw counters and gauges. A ratio such as
# `rag_cache_hit_rate` is computed by `/api/stats` or by a PromQL expression, and
# naming it as if it were a series sends an operator looking for a target that does
# not exist. The inventory below is read statically out of the collector so the
# guard needs no import of the application and stays deterministic.

COLLECTOR_MODULE = "monitoring/otel_tracer.py"
EXPORTER_MODULE = "monitoring/otel_exporter.py"

#: Method names on the collector that write a metric.
_METRIC_WRITER_METHODS = frozenset(
    {
        "increment",
        "set_gauge",
        "observe_histogram",
        "set_active_requests",
        "set_exporter_state",
        "set_redis_degraded",
        "record_prefix_cache_hit",
        "record_prefix_cache_miss",
        "record_cache_epoch_switch",
        "record_redis_degraded",
    }
)

#: Collector attributes that hold metric names directly.
_METRIC_STORE_ATTRS = frozenset({"_counters", "_gauges", "_histograms"})

#: `rag_*`-shaped tokens that are not Prometheus series. Qdrant collection names
#: share the prefix and would otherwise be false positives.
NON_METRIC_RAG_NAMES = frozenset({"rag_text_768", "rag_image_512"})

#: Names derived from emitted counters rather than emitted themselves. A doc may
#: use these only where it presents them as derived.
DERIVED_METRIC_NAMES = frozenset(
    {
        "rag_cache_hit_rate",
        "rag_rewrite_fallback_rate",
        "rag_blip_trigger_rate",
        "rag_nli_contradiction_rate",
        "rag_cache_failure_rate",
        "rag_http_responses_total",
    }
)

#: Markers that make a derived name acceptable: an explicit PromQL expression, an
#: explicit pointer at `/api/stats`, or an explicit statement that it does not exist.
_DERIVATION_MARKER_RE = re.compile(
    r"PromQL|rate\(|increase\(|sum\(|clamp_min\(|/api_stats|"
    r"derived|ratio|计算|比率|不存在|没有对应的|并非|"
    r"there\s+is\s+no|there\s+are\s+no|no\s+such|does\s+not\s+exist|"
    r"is\s+not\s+a\s+(?:real\s+)?(?:prometheus\s+)?(?:series|metric)|"
    r"never\s+emitted|不是\s*(?:一个)?(?:真实)?(?:series|指标)",
    re.IGNORECASE,
)

#: Documents whose `rag_*` references name series this repository claims to emit.
#:
#: Originally the four operational guides, on the reasoning that only those carry
#: references an operator pastes into a query. That reasoning turned out to be too
#: narrow for the two properties this repository states most loudly: the evidence map
#: and the truth audit both enumerate emitted series as part of classifying evidence,
#: and the README names `/api/metrics` as the endpoint that carries them. A phantom
#: series written into any of those three reads exactly like a real one and nothing
#: failed, which is the same failure this guard exists to catch, just one document
#: class over.
OPERATIONAL_METRIC_DOCS = (
    "README.md",
    "docs/operations-guide.md",
    "docs/slo-runbook.md",
    "docs/pre-launch-checklist.md",
    "docs/deployment-guide.md",
    "docs/interview-evidence-map.md",
    "docs/repository-truth-audit.md",
)


def _literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _prefix_from_formatted(node: ast.AST) -> str | None:
    """Return the literal head of an f-string / ``"a".format()`` style name."""
    if isinstance(node, ast.JoinedStr):
        head = ""
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                head += part.value
            else:
                break
        return head or None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return _literal(node.left)
    return None


def _module_constant(path: Path, name: str) -> str | None:
    """Return a module-level string constant, e.g. EXPORTER_ENABLED_METRIC."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                return _literal(node.value)
    return None


def _prometheus_name(metric: str) -> str:
    return "rag_" + metric.replace(".", "_").replace("-", "_")


def _collect_metric_names(tree: ast.AST) -> tuple[set[str], set[str], set[str]]:
    """Return ``(plain names, histogram names, formatted-name prefixes)``.

    A metric name reaches ``/api/metrics`` through
    ``MetricsCollector.to_prometheus_text``, which turns ``a.b`` into ``rag_a_b``.
    Three shapes occur:

    * plain literals written to a counter or gauge — emitted as-is;
    * histogram observations — additionally exposed as a ``_seconds`` summary with a
      ``_count`` companion;
    * names assembled at runtime (``f"cache.hit.{level}"``) — no fixed spelling
      exists, so only the Prometheus-visible prefix can be recorded.

    Keeping the three apart matters: expanding ``_seconds`` over a gauge would invent
    series that the exporter never writes.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Constant, ast.JoinedStr, ast.BinOp)):
            literal = _literal(node.value) or _prefix_from_formatted(node.value)
            if literal is None:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    aliases.setdefault(target.id, literal)

    def resolve(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return aliases.get(node.id)
        return _literal(node) or _prefix_from_formatted(node)

    plain: set[str] = set()
    histograms: set[str] = set()
    prefixes: set[str] = set()

    for node in ast.walk(tree):
        is_histogram = False
        args: tuple[ast.AST, ...] = ()
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _METRIC_WRITER_METHODS:
                args = tuple(node.args)
                is_histogram = node.func.attr == "observe_histogram"
        elif isinstance(node, ast.Subscript):
            owner = node.value
            if isinstance(owner, ast.Attribute) and owner.attr in _METRIC_STORE_ATTRS:
                args = (node.slice,)
                is_histogram = owner.attr == "_histograms"
        if not args:
            continue
        raw = resolve(args[0])
        if not raw:
            continue
        if raw.endswith((".", "_")) or "{" in raw:
            prefixes.add(_prometheus_name(raw))
        elif is_histogram:
            histograms.add(raw)
        else:
            plain.add(raw)

    return plain, histograms, prefixes


def emitted_prometheus_metrics() -> set[str]:
    """Return every ``rag_*`` series the canonical collector can emit.

    Derived by reading the collector's own metric-name literals rather than by
    importing and driving it, so the guard stays offline and cannot fail because of
    an unrelated import error. Every emission in the collector goes through a string
    literal, optionally an f-string, so the literals are the authoritative list of
    what can appear on ``/api/metrics``.

    Names assembled at runtime have no single spelling and are returned separately by
    :func:`emitted_prometheus_prefixes`.
    """
    collector_path = ROOT / COLLECTOR_MODULE
    if not collector_path.exists():
        return set()
    tree = ast.parse(collector_path.read_text(encoding="utf-8"), filename=str(collector_path))
    plain, histograms, _ = _collect_metric_names(tree)

    exporter_path = ROOT / EXPORTER_MODULE
    if exporter_path.exists():
        enabled_metric = _module_constant(exporter_path, "EXPORTER_ENABLED_METRIC")
        if enabled_metric:
            plain.add(enabled_metric)

    series = {_prometheus_name(name) for name in plain}
    summaries = {_prometheus_name(name) for name in histograms}
    series |= summaries | {f"{name}_seconds" for name in summaries}
    series |= {f"{name}_seconds_count" for name in summaries}
    series.add("rag_uptime_seconds")
    return series


def emitted_prometheus_prefixes() -> set[str]:
    """Return Prometheus-visible prefixes for metrics assembled at runtime.

    A doc may cite any name under one of these (``rag_cache_hit_L1`` under
    ``rag_cache_hit_``) because the collector emits a matching series, but no single
    spelling is canonical, so a doc must not present one as the only option.
    """
    collector_path = ROOT / COLLECTOR_MODULE
    if not collector_path.exists():
        return set()
    tree = ast.parse(collector_path.read_text(encoding="utf-8"), filename=str(collector_path))
    _, _, prefixes = _collect_metric_names(tree)
    return prefixes


def emitted_metric_reference_set() -> set[str]:
    """Concrete series plus the prefixes that stand for runtime-assembled names."""
    return emitted_prometheus_metrics() | emitted_prometheus_prefixes()


def _rag_tokens(text: str) -> list[tuple[int, str]]:
    """Return ``(line_index, token)`` for every ``rag_*`` token in the text.

    A bare family prefix is not a series. ``grep rag_http`` names several series at
    once, so a token that is a strict prefix of a real emitted series or of a
    runtime-assembled prefix is treated as a family reference rather than as a claim
    about one missing series.

    The explicit glob form ``rag_http_*`` is the same claim written out, and it needs
    its own rule: the token regex absorbs the trailing underscore, so the prefix test
    below would compare ``rag_http__`` against the inventory and match nothing, and
    the doc would be reported as citing a series that does not exist. It is a family
    reference precisely because it is written as one.
    """
    available = emitted_metric_reference_set()
    found: list[tuple[int, str]] = []
    for index, line in enumerate(text.splitlines()):
        for match in re.finditer(r"\brag_[A-Za-z0-9_]+", line):
            token = match.group(0)
            trailing = line[match.end() : match.end() + 1]
            if trailing == "_":
                continue
            if trailing == "*" and token.endswith("_"):
                if any(series.startswith(token) for series in available):
                    continue
            if any(series.startswith(f"{token}_") for series in available):
                continue
            found.append((index, token))
    return found


def _line_blocks(lines: list[str]) -> dict[int, str]:
    """Map each line index to the block of prose that contains it.

    Justification for a derived metric name may legitimately wrap across the lines
    of one paragraph, so the derivation marker is matched against a whole block
    rather than a single physical line. A heading is merged into the block it
    introduces, because a claim made in a heading is elaborated by its body — a
    heading that names a phantom series is corrected by the paragraph beneath it.
    """
    blocks: dict[int, str] = {}
    start = 0
    boundaries: list[int] = []
    for index in range(len(lines) + 1):
        at_end = index == len(lines)
        if at_end or not lines[index].strip():
            if index > start:
                boundaries.append(index)
            start = index + 1

    for position, end in enumerate(boundaries):
        begin = 0 if position == 0 else boundaries[position - 1] + 1
        body = " ".join(line.strip() for line in lines[begin:end] if line.strip())
        heading_only = all(
            not line.strip() or line.lstrip().startswith("#") or line.lstrip().startswith("```")
            for line in lines[begin:end]
        )
        if heading_only and position + 1 < len(boundaries):
            next_end = boundaries[position + 1]
            body = " ".join(line.strip() for line in lines[begin:next_end] if line.strip())
            end = next_end
        for line_index in range(begin, end):
            blocks[line_index] = body
    return blocks


def operational_metric_reference_errors(name: str, text: str) -> list[str]:
    """Return `rag_*` references that name a series the collector never emits."""
    available = emitted_prometheus_metrics()
    if not available:
        return []
    errors: list[str] = []
    lines = text.splitlines()
    blocks = _line_blocks(lines)
    for index, token in _rag_tokens(text):
        if token in available or token in NON_METRIC_RAG_NAMES:
            continue
        # A derived name must always be justified, even when it happens to sit under
        # a runtime-assembled family prefix: `rag_cache_hit_rate` starts with
        # `rag_cache_hit_`, but no such series exists under that family.
        if token in DERIVED_METRIC_NAMES:
            if _DERIVATION_MARKER_RE.search(blocks.get(index, "")):
                continue
            errors.append(
                f"{name}:{index + 1}: {token!r} is a derived ratio, not a series emitted by "
                f"{COLLECTOR_MODULE}; state the PromQL ratio or point at /api/stats"
            )
            continue
        if any(token.startswith(prefix) for prefix in emitted_prometheus_prefixes()):
            continue
        errors.append(f"{name}:{index + 1}: {token!r} is not a series emitted by {COLLECTOR_MODULE}")
    return errors


def check_operational_metric_references(errors: list[str]) -> None:
    """Operational docs may only cite emitted series or explicitly derived ratios."""
    for name in OPERATIONAL_METRIC_DOCS:
        path = ROOT / name
        if not path.exists():
            continue
        errors.extend(operational_metric_reference_errors(name, path.read_text(encoding="utf-8")))


# ── 3. audit tracker lineage ────────────────────────────────────────────────
#
# The tracker map is a human-facing snapshot, so it must keep completed
# implementation work and still-open external validation distinguishable without
# being pinned to a GitHub query at CI time.
#
# Read this before trusting a number below. Everything in this section is a
# *recorded snapshot of GitHub state*, re-verified by a human against the GitHub
# API and written down in ``docs/repository-truth-audit.md``. It is governance
# audit, not an observation: this guard runs offline, reads only the working
# tree, and has no way to know whether an issue is open on GitHub right now. It
# can therefore prove only that the document and this file agree with each other,
# never that either matches live GitHub. Re-query GitHub before relying on a row;
# the deterministic check below exists to make an *unrecorded* transition fail
# loudly, not to detect one automatically.

#: Areas whose implementation is delivered. Each row must carry an explicit
#: classification so "implemented" can never be read as "result achieved".
DELIVERED_AUDIT_AREAS = (
    "Performance evidence",
    "Structured audit trail",
    "SLO + incident runbook",
    "Prometheus alerting",
    "Grafana dashboard",
    "OTLP export",
)

#: Where the canonical classification vocabulary is defined. Named so the guard can
#: point an author at the one place a new level must be declared, rather than
#: holding a second, diverging copy of the vocabulary here.
_CLASSIFICATION_SOURCE = "docs/interview-evidence-map.md → Classification vocabulary"

#: Long-lived trackers for evidence that only an external environment can produce.
#: None of this repository's own changes can close them, so the audit must keep
#: recording them as open. The numbers are stable by construction: a closed tracker
#: is deleted from this list in the same commit that closes it.
OPEN_EXTERNAL_VALIDATION_TRACKERS = (8, 12, 18, 32, 54)

#: Reconciliation lineage, anchored on the tracking issue and never on the PR
#: number. A PR number is narration that ages out: the next PR exists long
#: before the reconciliation it carries is finished, so "PR #N is the latest /
#: current one" is not a repository-truth property and must never become an
#: invariant. The issue is stable, so the audit is checked against these.
#:
#: Both move in the same commit that closes the reconciliation issue and updates
#: ``docs/repository-truth-audit.md``, exactly like
#: ``OPEN_EXTERNAL_VALIDATION_TRACKERS`` above. A new reconciliation issue
#: supersedes the previous one; it does not extend it.
COMPLETED_RECONCILIATION_ISSUES = (16, 20, 22, 24, 47)

#: The single reconciliation issue the audit describes as the current open scope,
#: or ``None`` when no reconciliation issue is open.
#:
#: ``None`` is a real, legitimate state and not a gap to be papered over. A
#: repository between reconciliations has no open scope, and forcing one of the
#: closed issues above back into this slot to satisfy an "exactly one current
#: issue" invariant would reintroduce the exact drift the invariant exists to
#: catch: a completed reconciliation presented as the work happening now. So the
#: model allows the empty case, the audit must then say so explicitly, and
#: :func:`reconciliation_model_errors` rejects pointing this at an issue that is
#: also listed as completed.
CURRENT_RECONCILIATION_ISSUE: int | None = None

_BULLET_SPLIT_RE = re.compile(r"(?m)^(?=\s*[-*]\s)")

#: A reconciliation bullet records completion with one of these. ``closed`` is
#: accepted because the audit already spells #16's state as ``closed
#: (`completed`)``.
_COMPLETED_MARKER_RE = re.compile(r"\b(closed|completed|merged|resolved)\b", re.IGNORECASE)

#: Present-tense "this is the reconciliation happening now" phrasing. Deliberately
#: about *scope*, never about a PR number. Up to three intervening words are allowed
#: so the ordinary shapes are covered too: a bare ``the current one`` is not the only
#: way to drift, and "the current open scope" slipped through a stricter matcher that
#: demanded the noun immediately after "current".
_CURRENT_SCOPE_RE = re.compile(
    r"\bthe\s+current\s+(?:\w+[\s-]+){0,3}?(?:one|scope|reconciliation|truth|issue|pass|step|work)\b",
    re.IGNORECASE,
)

#: The audit's explicit declaration that there is *no* open reconciliation issue.
#: This is what makes the empty case reviewable rather than silent: a reader has to
#: find the sentence, and the guard refuses an audit that simply omits the topic.
#: It is deliberately worded so a row cannot satisfy it by accident, and the
#: declaration must name no issue number — otherwise the per-issue lookups below
#: would bind to this sentence instead of to the row they mean to check.
#:
#: The free-standing alternative is clause-final on purpose. Without that anchor,
#: ordinary prose such as "no new reconciliation issue has been opened" — which
#: *describes* the absence inside a sentence rather than declaring it — would
#: satisfy the guard and let the real declaration be deleted undetected.
_NO_CURRENT_RECONCILIATION_RE = re.compile(
    r"current\s+reconciliation\s+scope\s*(?:\:|：)?\s*[*_`]*\s*"
    r"(?:none|no\b|none\s+open|none\s+active|not\s+open)|"
    r"no\s+(?:open|new|current)\s+reconciliation\s+(?:issue|scope)\s*"
    r"(?:[.;。；!?！？]|[*_`]*\s*$)|"
    r"当前(?:没有|无)(?:进行中|开放)(?:的)?(?:对账|核对|治理)?(?:议题|范围)",
    re.IGNORECASE,
)


def reconciliation_model_errors() -> list[str]:
    """Return self-consistency errors in the recorded reconciliation model itself.

    These are checks on this module's own constants, independent of any document,
    so a bad model fails even when no audit file is present to contradict it.
    """
    errors: list[str] = []
    if CURRENT_RECONCILIATION_ISSUE is None:
        return errors
    if CURRENT_RECONCILIATION_ISSUE in COMPLETED_RECONCILIATION_ISSUES:
        errors.append(
            f"CURRENT_RECONCILIATION_ISSUE = {CURRENT_RECONCILIATION_ISSUE} is also listed in "
            "COMPLETED_RECONCILIATION_ISSUES; an issue cannot be both completed and the current open "
            "scope. Use CURRENT_RECONCILIATION_ISSUE = None to record that no reconciliation issue is open"
        )
    if CURRENT_RECONCILIATION_ISSUE in OPEN_EXTERNAL_VALIDATION_TRACKERS:
        errors.append(
            f"CURRENT_RECONCILIATION_ISSUE = {CURRENT_RECONCILIATION_ISSUE} is also listed in "
            "OPEN_EXTERNAL_VALIDATION_TRACKERS; an external validation tracker is not a reconciliation scope"
        )
    overlap = sorted(set(COMPLETED_RECONCILIATION_ISSUES) & set(OPEN_EXTERNAL_VALIDATION_TRACKERS))
    if overlap:
        errors.append(
            f"issues {overlap} are recorded as both completed reconciliations and open external validation "
            "trackers; the two lineages are independent and an issue belongs to exactly one of them"
        )
    return errors


def _issue_reference(number: int) -> re.Pattern[str]:
    """Match a Markdown/short reference to an issue or PR number."""
    return re.compile(r"#[\[({]?" + str(number) + r"\b")


def _tracker_section(audit_text: str) -> str | None:
    match = re.search(r"(?ms)^##\s+External validation tracker map\s*$(.*?)(?=^##\s|\Z)", audit_text)
    return match.group(1) if match else None


def _tracker_bullets(tracker: str) -> list[str]:
    """Split the tracker map into bullets, including hard-wrapped continuations.

    A bullet is the contiguous run of non-blank lines that starts at a list
    marker. Trailing prose paragraphs inside the section are *not* part of the
    last bullet: without the blank-line cut-off a closing note such as
    "Closing #N likewise records ..." is glued onto whichever bullet happens to
    precede it, so a guard that looks up #N finds the note instead of the row it
    meant to check -- and a removed row then satisfies its own guard.
    """
    starts = [match.start() for match in _BULLET_SPLIT_RE.finditer(tracker)]
    if not starts:
        return [tracker]
    bounds = starts + [len(tracker)]
    bullets = [tracker[bounds[index] : bounds[index + 1]] for index in range(len(starts))]
    return [re.split(r"(?m)^\s*$", bullet, maxsplit=1)[0] for bullet in bullets]


def audit_tracker_errors(audit_text: str) -> list[str]:
    """Require the audit to separate delivered scope from open external validation."""
    errors: list[str] = reconciliation_model_errors()

    tracker = _tracker_section(audit_text)
    if tracker is None:
        errors.append("repository truth audit: missing the 'External validation tracker map' section")
        return errors

    bullets = _tracker_bullets(tracker)
    for number in OPEN_EXTERNAL_VALIDATION_TRACKERS:
        reference = _issue_reference(number)
        line = next((bullet for bullet in bullets if reference.search(bullet)), None)
        if line is None:
            errors.append(
                f"repository truth audit: external validation tracker #{number} is no longer recorded "
                "in the tracker map; if it was closed, remove it from "
                "OPEN_EXTERNAL_VALIDATION_TRACKERS in scripts/check_repo_consistency.py"
            )
            continue
        if not re.search(r"\bopen\b", line, re.IGNORECASE):
            errors.append(
                f"repository truth audit: tracker #{number} must stay recorded as open; "
                "no change in this repository produces that external evidence"
            )

    for number in COMPLETED_RECONCILIATION_ISSUES:
        bullet = next((item for item in bullets if _issue_reference(number).search(item)), None)
        if bullet is None:
            errors.append(
                f"repository truth audit: completed reconciliation issue #{number} is no longer recorded "
                "in the tracker map; if its reconciliation reopened, record it as the current scope and "
                "remove it from COMPLETED_RECONCILIATION_ISSUES in scripts/check_repo_consistency.py"
            )
            continue
        if not _COMPLETED_MARKER_RE.search(bullet):
            errors.append(f"repository truth audit: reconciliation issue #{number} must stay recorded as completed")
        if _CURRENT_SCOPE_RE.search(bullet):
            errors.append(
                f"repository truth audit: reconciliation issue #{number} is recorded as completed and must "
                "not also be described as the current reconciliation scope; that is the drift this audit "
                "exists to prevent"
            )

    current = None
    if CURRENT_RECONCILIATION_ISSUE is not None:
        current = next(
            (item for item in bullets if _issue_reference(CURRENT_RECONCILIATION_ISSUE).search(item)),
            None,
        )
    none_bullet = next((item for item in bullets if _NO_CURRENT_RECONCILIATION_RE.search(item)), None)

    if CURRENT_RECONCILIATION_ISSUE is None:
        # No open reconciliation issue is a legal state, but it has to be stated.
        # Silence would leave a reader unable to tell "no scope" from "scope not
        # recorded", which is the ambiguity this whole section exists to remove.
        if none_bullet is None:
            errors.append(
                "repository truth audit: CURRENT_RECONCILIATION_ISSUE is None, so the tracker map must state "
                "explicitly that there is no current reconciliation scope; set "
                "CURRENT_RECONCILIATION_ISSUE in scripts/check_repo_consistency.py if one is actually open"
            )
        # ...and it must be the whole story. A single surviving 'the current one'
        # row beside a 'none' declaration is the same drift as the reverse.
        for bullet in bullets:
            if bullet is none_bullet:
                continue
            if _CURRENT_SCOPE_RE.search(bullet):
                errors.append(
                    "repository truth audit: the tracker map declares no current reconciliation scope, but "
                    f"another row still claims to be it: {bullet.strip()!r}"
                )
    elif current is None:
        errors.append(
            f"repository truth audit: current reconciliation issue #{CURRENT_RECONCILIATION_ISSUE} is not "
            "recorded in the tracker map; record it as the open scope, or move it to "
            "COMPLETED_RECONCILIATION_ISSUES in the same commit that closes it"
        )
    elif _COMPLETED_MARKER_RE.search(current):
        errors.append(
            f"repository truth audit: reconciliation issue #{CURRENT_RECONCILIATION_ISSUE} is recorded as "
            "completed but is still the current scope; move it to COMPLETED_RECONCILIATION_ISSUES in the "
            "same commit that closes it"
        )

    # Checked outside the branches above: a model pointing at an issue number and an
    # audit declaring 'none' contradict each other whichever one is wrong, so the
    # contradiction must be reported even when the pointed-at row is itself missing.
    if CURRENT_RECONCILIATION_ISSUE is not None and none_bullet is not None:
        errors.append(
            f"repository truth audit: the tracker map declares no current reconciliation scope, but "
            f"#{CURRENT_RECONCILIATION_ISSUE} is recorded as the current scope; record exactly one of the two"
        )

    for area in DELIVERED_AUDIT_AREAS:
        row = next(
            (line for line in audit_text.splitlines() if re.match(rf"^\|\s*{re.escape(area)}\s*\|", line)),
            None,
        )
        if row is None:
            errors.append(f"repository truth audit: no row for delivered area {area!r}")
            continue
        if not _CLASSIFICATION_TOKENS:
            errors.append(
                f"repository truth audit: cannot classify delivered area {area!r}: the canonical vocabulary "
                f"in {_CLASSIFICATION_SOURCE} is missing or unparsable"
            )
            continue
        if not any(token in row for token in _CLASSIFICATION_TOKENS):
            errors.append(
                f"repository truth audit: delivered area {area!r} carries no explicit classification; "
                f"an unclassified delivered capability reads as a completed result. Use one of "
                f"{sorted(_CLASSIFICATION_TOKENS)}"
            )
    return errors


def check_audit_tracker_lineage(errors: list[str]) -> None:
    """The audit must keep completed implementation and open validation separable."""
    path = ROOT / "docs/repository-truth-audit.md"
    if not path.exists():
        return
    errors.extend(audit_tracker_errors(path.read_text(encoding="utf-8")))


# ── 4. Qdrant evidence reconciliation ───────────────────────────────────────
#
# Qdrant carries two independent evidence states, and the repository must be able
# to say both without contradiction:
#
#   * current reproducible coverage is the in-process `QdrantClient`, and
#   * the PR #6/#7 development record contains a real local Qdrant
#     service/container execution, which is lineage rather than a current result.
#
# Two failure modes follow, and the repository must be able to hold neither:
#
#   * "Qdrant has only ever run in memory" — erases a recorded execution; and
#   * "Qdrant is validated against a real service" — asserts current evidence
#     that no committed artifact supports.
#
# The second is additionally derived from disk rather than hardcoded: while no
# real-service artifact is committed, any current-verification claim is an
# overclaim no matter how it is phrased. The two claims are also checked against
# each other, so the repository cannot deny one while asserting the other.

#: Where a current real-service Qdrant run would have to leave its evidence.
#: Derived from the working tree, like the benchmark and OTLP evidence probes, so
#: the required wording follows what is actually committed instead of a fixed
#: expectation.
QDRANT_RUNTIME_ARTIFACT_GLOB = "artifacts/qdrant/*/metadata.json"

#: The documents that classify Qdrant evidence. Both must keep recording the
#: historical execution, so deleting the lineage cannot pass silently.
QDRANT_EVIDENCE_LINEAGE_DOCS = (
    "docs/interview-evidence-map.md",
    "docs/repository-truth-audit.md",
)

#: "Only in-memory ever happened." Each pattern needs an absolute quantifier over
#: time, because the honest statement about current coverage — "in-memory
#: `QdrantClient`; no checked-in test targets a real service" — carries no such
#: quantifier and must stay allowed.
_QDRANT_ONLY_EVER_PATTERNS = (
    r"(?:only|just)[^\n]{0,40}\bever\b",
    r"\bever\b[^\n]{0,24}(?:only|just)\b",
    r"Qdrant[^\n]{0,48}(?:has|have|had|was|were|is|are)\s+never\b",
    r"(?:never|not\s+ever)[^\n]{0,48}(?:real|live|container|daemon)\s+(?:Qdrant\s+)?(?:service|server|instance)",
    r"(?:从未|从来没有|从来没)[^\n]{0,24}Qdrant",
    r"Qdrant[^\n]{0,24}(?:从未|从来没)",
)

#: "Real service is currently verified." An affirmative result claim about a real
#: Qdrant service, in the present tense.
_QDRANT_CURRENTLY_VERIFIED_PATTERNS = (
    r"(?:real|live|local)\s+Qdrant[^\n]{0,40}(?:service|server|container|instance|daemon)"
    r"[^\n]{0,40}(?:validated|verified|confirmed|passed)",
    r"Qdrant[^\n]{0,40}(?:currently|now|today)[^\n]{0,24}(?:validated|verified|confirmed|reproducible)",
    r"Qdrant[^\n]{0,32}LOCAL_REAL_VALIDATION",
    r"(?:真实|本地)[^\n]{0,16}Qdrant[^\n]{0,24}(?:服务|容器)[^\n]{0,16}(?:已验证|验证通过)",
    r"Qdrant[^\n]{0,24}(?:当前)?(?:已验证|验证通过)",
)

#: A statement scoped to current coverage cannot erase history, so it is exempt
#: from the only-in-memory check: "the suite never connects to a real Qdrant
#: service" describes today's coverage, not the absence of a past run. The words
#: ``in-memory``/``in process`` are deliberately *not* markers here — the wrong
#: claim quotes them, so exempting on them would exempt the claim itself.
_QDRANT_CURRENT_SCOPE_RE = re.compile(
    r"current(?:ly)?\b|today\b|checked[-\s]in|committed\b|"
    r"deterministic|regression|\bsuite\b|\btests?\b|artifact|reproduc|pending",
    re.IGNORECASE,
)

#: The claim is historical lineage, not a current claim.
_QDRANT_LINEAGE_SCOPE_RE = re.compile(
    r"historical|lineage|PR\s*#6|#6/#7|PR\s*#7|development\s+(?:round|record|phase)|"
    r"at\s+that\s+time|历史|沿革|开发过程",
    re.IGNORECASE,
)

#: The claim says the result is absent, which is the honest current state.
_QDRANT_NO_RESULT_SCOPE_RE = re.compile(
    r"no\s+(?:checked[-\s]in\s+|committed\s+|current\s+|reproducible\s+|such\s+)?"
    r"(?:artifact|test|evidence|result)|not\s+(?:a\s+)?(?:current\s+)?"
    r"(?:reproducible|committed|verified|validated)|nothing\s+committed|"
    r"PENDING|pending|NOT\s+RUN|out\s+of\s+scope|would\s+require|"
    r"requires\s+a\s+new|upgrad\w+\s+[^.\n]{0,40}require|"
    r"没有.{0,10}产物|无产物|尚未|未执行|不在本轮|需要新的|需要.?新",
    re.IGNORECASE,
)

#: "Do not say X", written as a prohibition. A document is required to write the
#: denial, so the frame is a signal to skip rather than a signal to fail. Matches
#: gerunds too, because these documents quote the wrong claim inside prose. A bare
#: ``Never`` is deliberately not a frame: "never validated against a real Qdrant
#: service" is the erasure this guard exists to catch, not a prohibition.
_QDRANT_PROHIBITION_FRAME_RE = re.compile(
    r"\bClaim(?:ing|s)?\b|\bSays?\b|\bDo" + _EMPHASIS_GAP + r"not\b|\bDon't\b|"
    r"\bMust" + _EMPHASIS_GAP + r"not\b|"
    r"\bwrong\s+claim\b|\bboth\s+directions?\b|\bcollapse[sd]?\b|\berras\w+\b|"
    r"不能说|不得|不要(?:说|声称)|不应(?:说|声称)",
    re.IGNORECASE,
)

_QDRANT_SENTENCE_SPLIT_RE = re.compile(r"[；;。！!？?\n]+|\.(?=\s|$)")


def qdrant_runtime_artifact_exists(root: Path | None = None) -> bool:
    """True when a committed real-service Qdrant run artifact is on disk."""
    base = root or ROOT
    return any(path.is_file() for path in base.glob(QDRANT_RUNTIME_ARTIFACT_GLOB))


def _qdrant_claim_patterns(
    text: str,
    patterns: tuple[str, ...],
    extra_exemption: re.Pattern[str] | None = None,
) -> list[str]:
    """Return the matched claim fragments for one Qdrant claim family.

    Scoped to sentences that actually name Qdrant, so a generic "never" or
    "verified" elsewhere in a document cannot trip the guard, and clause markers
    that scope the sentence to lineage or to an absent result suppress the claim.
    """
    claims: list[str] = []
    for sentence in _QDRANT_SENTENCE_SPLIT_RE.split(text):
        if "qdrant" not in sentence.casefold():
            continue
        if _QDRANT_PROHIBITION_FRAME_RE.search(sentence):
            continue
        if _QDRANT_LINEAGE_SCOPE_RE.search(sentence) or _QDRANT_NO_RESULT_SCOPE_RE.search(sentence):
            continue
        if extra_exemption is not None and extra_exemption.search(sentence):
            continue
        for pattern in patterns:
            match = re.search(pattern, sentence, flags=re.IGNORECASE)
            if match:
                claims.append(match.group(0).strip())
                break
    return claims


def qdrant_only_ever_claims(text: str) -> list[str]:
    """Return claims that deny any real Qdrant service ever being exercised."""
    return _qdrant_claim_patterns(text, _QDRANT_ONLY_EVER_PATTERNS, _QDRANT_CURRENT_SCOPE_RE)


def qdrant_currently_verified_claims(text: str) -> list[str]:
    """Return claims that a real Qdrant service is currently validated."""
    return _qdrant_claim_patterns(text, _QDRANT_CURRENTLY_VERIFIED_PATTERNS)


def qdrant_evidence_claim_errors(name: str, text: str, artifact_exists: bool) -> list[str]:
    """Return the Qdrant evidence-claim errors for one document."""
    errors: list[str] = []
    denials = qdrant_only_ever_claims(text)
    if denials:
        errors.append(
            f"{name}: denies that a real Qdrant service was ever exercised, which the PR #6/#7 "
            f"development record contradicts: {denials}"
        )
    verified = qdrant_currently_verified_claims(text)
    if verified and not artifact_exists:
        errors.append(
            f"{name}: claims a real Qdrant service is currently validated with no artifact on disk "
            f"({QDRANT_RUNTIME_ARTIFACT_GLOB}): {verified}"
        )
    return errors


def qdrant_doc_claims(docs: Iterable[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """Return ``(denials, verifications)`` across named documents.

    Denials and verifications are collected together because the contradiction
    between them is a repository-level property: two documents can disagree
    without either one disagreeing with itself.
    """
    denials: list[str] = []
    verified: list[str] = []
    for name, text in docs:
        denials.extend(f"{name}: {claim}" for claim in qdrant_only_ever_claims(text))
        verified.extend(f"{name}: {claim}" for claim in qdrant_currently_verified_claims(text))
    return denials, verified


def qdrant_contradiction_errors(denials: list[str], verified: list[str]) -> list[str]:
    """Reject holding both contradictory Qdrant claims at the same time."""
    if not (denials and verified):
        return []
    return [
        f"Qdrant evidence is self-contradictory: the repository both denies any real-service "
        f"execution ({denials}) and claims one is verified ({verified})"
    ]


def qdrant_lineage_errors(name: str, text: str, artifact_exists: bool) -> list[str]:
    """Require the classifying documents to keep recording both Qdrant states.

    The historical-execution half is permanent: that run happened, so no
    reconciliation may quietly stop recording it. The "no artifact yet" half is
    derived from disk, because it stops being true the moment a real-service run
    commits one.
    """
    errors: list[str] = []
    records_run = re.search(r"#6/#7|PR\s*#6|PR\s*#7", text) and re.search(
        r"real\s+(?:local\s+)?Qdrant\s+(?:service|container)|"
        r"Qdrant[^\n]{0,24}(?:service|container)[^\n]{0,24}(?:run|execution)|"
        r"真实[^\n]{0,12}Qdrant[^\n]{0,12}(?:服务|容器)",
        text,
        re.IGNORECASE,
    )
    if not records_run:
        errors.append(
            f"{name}: must record that the PR #6/#7 development round ran the writers against a real "
            "local Qdrant service/container, alongside Elasticsearch"
        )
    if artifact_exists:
        return errors
    if not re.search(
        r"no\s+(?:checked[-\s]in\s+|committed\s+|current\s+|reproducible\s+)?artifact|"
        r"not\s+a\s+current\s+reproducible|"
        r"requires?\s+a\s+new|new\s+real[-\s]service\s+run|"
        r"没有.{0,10}产物|无产物|需要新的",
        text,
        re.IGNORECASE,
    ):
        errors.append(
            f"{name}: must state that no artifact of the historical Qdrant run is committed and that "
            "upgrading the current evidence requires a new real-service run"
        )
    return errors


def check_qdrant_evidence_reconciliation(errors: list[str], root: Path | None = None) -> None:
    """Qdrant must record both evidence states and claim neither away."""
    base = root or ROOT
    artifact_exists = qdrant_runtime_artifact_exists(base)

    docs: list[tuple[str, str]] = []
    for doc in CANONICAL_DOCS:
        if not doc.exists():
            continue
        name = str(doc.relative_to(ROOT)) if doc.is_relative_to(ROOT) else str(doc)
        text = doc.read_text(encoding="utf-8")
        docs.append((name, text))
        errors.extend(qdrant_evidence_claim_errors(name, text, artifact_exists))

    denials, verified = qdrant_doc_claims(docs)
    errors.extend(qdrant_contradiction_errors(denials, verified))

    for name in QDRANT_EVIDENCE_LINEAGE_DOCS:
        path = base / name
        if not path.exists():
            continue
        errors.extend(qdrant_lineage_errors(name, path.read_text(encoding="utf-8"), artifact_exists))

    if artifact_exists:
        fail(
            errors,
            f"a Qdrant real-service artifact now exists under {QDRANT_RUNTIME_ARTIFACT_GLOB}; the "
            "current-evidence wording is now stale and must be re-derived from that artifact",
        )


def check_canonical_runtime_is_not_observability_gated(errors: list[str]) -> None:
    """No forbidden platform may be required by the canonical deployment."""
    for name in ("docker-compose.yml", "Dockerfile"):
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").lower()
        for forbidden in ("kubernetes", "helm", "kafka", "langgraph"):
            if forbidden in text:
                fail(errors, f"{name} must not introduce {forbidden} into the canonical deployment")


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


# --------------------------------------------------------------------------
# Frontend evidence: CI build != end-to-end runtime != production deployment
# --------------------------------------------------------------------------
#
# These are three separate claims and the repository has evidence for exactly
# one of them:
#
#   (A) the React client and its API metadata contract  -> REPO_VERIFIED
#   (B) `npm ci` + `npm run build` in GitHub Actions    -> REPO_VERIFIED
#   (C) frontend + real backend end-to-end runtime      -> PENDING
#   (D) production deployment                          -> deployment-specific, PENDING
#
# A green `frontend-build` proves the bundle compiles. It observes no browser,
# no real backend and no deployed environment, so it can never promote (C) or
# (D). `.github/workflows/ci.yml` is the source of truth for (B) and is read
# from disk rather than hardcoded, so removing the job turns the (B) sentence
# into drift instead of leaving a stale claim behind an unenforced guard.
FRONTEND_CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

#: A committed browser run of the frontend against the real backend. Follows the
#: benchmark/performance idiom: generated runs are git-ignored, so the absence of
#: a match is what keeps (C) `PENDING`.
FRONTEND_E2E_ARTIFACT_GLOB = "artifacts/frontend-e2e/*/report.json"

#: Docs that state what CI proves about the frontend. All are canonical, so all
#: are in scope for the denial and escalation scanners.
FRONTEND_EVIDENCE_DOCS = [
    "README.md",
    "docs/README.md",
    "docs/interview-evidence-map.md",
    "docs/interview-architecture-baseline.md",
    "docs/repository-truth-audit.md",
    "docs/main-branch-governance.md",
    "docs/pre-launch-checklist.md",
    "docs/deployment-guide.md",
]

# Denials of (B). These are only drift while the workflow builds the frontend.
_FRONTEND_BUILD_DENIAL_PATTERNS = [
    r"CI\s+(?:does\s+not|doesn't|do\s+not|never)\s+(?:build|compile)\w*\s*(?:the\s+)?frontend",
    r"CI\s*(?:不|未|没有|不会)\s*(?:构建|编译|打包)\s*前端",
    r"frontend\s+build\s+is\s+separate",
    r"(?:没有|无)\s*前端构建\s*(?:校验|job|任务|门禁)",
]

# Overclaims of (C) or (D) from a CI build. Deliberately narrow: each pattern has
# to name the frontend *and* the escalated outcome, so ordinary prose about the
# pending states cannot match. Negation-aware via _PROHIBITION_FRAME_RE and
# _NO_EVIDENCE_NEGATION_RE, so "never say the frontend is validated end-to-end"
# and "end-to-end runtime integration is PENDING" both stay allowed.
# Runtime claims, gated on a committed artifact: a browser run against the real
# backend can establish end-to-end integration (C), so these relax once such an
# artifact exists.
_FRONTEND_RUNTIME_ESCALATION_PATTERNS = [
    r"(?:frontend|前端)[^\n]{0,40}?(?:build|构建|编译)[^\n]{0,40}?(?:end[-\s]to[-\s]end|e2e|端到端)\s*"
    r"(?:validat|verif|tested|验证|测试|通过)",
    r"(?:end[-\s]to[-\s]end|e2e|端到端)[^\n]{0,30}?(?:validat|verif|验证)[^\n]{0,40}?(?:frontend|前端)",
    r"(?:frontend|前端)[^\n]{0,30}?(?:browser|浏览器)[^\n]{0,30}?"
    r"(?:real\s+backend|真实\s*后端|monolith)",
    r"(?:browser|浏览器)[^\n]{0,30}?(?:frontend|前端)[^\n]{0,30}?"
    r"(?:real\s+backend|真实\s*后端|monolith)",
    # Chinese and English both allow "real backend" to sit on either side of the
    # frontend subject ("browser verified against the real backend for the
    # frontend" and "the real backend validated the frontend" are the same claim).
    r"(?:browser|浏览器)[^\n]{0,40}?(?:real\s+backend|真实\s*后端|monolith)"
    r"[^\n]{0,20}?(?:validat|verif|验证)[^\n]{0,20}?(?:frontend|前端)",
    r"(?:real\s+backend|真实\s*后端|monolith)[^\n]{0,40}?(?:validat|verif|验证)"
    r"[^\n]{0,20}?(?:frontend|前端)",
    r"(?:CI|ci)\s*(?:frontend\s*|前端\s*)?(?:构建|build)[^\n]{0,30}?(?:等于|即为|proves?|means)\s*[^\n]{0,20}?"
    r"(?:端到端\s*验证|end[-\s]to[-\s]end\s+(?:validat|verif))",
]

# Deployment claims, never gated on any artifact. No run recorded in a
# repository can establish that a system is deployed to production, so these
# checks stay active unconditionally. Gating them on the E2E artifact would let
# a single browser-run report switch off the production-deployment overclaim
# check, which is precisely the framework/result conflation this guard exists to
# prevent: (C) and (D) are separate claims and an artifact for (C) is not
# evidence for (D).
_FRONTEND_DEPLOYMENT_ESCALATION_PATTERNS = [
    r"(?:frontend|前端)[^\n]{0,20}?(?:is\s+|已)?(?:deployed|deployed\s+to|部署)[^\n]{0,20}?"
    r"(?:production|生产)",
    r"(?:frontend|前端)[^\n]{0,20}?(?:production|生产)[^\n]{0,10}?(?:deployed|部署)",
    r"(?:CI|ci)\s*(?:frontend\s*|前端\s*)?(?:构建|build)[^\n]{0,30}?(?:等于|即为|proves?|means)\s*[^\n]{0,20}?"
    r"(?:production\s+validated|生产\s*验证)",
]

# The one committed browser-driven frontend run is the demo capture, and it is
# mock-backed by construction. Presenting it as real-backend E2E conflates a
# demo fixture with runtime evidence.
_FRONTEND_MOCK_AS_E2E_PATTERNS = [
    r"(?:capture_demo|demo\s+capture|演示\s*(?:截图|录制))[^\n]{0,60}?"
    r"(?:end[-\s]to[-\s]end|e2e|端到端)",
    r"(?:end[-\s]to[-\s]end|e2e|端到端)[^\n]{0,60}?(?:capture_demo|demo\s+capture|演示\s*(?:截图|录制))",
]


# Order-independent companion to the patterns above. "Browser verified against
# the real backend for the frontend" states the same escalation as "the frontend
# build is end-to-end validated", but no single regex covers every word order,
# so the rule is stated as a token co-occurrence instead: a line that names a
# browser, the frontend *and* a real backend in one breath is making a runtime
# claim, and must therefore be negated or pending to be allowed.
_FRONTEND_BROWSER_TOKEN_RE = re.compile(r"browser|浏览器|playwright", re.IGNORECASE)
_FRONTEND_SUBJECT_TOKEN_RE = re.compile(r"frontend|前端", re.IGNORECASE)
_FRONTEND_REAL_BACKEND_TOKEN_RE = re.compile(
    r"real\s+backend|真实\s*后端|real\s+monolith|真实\s*单体|real\s+FastAPI|真实\s*FastAPI",
    re.IGNORECASE,
)

#: Negations that make a frontend runtime paragraph a denial rather than a claim.
#: Separate from _NO_EVIDENCE_NEGATION_RE because that one is deliberately tuned
#: for single-line table cells and omits the "no committed artifact" /
#: "never been recorded" phrasings these wrapped bullets actually use.
_FRONTEND_RUNTIME_NEGATION_RE = re.compile(
    r"PENDING|never|not\s+(?:yet\s+)?(?:established|validated|verified|recorded|claimed)|"
    r"no\s+(?:committed\s+)?(?:such\s+)?(?:artifact|evidence|run|result)|no\s+browser\s+run|"
    r"未(?:有|能|执行|验证|产生|记录)|尚未|没有|无可|不(?:会|能|得|是)|不能|不等于|不代表",
    re.IGNORECASE,
)


def _check_frontend_runtime_cooccurrence(name: str, text: str, errors: list[str]) -> None:
    """Flag a frontend browser/runtime claim about a real backend with no artifact.

    Negation is searched across the whole *paragraph* rather than the single
    physical line, because these are wrapped markdown bullets: the denial
    ("No committed artifact shows a browser-rendered frontend/ against the real
    monolith...") routinely sits on a different line from the claim it denies.
    A per-line window would read that bullet as an overclaim.
    """
    for paragraph, offset in _markdown_paragraphs(text):
        if not (
            _FRONTEND_BROWSER_TOKEN_RE.search(paragraph)
            and _FRONTEND_SUBJECT_TOKEN_RE.search(paragraph)
            and _FRONTEND_REAL_BACKEND_TOKEN_RE.search(paragraph)
        ):
            continue
        if _PROHIBITION_FRAME_RE.search(paragraph) or _FRONTEND_RUNTIME_NEGATION_RE.search(paragraph):
            continue
        if HISTORICAL_MARKERS.search(paragraph):
            continue
        fail(
            errors,
            f"{name}:{offset}: asserts a frontend browser runtime against a real backend, but no "
            f"committed artifact matching {FRONTEND_E2E_ARTIFACT_GLOB} exists; end-to-end runtime "
            f"integration is PENDING and must be stated as such",
        )


def _markdown_paragraphs(text: str) -> list[tuple[str, int]]:
    """Yield (paragraph, first 1-based line number) for each blank-line-delimited block."""
    paragraphs: list[tuple[str, int]] = []
    current: list[str] = []
    start = 1
    for number, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            if not current:
                start = number
            current.append(line)
            continue
        if current:
            paragraphs.append(("\n".join(current), start))
            current = []
    if current:
        paragraphs.append(("\n".join(current), start))
    return paragraphs


def ci_builds_frontend(workflow_text: str) -> bool:
    """Whether any CI job installs from the lockfile and builds the frontend."""
    try:
        import yaml

        workflow = yaml.safe_load(workflow_text) or {}
    except Exception:
        # A workflow this script cannot parse is a separate failure; treating it
        # as "no frontend build" would let the denial scanner pass silently.
        return True

    for job in (workflow.get("jobs") or {}).values():
        if not isinstance(job, dict):
            continue
        runs = "\n".join(str(step.get("run", "")) for step in (job.get("steps") or []) if isinstance(step, dict))
        if re.search(r"npm\s+ci\b", runs) and re.search(r"npm\s+run\s+build\b", runs):
            return True
    return False


def frontend_e2e_artifact_exists() -> bool:
    return any(ROOT.glob(FRONTEND_E2E_ARTIFACT_GLOB))


def _scanned_frontend_lines(text: str) -> list[tuple[int, str]]:
    return [(n, line) for n, line in enumerate(text.splitlines(), start=1)]


def _scan_frontend_patterns(
    name: str,
    text: str,
    patterns: list[str],
    message: str,
    errors: list[str],
    *,
    mode: str,
) -> None:
    """Scan `text` for frontend patterns, skipping legitimately negated lines.

    ``mode`` selects what excuses a match, because the two pattern families need
    opposite treatment:

    - ``"denial"``. A denial is itself a negation, so the generic no-evidence
      negation window cannot apply: it would let an unrelated ``PENDING`` later
      in the same table row excuse it, and every audit row ends in a status cell,
      so the guard would never fire. The coarse ``HISTORICAL_MARKERS`` set is
      excluded too — it matches ordinary prose such as "there is no separate
      pytest target" — because a denial is a claim about the *current* CI
      configuration, which this guard reads from disk. Only a "do not claim
      this" frame excuses it.
    - ``"escalation"``. Here all three exemptions apply: the no-evidence window
      is what lets "end-to-end runtime integration is PENDING" and "never say
      the frontend is validated end-to-end" stay allowed, and a line marked
      historical may narrate a former state.
    """
    for number, line in _scanned_frontend_lines(text):
        for pattern in patterns:
            for match in re.finditer(pattern, line, re.IGNORECASE):
                prefix = line[max(0, match.start() - 60) : match.start()]
                if _PROHIBITION_FRAME_RE.search(prefix):
                    continue
                if mode == "escalation":
                    window = line[max(0, match.start() - 50) :]
                    if _NO_EVIDENCE_NEGATION_RE.search(window):
                        continue
                    if HISTORICAL_MARKERS.search(line):
                        continue
                fail(
                    errors,
                    f"{name}:{number}: {message} matched {pattern!r}: {match.group(0)!r}",
                )
                break


def check_frontend_evidence_classification(errors: list[str]) -> None:
    """Keep CI build, end-to-end runtime and deployment as three separate claims."""
    if not FRONTEND_CI_WORKFLOW.exists():
        return
    builds = ci_builds_frontend(FRONTEND_CI_WORKFLOW.read_text(encoding="utf-8"))
    has_e2e_artifact = frontend_e2e_artifact_exists()

    for name in FRONTEND_EVIDENCE_DOCS:
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")

        if builds:
            _scan_frontend_patterns(
                name,
                text,
                _FRONTEND_BUILD_DENIAL_PATTERNS,
                "denies the CI frontend build that .github/workflows/ci.yml performs",
                errors,
                mode="denial",
            )

        # Deployment overclaim is checked unconditionally: no repository artifact
        # can establish a production deployment, so this never relaxes.
        _scan_frontend_patterns(
            name,
            text,
            _FRONTEND_DEPLOYMENT_ESCALATION_PATTERNS,
            "presents the frontend as deployed to production; deployment state lives outside this "
            "repository and no committed artifact can establish it",
            errors,
            mode="escalation",
        )

        if not has_e2e_artifact:
            _scan_frontend_patterns(
                name,
                text,
                _FRONTEND_RUNTIME_ESCALATION_PATTERNS,
                f"presents frontend CI build evidence as end-to-end runtime evidence, but no "
                f"committed artifact matching {FRONTEND_E2E_ARTIFACT_GLOB} exists",
                errors,
                mode="escalation",
            )
            _scan_frontend_patterns(
                name,
                text,
                _FRONTEND_MOCK_AS_E2E_PATTERNS,
                "presents the mock-backed demo capture as real-backend end-to-end evidence",
                errors,
                mode="escalation",
            )
            _check_frontend_runtime_cooccurrence(name, text, errors)

    _check_frontend_audit_row(builds, has_e2e_artifact, errors)


#: A level together with the claim it qualifies, e.g.
#: ``REPO_VERIFIED`` (B CI build gate). The qualifier is what makes a compound
#: status cell meaningful: without reading it, "the row contains REPO_VERIFIED"
#: cannot tell the CI build apart from the client contract, so deleting the
#: build's level would leave the row looking correct.
_FRONTEND_LEVEL_QUALIFIER_RE = re.compile(r"`([A-Z][A-Z0-9_]+)`\s*(?:\(([^)]*)\))?")

#: Qualifier wording that names the CI build claim, in either language.
_FRONTEND_BUILD_QUALIFIER_RE = re.compile(r"\bbuild\b|\bci\b|构建", re.IGNORECASE)

#: Qualifier wording that names the runtime-integration claim.
_FRONTEND_RUNTIME_QUALIFIER_RE = re.compile(
    r"end[-\s]to[-\s]end|\be2e\b|runtime|integration|端到端|运行", re.IGNORECASE
)

#: Qualifier wording that names the deployment claim. Kept separate from the
#: runtime qualifier because deployment is gated differently: an E2E artifact can
#: close (C) but never (D).
_FRONTEND_DEPLOYMENT_QUALIFIER_RE = re.compile(r"deploy|部署", re.IGNORECASE)


def _frontend_level_qualifiers(status: str, level: str) -> list[str]:
    return [
        (qualifier or "").lower() for found, qualifier in _FRONTEND_LEVEL_QUALIFIER_RE.findall(status) if found == level
    ]


def frontend_audit_classification_errors(status: str, *, builds: bool, has_e2e_artifact: bool) -> list[str]:
    """Classify the `Frontend contract` status cell against what CI can prove.

    Split out from the file walk so the rule is testable without a temp tree,
    mirroring `benchmark_classification_errors`.
    """
    problems: list[str] = []

    if builds:
        verified = _frontend_level_qualifiers(status, "REPO_VERIFIED")
        if not any(_FRONTEND_BUILD_QUALIFIER_RE.search(q) for q in verified):
            problems.append(
                "the CI frontend build (npm ci + npm run build) must be classified "
                f"REPO_VERIFIED with a qualifier naming the build; got {status!r}"
            )

    if not has_e2e_artifact:
        pending = _frontend_level_qualifiers(status, "PENDING")
        if not any(_FRONTEND_RUNTIME_QUALIFIER_RE.search(q) for q in pending):
            problems.append(
                "end-to-end runtime integration must stay PENDING, with a qualifier naming it, while no "
                f"artifact matching {FRONTEND_E2E_ARTIFACT_GLOB} is committed; got {status!r}"
            )

    # Deployment is required to stay PENDING unconditionally. It is *not* gated on
    # the E2E artifact: a browser-against-backend run establishes runtime
    # integration, never a production deployment, so letting an artifact close
    # this would silently merge claims (C) and (D).
    pending = _frontend_level_qualifiers(status, "PENDING")
    if not any(_FRONTEND_DEPLOYMENT_QUALIFIER_RE.search(q) for q in pending):
        problems.append(
            f"production deployment must stay PENDING with a qualifier naming it; no committed artifact "
            f"can establish a deployment, so this does not relax with {FRONTEND_E2E_ARTIFACT_GLOB}; "
            f"got {status!r}"
        )
    return problems


def _check_frontend_audit_row(builds: bool, has_e2e_artifact: bool, errors: list[str]) -> None:
    """The audit must classify the frontend states the repository can support."""
    audit = ROOT / "docs" / "repository-truth-audit.md"
    if not audit.exists():
        return

    status = None
    for line in audit.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        columns = [part.strip() for part in line.strip("|").split("|")]
        if columns and columns[0] == "Frontend contract":
            status = columns[6] if len(columns) > 6 else None
            break

    # Presence itself is enforced by REQUIRED_AUDIT_AREAS; there is nothing to
    # classify if the row is absent, and that check reports it legibly.
    if status is None:
        return

    for problem in frontend_audit_classification_errors(status, builds=builds, has_e2e_artifact=has_e2e_artifact):
        fail(errors, f"repository truth audit 'Frontend contract' row: {problem}")


# --------------------------------------------------------------------------
# Removed plan path: docs/superpowers/ is a historical path, not a directory
# --------------------------------------------------------------------------
#
# PR #33 deleted `docs/superpowers/` from the branch. Nothing was relocated: the
# plans survive only in Git history. A canonical document that still names that
# path in the present tense therefore sends a reader to open a directory that
# does not exist, and nothing in the tree contradicts them.
#
# The check is a *positive* requirement rather than a pattern exemption: every
# surviving mention must carry a historical frame. An unrelated "PENDING" or
# "not verified" elsewhere in the paragraph cannot excuse it, which is the
# failure mode a shared negation window would reintroduce.
REMOVED_PLAN_PATH = "docs/superpowers/"

#: Framing that marks a mention as historical. Deliberately narrower than
#: HISTORICAL_MARKERS: that set matches ordinary current-state prose such as
#: "removed" or "no longer", so it would wave through a present-tense sentence.
_REMOVED_PLAN_HISTORICAL_FRAME_RE = re.compile(
    r"former(?:ly)?|previous(?:ly)?|used\s+to\b|no\s+longer|"
    r"\bremoved\b|\bdeleted\b|\bpreserved\b|retrievable|"
    r"git\s+history|"
    r"曾经|原先|原目录|已删除|已移除|不在当前|历史",
    re.IGNORECASE,
)

#: Sentence boundaries, so a frame in one sentence cannot vouch for a path
#: mentioned in another.
_REMOVED_PLAN_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;!?。；])\s+|\n")


def check_removed_plan_path_is_historical(errors: list[str]) -> None:
    """A removed plan path may only be named as history."""
    if (ROOT / REMOVED_PLAN_PATH).exists():
        # The directory is back. Whether it is canonical, archived or untracked
        # is a placement decision the documentation guard already owns, so this
        # rule stands down rather than ruling on it.
        return

    for path in CANONICAL_DOCS:
        if not path.exists():
            continue
        # Sentence scope, not line or paragraph scope: these references sit inside
        # wrapped prose, and a coarser scope lets one sentence's historical frame
        # vouch for a present-tense mention in the next.
        for paragraph, offset in _markdown_paragraphs(path.read_text(encoding="utf-8")):
            for sentence in _REMOVED_PLAN_SENTENCE_SPLIT_RE.split(paragraph):
                if REMOVED_PLAN_PATH not in sentence:
                    continue
                if _REMOVED_PLAN_HISTORICAL_FRAME_RE.search(sentence):
                    continue
                fail(
                    errors,
                    f"{path.relative_to(ROOT)}:{offset}: names {REMOVED_PLAN_PATH!r} without marking "
                    "it as a removed path. The directory is not in the current tree and its plans live "
                    'only in Git history; say so (e.g. "formerly stored under docs/superpowers/, '
                    'preserved in Git history") so no reader expects to open it',
                )


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
    header_index = next((i for i, line in enumerate(audit_lines) if line.startswith("| Area |")), None)
    if header_index is None:
        fail(errors, "repository truth audit must have Area and Status columns")
        return
    header = audit_lines[header_index]
    columns_header = [part.strip().lower() for part in header.strip("|").split("|")]
    area_column = columns_header.index("area") if "area" in columns_header else -1
    status_column = columns_header.index("status") if "status" in columns_header else -1
    if area_column < 0 or status_column < 0:
        fail(errors, "repository truth audit must have Area and Status columns")
        return

    seen_areas: dict[str, str] = {}
    legal = canonical_evidence_levels()
    if not legal:
        fail(
            errors,
            f"repository truth audit cannot be validated: the canonical vocabulary in {INTERVIEW_EVIDENCE_MAP} "
            "is missing or unparsable",
        )
    # Only the contiguous run of `|` lines that starts at the audit header is the
    # audit table. Later tables in the same document are prose, not audit rows, so
    # parsing must stop at the first non-table line after the header.
    for line_number in range(header_index + 1, len(audit_lines)):
        line = audit_lines[line_number]
        if not line.startswith("|"):
            if line.strip():
                break
            continue
        if "---" in line:
            continue
        columns = [part.strip() for part in line.strip("|").split("|")]
        if len(columns) <= max(area_column, status_column):
            fail(errors, f"repository audit line {line_number + 1}: malformed row")
            continue
        status = columns[status_column]
        # The status cell must name canonical levels and nothing else, so a row
        # cannot quietly revert to a retired status word or to prose.
        levels = _CLASSIFICATION_TOKEN_RE.findall(status)
        if not levels:
            fail(
                errors,
                f"repository audit line {line_number + 1}: status {status!r} names no canonical evidence "
                f"level; use one of {sorted(legal) or ['(canonical vocabulary unreadable)']}",
            )
        for level in levels:
            if level not in legal:
                fail(
                    errors,
                    f"repository audit line {line_number + 1}: {level!r} is not a canonical evidence level; "
                    f"use one of {sorted(legal) or ['(canonical vocabulary unreadable)']}",
                )
        seen_areas[columns[area_column]] = status

    missing_areas = REQUIRED_AUDIT_AREAS - set(seen_areas)
    if missing_areas:
        fail(errors, f"repository truth audit is missing required areas: {sorted(missing_areas)}")

    for area in sorted(OFFLINE_CAPABILITY_AREAS & set(seen_areas)):
        if SUPERSEDED_LEVEL in _CLASSIFICATION_TOKEN_RE.findall(seen_areas[area]):
            fail(
                errors,
                f"repository truth audit classifies existing offline capability {area!r} as "
                f"{SUPERSEDED_LEVEL}; it exists in the current tree and is not a superseded artifact",
            )


# ── 12. a section may not restate a hardcoded count of its own list ─────────
#
# README's Interviewer Guide preface said "六个问题" above a Q1..Q7 list. Nothing
# failed: every other guard here checks evidence vocabulary or whether a claim
# outruns its evidence, and none of them cross-check a section's prose against
# the section's own contents. Deleting the number fixed that instance; these two
# invariants make the class unrepresentable rather than merely fixed once.
#
# Both are derived from the text itself, so neither needs a hardcoded count, a
# heading name or a document list. A section that enumerates nothing is skipped,
# which is why applying this to every canonical document costs nothing: prose
# that merely mentions "12 个问题" carries no enumerator and is never inspected.

#: An enumeration item, e.g. ``**Q1 · 这个系统解决什么业务问题？**``. The bold
#: marker has to *open the line* and be followed by whitespace plus the item
#: title, so nothing but a real item line is counted: an inline cross-reference
#: (``See **Q7**``), a bare ``**Q7**`` and a heading like ``## Q12 标准回答`` all
#: leave the section with no enumeration, which is what makes the guard safe to
#: run over every canonical document instead of tripping CI on ordinary prose.
_ENUMERATED_ITEM_RE = re.compile(r"^\*\*Q(\d+)\s+\S", re.MULTILINE)

#: English count words that may precede "questions". The list runs well past
#: "ten" on purpose: the marker syntax accepts any ``Q<n>``, so a list that grows
#: to twelve or twenty-one items must be guarded exactly like a shorter one.
#: Every English count word the guards accept. Kept as one tuple and turned into
#: both the regex alternation and the word->value map below, because maintaining
#: those two lists separately is how "twenty-one" came to parse as 20 and
#: "thirty" came to be matchable but unparseable.
_COUNT_WORD_LIST = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand"
).split()
_COUNT_WORDS = "|".join(_COUNT_WORD_LIST)


def _count_word_values() -> dict[str, int]:
    """``zero``..``nineteen`` count up; the rest are tens, then hundred/thousand.

    Built by position for the first twenty and from an explicit table for the
    rest, because an index is not a value: ``thirty`` is the 22nd word in the
    list but the number 30.
    """
    values = {word: index for index, word in enumerate(_COUNT_WORD_LIST[:20])}
    values.update(
        zip(
            ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"),
            (20, 30, 40, 50, 60, 70, 80, 90),
            strict=True,
        )
    )
    values["hundred"] = 100
    values["thousand"] = 1000
    return values


_EN_COUNT_WORDS = _count_word_values()

#: The only values that may head an additive English compound. "twenty-one" is
#: 21; "hundred-one" and "one-two" are not numbers at all.
_EN_TENS_VALUES = frozenset({20, 30, 40, 50, 60, 70, 80, 90})

#: A grouped integer, the only numeric form a count may take: "1,005" and "1005"
#: are the same number, "1,00" is a typo, and "105.5" is not a count of anything.
_GROUPED_INT_RE = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")

#: Prose that restates how many items the list has, in the two languages this
#: repository documents in. Spacing is optional because both "六个问题" and
#: "7 个问题" occur in practice, and a compound count such as "twenty-one
#: questions" is accepted for the same reason.
_RESTATED_COUNT_RE = re.compile(
    r"(?:[一二两三四五六七八九十]|\d+)\s*个\s*问题"
    r"|\b(?:\d+|" + _COUNT_WORDS + r")(?:[-\s](?:" + _COUNT_WORDS + r"))?\s+questions\b",
    re.IGNORECASE,
)

#: One objective row in the SLO runbook, e.g. ``| SLO-1 | Availability ... |``.
#: The marker must open the line so a prose reference to ``SLO-3`` is not counted
#: as a sixth objective.
_SLO_ROW_RE = re.compile(r"^\|\s*SLO-(\d+)\s*\|", re.MULTILINE)

#: A stated objective count, in either order and either language:
#: ``5 个 SLO 目标``, ``SLO 目标（5 个）``, ``five SLO objectives``. Every phrasing
#: the README actually uses is matched, because the point is to catch whichever
#: one drifts, not to police one canonical sentence. Each alternative names its
#: own group (``re`` forbids reusing one name), and exactly one is set per match.
_SLO_COUNT_RE = re.compile(
    # 百 千 and 零 are matched so the guard *sees* a count it cannot parse and
    # reports "cannot verify", rather than not matching and silently ignoring
    # the claim. A guard that shrugs at what it does not understand is not one.
    #
    # The lookbehind is load-bearing for the same reason the English one is:
    # Chinese numerals are contiguous ideographs, so `一百零五个` has no
    # separator to stop the class matching its `五个` tail — without the guard,
    # a claim of 105 would be read as 5 and quietly pass. 零 is what makes that
    # particular tail reachable, so it is in the class.
    # The numeric token is captured whole, and comma grouping, decimal point and
    # sign included, because a bare `\d+` starts *after* the punctuation:
    # "1,005" would match "005" and read as 5, certifying a claim of 1,005
    # against a five-objective runbook, and "-5" would match "5" and certify a
    # claim of -5 as 5. Capturing the sign instead makes the parser see "-5",
    # decline it, and report "cannot verify" — unchecked is safe, wrong is not.
    # The lookbehind stops a match beginning mid-number even if the shape
    # changes later.
    r"(?<![\u4e00-\u9fff])(?P<lead>[一二两三四五六七八九十百千零]+|[\d,.\-+]+)\s*个\s*SLO\s*目标"
    r"|SLO\s*目标\s*[（(]\s*(?<![\u4e00-\u9fff])(?P<trail>[一二两三四五六七八九十百千零]+|[\d,.\-+]+)\s*个"
    # The compound has to sit *inside* the group: capturing only the first word
    # made "twenty-one SLO objectives" parse as 20. The lookbehind keeps the
    # match from starting *inside* one instead — "twenty-one" has a word
    # boundary before "one". The `(?:...)` around the interpolated word list is
    # load-bearing: `_COUNT_WORDS` is an `a|b|c` alternation, so without it the
    # top-level `|` splits the whole pattern and only the final alternative
    # keeps the trailing `\s+SLO` requirement.
    r"|(?<![\w,.\-+])(?P<english>[\d,.\-+]+|(?:"
    + _COUNT_WORDS
    + r")(?:[-\s](?:"
    + _COUNT_WORDS
    + r"))?)\s+SLO\s+objectives?\b",
    re.IGNORECASE,
)

#: Chinese and English count words, so the guard compares numbers rather than
#: strings: "十个" must not slip past a check that only understands "10", and
#: "twenty-one" must not slip past one that only reads the first word.
#: "十" is handled structurally by `_parse_chinese_number`, not looked up here.
_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _parse_chinese_number(token: str) -> int | None:
    """Parse a Chinese numeral up to 99: 十一 = 11, 二十 = 20, 二十一 = 21.

    Needed because these are single words: a character class would match only
    the `二` in `十二个 SLO 目标` and report 2, rejecting a correct document, and
    the trailing form `SLO 目标（十二个）` would not match at all.

    Past 99 the notation stops being compositional — 一百 is 100 and 一千 is 1000,
    and 二百一十 has its own rules — so those return None rather than a wrong
    number. "Cannot verify" is the honest answer; 210 would not be.

    Total by construction: it returns None for anything it does not understand
    and never raises. This runs in CI, so a documentation typo like `二一十个`
    must produce a "cannot verify" error, not a traceback that aborts the whole
    consistency check and hides every other problem with the repository.
    """
    if not token or (set(token) - set(_CN_DIGITS)) - {"十"}:
        return None
    if "十" not in token:
        # A lone digit, or a run of digits with no 十, which is not a number.
        return _CN_DIGITS[token] if len(token) == 1 else None
    head, _, tail = token.partition("十")
    # Exactly one 十, with at most one known digit on each side of it. `.get`
    # rather than indexing: `二一十个` and `十一二个` are typos, not numbers, and
    # a KeyError here would take down the checker instead of reporting them.
    if "十" in head or "十" in tail:
        return None
    # An omitted leading 一 means one ten, so 十 = 10 and 十一 = 11.
    tens = _CN_DIGITS.get(head, 1) if len(head) <= 1 else None
    ones = _CN_DIGITS.get(tail, 0) if len(tail) <= 1 else None
    if tens is None or ones is None:
        return None
    return tens * 10 + ones


def slo_objective_rows(runbook_text: str) -> list[int]:
    """The objective identifiers the runbook defines, in document order.

    A list, not a set. Deduplicating here would hide exactly the mistake worth
    catching: six rows where two share an ``SLO-5`` would count as five, and
    every "5 个 SLO 目标" in the summaries would keep passing while the runbook
    described a sixth objective under an id that already existed.
    """
    return [int(number) for number in _SLO_ROW_RE.findall(runbook_text)]


def slo_objective_count(runbook_text: str) -> int:
    """How many SLO objectives ``docs/slo-runbook.md`` actually defines."""
    return len(slo_objective_rows(runbook_text))


def slo_objective_id_errors(runbook_name: str, runbook_text: str) -> list[str]:
    """Identifiers must be unique and contiguous from 1.

    Contiguity is what makes "SLO-6" mean the sixth objective. A gap or a repeat
    means a row was inserted or copy-pasted, and every stated count becomes
    ambiguous — so it is rejected here rather than silently absorbed into a
    total.
    """
    numbers = slo_objective_rows(runbook_text)
    if not numbers:
        return []
    errors: list[str] = []
    duplicates = sorted({number for number in numbers if numbers.count(number) > 1})
    if duplicates:
        errors.append(f"{runbook_name}: duplicate objective identifiers {duplicates}; each SLO-<n> must appear once")
    expected = list(range(1, len(numbers) + 1))
    if numbers != expected:
        errors.append(f"{runbook_name}: objective identifiers are {numbers} instead of a contiguous 1..{len(numbers)}")
    return errors


def _parse_stated_count(token: str) -> int | None:
    """Turn one captured count token into an int, or ``None`` if it is not one.

    An unrecognised token returns ``None`` so the guard reports "cannot verify"
    rather than silently accepting a phrase it failed to parse — a guard that
    shrugs at what it does not understand is not a guard.
    """
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    if _GROUPED_INT_RE.fullmatch(token):
        return int(token.replace(",", ""))
    if not token.isascii():
        return _parse_chinese_number(token)
    for separator in ("-", " "):
        head, found, tail = token.partition(separator)
        if not (found and head in _EN_COUNT_WORDS and tail in _EN_COUNT_WORDS):
            continue
        # A scale word multiplies: "five hundred" is 500, not 105.
        if tail in ("hundred", "thousand"):
            return _EN_COUNT_WORDS[head] * _EN_COUNT_WORDS[tail]
        # An additive compound is a tens word plus a unit, and nothing else:
        # "twenty-one", "forty-five", "ninety-nine". Constraining both sides is
        # what makes malformed shapes unreadable rather than merely unlikely —
        # "twenty-zero" is not 20, "twenty-eleven" is not 31, "twenty-thirty" is
        # not 50, and "hundred-one" is not 101. Each of those would otherwise
        # compute to a number that could coincide with the real count, and the
        # guard would certify a typo.
        if _EN_COUNT_WORDS[head] in _EN_TENS_VALUES and 1 <= _EN_COUNT_WORDS[tail] <= 9:
            return _EN_COUNT_WORDS[head] + _EN_COUNT_WORDS[tail]
    return _EN_COUNT_WORDS.get(token)


#: Words that can precede a count token as part of a *larger* numeral, rather than
#: starting one: "one hundred and five" continues into "five", and "twenty one"
#: is one numeral rather than two. `re` cannot express "not preceded by an
#: arbitrarily long number phrase" with a lookbehind, so the preceding context is
#: inspected in Python instead.
_NUMERAL_CONTINUATION_WORDS = frozenset(_COUNT_WORD_LIST) | {"and"}


def _continues_a_larger_numeral(text: str, start: int) -> bool:
    """Whether the match at ``start`` is the tail of a longer numeral.

    True for "five" in "one hundred and five SLO objectives", which would
    otherwise be read as a statement of five when the runbook defines five — a
    false pass, and the reason the regex alone cannot be trusted here.
    """
    prefix = text[:start].rstrip(" \t\u3000-")
    numerals = 0
    while prefix:
        head, _separator, last = prefix.rpartition(" ")
        candidate = last.strip(" \t\u3000-,").lower()
        if not candidate or candidate not in _NUMERAL_CONTINUATION_WORDS:
            break
        # "and" alone introduces a numeral; "and" after a numeral continues one.
        # Counting only the numeral words is what tells those two apart. The walk
        # ends by itself: the final token leaves an empty `head`.
        if candidate != "and":
            numerals += 1
        prefix = head.rstrip(" \t\u3000-")
    return numerals > 0


def slo_count_errors(name: str, text: str, expected: int) -> list[str]:
    """Return errors for objective counts that contradict the SLO runbook.

    The README states how many SLO objectives there are in three separate places
    while ``docs/slo-runbook.md`` is the only place that defines them. Nothing
    tied the two together, so a runbook edit left the summary claiming "十个"
    next to two correct "5"s in the same file — a self-contradiction inside a
    section presented as repository-reproducible evidence.

    Deriving the count from the runbook instead of restating it means adding
    SLO-6 can no longer leave the summary quietly behind.
    """
    errors: list[str] = []
    for match in _SLO_COUNT_RE.finditer(text):
        if _continues_a_larger_numeral(text, match.start()):
            errors.append(
                f"{name}: cannot verify the SLO objective count in {match.group(0)!r}; "
                f"it continues a longer numeral, and docs/slo-runbook.md defines {expected}"
            )
            continue
        token = match.group("lead") or match.group("trail") or match.group("english") or ""
        stated = _parse_stated_count(token)
        if stated is None:
            errors.append(
                f"{name}: cannot verify the SLO objective count in {match.group(0)!r}; "
                f"docs/slo-runbook.md defines {expected}"
            )
        elif stated != expected:
            errors.append(
                f"{name}: states {stated} SLO objectives but docs/slo-runbook.md defines {expected} "
                f"({match.group(0)!r})"
            )
    return errors


def check_slo_objective_counts(errors: list[str], root: Path | None = None) -> None:
    """Every stated SLO objective count must match the runbook that defines them."""
    base = ROOT if root is None else root
    runbook = base / "docs" / "slo-runbook.md"
    if not runbook.is_file():
        return
    runbook_text = runbook.read_text(encoding="utf-8")
    expected = slo_objective_count(runbook_text)
    if expected == 0:
        errors.append(
            "docs/slo-runbook.md: no `| SLO-<n> |` objective rows found; the count guard has nothing to compare"
        )
        return
    # Identifiers first: if two rows share an id then the count above is not a
    # count of objectives, and comparing summaries against it would bless the
    # very duplication that produced the wrong number.
    errors.extend(slo_objective_id_errors("docs/slo-runbook.md", runbook_text))
    for path in CANONICAL_DOCS:
        if not path.exists():
            continue
        errors.extend(slo_count_errors(_display(path), path.read_text(encoding="utf-8"), expected))


# --------------------------------------------------------------------------
# Kubernetes static-check count
# --------------------------------------------------------------------------
#
# The manifests under `deploy/k8s/` are the only `REPO_VERIFIED` Kubernetes claim
# this repository makes, and four current documents state how many static checks
# back it. The test module defines them, so the count is derived from that module
# rather than restated: `26` outlived four added checks and sat next to two correct
# `31`s in the same repository, which is exactly the drift this guard is for.
#
# The comparison is deliberately narrow. This guard knows about one number — the
# size of one test module — and it only claims to understand a number whose own
# Markdown window names the Kubernetes subject it belongs to. It is not a
# repository-wide "every number must agree" framework and must not grow into one.

K8S_STATIC_CHECK_MODULE = Path("tests/deploy/test_k8s_manifests.py")


def k8s_static_check_count(module_path: Path) -> int:
    """How many module-level test functions the K8s manifest contract defines.

    Parsed rather than counted by running pytest: this guard runs in CI as a
    documentation check and must stay offline and cheap. Module level only, so a
    nested helper that happens to be named ``test_*`` cannot inflate the number.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    return sum(1 for node in tree.body if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"))


#: A stated count in either language: ``26 项``, ``31 项``, ``31 static checks``.
#: Each alternative names its own groups, and exactly one is set per match. Matched
#: against a whitespace-normalised clause, so a soft wrap cannot split a claim.
#: The Chinese numeral class includes 百/千/零 so the parser *sees* a count it cannot
#: read and reports "cannot verify" instead of silently ignoring the claim, and the
#: numeric token is captured whole — sign, grouping and decimal point included —
#: so ``-5`` is never read as ``5``.
#:
#: ``N 项`` on its own is not a static-check count. Chinese counts items of every
#: kind, so in ``Kubernetes 清单的 5 项资源由 31 项静态检查覆盖。`` the 5 counts
#: resources. The Chinese count therefore has to be bound to ``静态检查`` — in either
#: direction, since the documents use both:
#:
#: * count first: it must modify the phrase, ``31 项静态检查``;
#: * phrase first: it must be the nearest count after the phrase, and it must be a
#:   *bare tally* — ``静态检查指…（31 项，全部离线）``. A count that goes on to modify
#:   something is not a check count: in ``静态检查覆盖 5 项资源。`` the 5 counts
#:   resources, and in ``5 项 YAML 资源`` the counted noun is one gap further along,
#:   which is why the test for a bare tally is what may follow (a closing bracket or
#:   the end of the clause) rather than a character that must not. The rule is
#:   deliberately "unless shown to count something else": ``静态检查共 5 项。`` is
#:   ambiguous prose read as five checks, because nothing else in it takes the number.
#:
#: This mirrors the English alternative, which already requires the phrase to follow
#: the number.
#:
#: The lookbehind refuses only a match that would begin *inside* a numeral, not
#: every start that follows a Chinese character. Chinese numerals are contiguous
#: ideographs, so ``一百零五项`` would otherwise be matched from its ``五`` tail
#: and read as 5; blocking on the numeral characters themselves catches that while
#: still matching ``契约与二十六项``, where ``与`` is ordinary prose. Blocking on all
#: CJK would silently skip any count written without a space after a Chinese word,
#: which is exactly the guard ignoring the claim it was added to catch.
#:
#: The characters a bound count may not be separated from the phrase by: clause
#: punctuation (the claim has moved on) and ``项`` itself (another count is in
#: between, so the nearest one is no longer the one the phrase modifies).
_CN_UNBOUND = "；;。、！？!?,，项"

#: What a phrase-first tally has to be followed by to count as a *check* count: a
#: closing bracket, or the end of the clause. It is a positive test rather than a
#: negative one on purpose. "The next character is not Han" still accepts
#: ``5 项 资源`` and ``5 项 YAML 资源``, because the space in between is not Han
#: either — the counted noun is still there, one gap further along. A bare tally
#: is a tally that runs out: nothing follows it inside the clause it belongs to.
#:
#: Inline delimiters are transparent to that test. ``静态检查共 **26 项**。`` and
#: ``静态检查共 `26 项`。`` are the same claim as the bare form, and a terminator
#: that stopped at the closing ``*`` would silently drop them — which is worse than
#: a false positive, because nothing would say the claim went unverified. Plain
#: whitespace is transparent for the same reason: ``共 26 项 ）`` is the bare form
#: with a space in it. Neither weakens the test, because what follows the whitespace
#: must still be a bracket or the end of the clause.
_CN_INLINE_DELIMITERS = r"[*_`~]"
_CN_TALLY_END = r"(?=(?:" + _CN_INLINE_DELIMITERS + r"[ \t]*)*[ \t]*(?:[)\]】）]|$))"

_K8S_STATIC_CHECK_COUNT_RE = re.compile(
    r"(?<![一二两三四五六七八九十百千零\d,.\-+])(?P<chinese>[一二两三四五六七八九十百千零]+|[\d,.\-+]+)\s*项\s*静态检查"
    + r"|静态检查(?P<cn_trail_gap>[^"
    + _CN_UNBOUND
    + r"]{0,60}?)(?<![一二两三四五六七八九十百千零\d,.\-+])(?P<cn_trail>"
    + r"[一二两三四五六七八九十百千零]+|[\d,.\-+]+)\s*项"
    + _CN_TALLY_END
    + r"|(?<![\w,.\-+])(?P<english>[\d,.\-+]+|(?:"
    + _COUNT_WORDS
    + r")(?:[-\s](?:"
    + _COUNT_WORDS
    + r"))?)\s+static\s+checks?\b",
    re.IGNORECASE,
)

#: The count groups, in the order they appear in the alternatives above. Named
#: rather than indexed so adding an alternative cannot silently shift which token a
#: parse reads; the count's own span is used for the numeral-continuation check.
_K8S_COUNT_GROUPS = ("chinese", "cn_trail", "english")

#: What makes a number a *Kubernetes* static-check count. Deliberately an explicit
#: subject list rather than "the word static checks": a repository can and does write
#: about static checks that have nothing to do with `deploy/k8s/`, and comparing
#: those against the manifest module's count would be a fabricated error. The
#: subject has to appear in the *same clause* as the count, not merely somewhere in
#: the paragraph: a paragraph that names Kubernetes and then, in a separate clause,
#: states another suite's tally is two claims, and window-level licensing would read
#: the second one as the first one's count.
#:
#: `manifest` on its own is deliberately *not* in this list, which it was until the
#: review caught it. The noun is generic — `The frontend manifest`, `The Python
#: package manifest` — and treating any of them as the Kubernetes manifest is the
#: same fabricated error with a different word. A Kubernetes-qualified reference
#: still matches through `kubernetes` / `k8s`, and the module is named outright, so
#: no current document lost its guard: the English claim in
#: `docs/repository-truth-audit.md` names `tests/deploy/test_k8s_manifests.py` in
#: the same clause. A bare "The manifests are covered by 31 static checks" is now
#: out of scope, which is the trade the guard accepts — out of scope beats wrong.
_K8S_SUBJECT_CONTEXT_RE = re.compile(r"kubernetes|k8s|test_k8s_manifests", re.IGNORECASE)

#: The phrase that turns a number in an in-scope window into a static-check count.
_K8S_STATIC_CHECK_PHRASE_RE = re.compile(r"静态检查|static\s+checks?", re.IGNORECASE)

#: Markdown structural boundaries that end a semantic window. A blank line always
#: does; so does a new block element, because a heading, a table row and a list item
#: are separate claims that merely happen to sit next to each other. Wrapped
#: continuation lines match none of these and therefore join the window they belong
#: to, which is what stops an ordinary soft wrap from hiding a claim.
_K8S_BLOCK_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
_K8S_BLOCK_TABLE_RE = re.compile(r"^\s{0,3}\|")
_K8S_BLOCK_LIST_RE = re.compile(r"^\s{0,3}(?:[-*+]|\d{1,3}[.)])\s+")

#: The block kinds, longest-ambiguity first, so a heading is not read as a list
#: item and a row is not read as either. Order is load-bearing.
_K8S_BLOCK_KINDS = (
    ("heading", _K8S_BLOCK_HEADING_RE),
    ("table", _K8S_BLOCK_TABLE_RE),
    ("list", _K8S_BLOCK_LIST_RE),
)

#: A blockquote line, and the marker to strip off it. Standard Markdown prefixes
#: *every* source line of a paragraph with ``>``, so the marker repeats per line and
#: says nothing about where a claim ends: treating each marked line as its own block
#: would split one wrapped paragraph into one window per line, and a stale count
#: would slip through merely because the author rewrapped it. The marker is removed
#: instead, so the window holds the prose a reader actually sees.
#:
#: Exactly one space after the last ``>`` is consumed, because that one space is
#: part of the marker. The rest is the block's own indentation, and it has to
#: survive: ``> - item`` followed by ``>   continuation`` is one list item, and a
#: marker regex that ate the indentation would make the continuation look
#: unindented and split the item in two.
_K8S_BLOCK_QUOTE_RE = re.compile(r"^\s{0,3}>")

#: The whole leading blockquote marker run of a line, with the content's own
#: indentation still attached. Depth, body and indentation all come from this one
#: match, because deriving them from separate regexes is how they came to disagree.
#:
#: The asymmetry in the pattern is the load-bearing part:
#:
#: A line with tabs expanded to CommonMark's four-column tab stops, because the
#: block structure is defined in columns and not in characters. ``>\\t> text`` is a
#: valid depth-2 quote: the tab carries the line to column 4, the outer marker's
#: padding leaves two permitted indentation columns, and the second ``>`` is a
#: marker. Counting characters instead gets this wrong in both directions —
#: ``[ \\t]{0,4}`` reads ``>\\t\\t\\t\\t> text`` as nested when the four tabs carry the
#: line to column 16, and a spaces-only bound misses the single-tab case entirely.
#: Windows are whitespace-normalised anyway, so expanding here changes no claim text.
_K8S_TAB_STOP = 4


def _expand_tabs(line: str) -> str:
    """Expand ``\\t`` to the next four-column tab stop, as CommonMark defines it."""
    if "\t" not in line:
        return line
    column = 0
    out: list[str] = []
    for char in line:
        if char == "\t":
            width = _K8S_TAB_STOP - (column % _K8S_TAB_STOP)
            out.append(" " * width)
            column += width
        else:
            out.append(char)
            column += 1
    return "".join(out)


#: * *between* markers, up to four columns are consumed. Three of those are the
#:   inner marker's permitted indentation; the fourth is the outer marker's own
#:   optional space, which is why the bound is four and not three — `>    > text` is
#:   valid and reading it as depth 1 merges the inner paragraph into the outer one.
#:   Spaces, because tabs are gone by now. The lookahead keeps the slack harmless:
#:   the run only grows when another ``>`` actually follows.
#: * after the *last* marker, at most one space is consumed, because that single
#:   space is the marker's own optional space. Everything after it is the block's
#:   content indentation.
#:
#: Consuming greedily in both positions (``(?:>[ \t]{0,3})*``) destroys the second
#: measurement: ``>   continuation`` loses its two content spaces entirely, so a
#: wrapped quoted list item looks like it has no indentation and gets split in two.
#: That is not fixable downstream, which is why the asymmetry is here rather than in
#: the arithmetic that consumes this match.
_K8S_QUOTE_PREFIX_RE = re.compile(r"^\s{0,3}(?:>[ ]{0,4}(?=>))*>[ \t]?")

#: Punctuation that separates clauses inside one window. A count only counts as a
#: static-check count when it shares a clause with the phrase *and* the Kubernetes
#: subject, so a paragraph that mentions an unrelated tally elsewhere is not dragged
#: into the comparison. A newline is intentionally *not* a separator: joining across
#: a soft wrap is the whole point.
#:
#: Two English shapes have to be separated without being over-split:
#:
#: * A sentence-ending period. Two sentences in one paragraph are two claims, and
#:   leaving them joined lets the second borrow the first's subject. It is a boundary
#:   only when whitespace or the end of the window follows it, so a period inside a
#:   token is left alone: ``1.5``, ``README.md`` and ``api-deployment.yaml`` keep
#:   their periods, while ``schema 1.5. The frontend...`` splits. Nothing excludes a
#:   preceding digit — a sentence that ends in a number ends in a number as often as
#:   not, and refusing to split there is exactly the leak being closed.
#: * A digit-grouping comma. Splitting there would read ``1,031 static checks`` as
#:   ``031``, certifying a claim of 1,031 as the expected count. The grouping shape
#:   is the whole check rather than "a digit on each side": ``26, static`` is a
#:   sentence boundary, and ``1,00`` is a typo, not a group.
#:
#: Every other comma, Chinese or not, still separates.
_K8S_CLAUSE_SPLIT_RE = re.compile(r"[；;。，、!?！？]+|,(?!\d{3}(?!\d))|\.(?=\s|$)")

#: A Setext heading underline: a paragraph line made only of ``=`` or ``-``. The
#: line carries no claim, but it *is* the heading mark — the text above it is a
#: heading, so the window closes there instead of absorbing the paragraph below.
_K8S_SETEXT_UNDERLINE_RE = re.compile(r"^\s{0,3}(?:=+|-+)\s*$")

#: A separator-only line: ``|---|``, ``---``, ``:::``. It opens a table block but
#: carries no claim, so it starts a window and contributes nothing to it.
_K8S_SEPARATOR_ONLY_RE = re.compile(r"^[\s|:\-]+$")


def _markdown_claim_windows(text: str) -> list[str]:
    """Split ``text`` into bounded Markdown windows for claim scoping.

    A window is one block element — a paragraph, a single table row, one list item
    with its wrapped continuation lines, one blockquote paragraph, a Setext
    heading's text line. Windows are bounded on purpose: the whole document is
    never concatenated, because doing so would let a number hundreds of lines away
    from a ``static checks`` phrase be read as its count.

    Windows are bounded per *block*, and what decides whether the next ordinary
    line joins the current window is the kind of block already open, because
    CommonMark treats the shapes differently:

    * A paragraph absorbs the unmarked line that follows it — that is ordinary
      wrapping, and inside a quote it is the *lazy continuation* form that most
      people use to wrap a quoted claim. An unconditional flush there would drop
      the count out of the subject's window and let a rewrap hide it.
    * A heading or a table row is a single line, so the line after it is the
      start of a new paragraph. Attaching that paragraph to the heading would let
      the heading's subject authorise the paragraph's count. A Setext heading is
      this in a different spelling: the text line plus its underline, where the
      underline is a boundary rather than content.
    * A list item is not a single line — its continuations are part of it.

    Only a *paragraph* takes a lazy continuation. `> ## Kubernetes manifests`
    followed by prose is a quoted heading followed by a paragraph, and quoting the
    heading does not make the two one block.

    Windows are whitespace-normalised, so a claim written across a soft wrap is
    matched as the single line a reader actually sees.
    """
    windows: list[str] = []
    current: list[str] = []
    # The block kind the open window belongs to: one of the ``_K8S_BLOCK_KINDS``
    # names, ``"paragraph"``, or ``None`` when no window is open.
    open_block: str | None = None
    # Whether the open window is a blockquote, and at what depth. Depth may be
    # omitted by a continuation line but not increased by one.
    open_quoted = False
    open_depth = 0

    def flush() -> None:
        nonlocal open_block, open_quoted, open_depth
        joined = " ".join(" ".join(current).split())
        if joined:
            windows.append(joined)
        current.clear()
        open_block = None
        open_quoted = False
        open_depth = 0

    def block_kind(text: str) -> str:
        for name, pattern in _K8S_BLOCK_KINDS:
            if pattern.match(text):
                return name
        return "paragraph"

    for line in text.splitlines():
        line = _expand_tabs(line)
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if _K8S_SETEXT_UNDERLINE_RE.match(line):
            # The underline marks the text above it as a heading: that window is
            # already complete, and it does not carry a claim of its own.
            flush()
            continue
        in_quote = bool(_K8S_BLOCK_QUOTE_RE.match(line))
        if in_quote:
            prefix = _K8S_QUOTE_PREFIX_RE.match(line).group(0)
            depth = prefix.count(">")
            content = line[len(prefix) :]
        else:
            depth = 0
            content = line
        # The block's own content, with the quote prefix removed. Windows are
        # whitespace-normalised, so the leading indentation a continuation carries is
        # not compared against anything — a paragraph's continuation is lazy about it.
        body = content.strip()
        # A `>`-only line is a paragraph break inside the quote, not content.
        if not body or _K8S_SEPARATOR_ONLY_RE.match(body):
            flush()
            continue
        kind = block_kind(body)
        # Which block the line continues, decided by the block that is already open.
        # The open block has to be a paragraph (or a list item, whose content is a
        # paragraph): a heading or a table row is one line, so the next line is a new
        # block even when it looks like a wrap.
        #
        # A paragraph is *lazy* about its continuation, and both halves of that
        # laziness are load-bearing:
        #
        # * quote depth may be omitted, not only equal. `>>> wrapped\n> text` is one
        #   paragraph inside the depth-3 quote; only *entering* a deeper quote starts a
        #   block, because a quote marker begins one. The depth a line may not exceed
        #   is the paragraph's *effective* container depth — the deepest marker it has
        #   been written with — not the previous line's written depth. Those differ
        #   exactly in the restore case: `>>> …26\n> static\n>> checks.` omits markers
        #   and then brings some back, and comparing against the reduced middle line
        #   reads the third line as a deeper quote and splits one CommonMark paragraph.
        # * indentation may be omitted. `- wrapped by 26 static\nchecks.` is one
        #   paragraph in the item, because the list-item laziness rule lets the
        #   continuation's hanging indent be dropped.
        #
        # Requiring an exact depth or a full content column rejects both. What keeps
        # the looser reading safe downstream is that neither is the only bound: the
        # clause-level subject binding and the sentence split still decide whether the
        # number belongs to the subject, so a lazy join widens the window and not the
        # comparison.
        if not in_quote and not open_quoted:
            container_continues = True
        elif in_quote and open_quoted:
            container_continues = depth <= open_depth
        elif open_quoted:
            container_continues = True  # unmarked line leaving a quote: still its paragraph
        else:
            container_continues = False  # a quote marker always begins a block
        continues_open_block = kind == "paragraph" and container_continues and open_block in ("paragraph", "list")
        if not continues_open_block:
            flush()
        current.append(body)
        open_block = kind
        open_quoted = in_quote
        # Keep the paragraph's effective container depth across lazy lines: a line may
        # drop markers, and a later line may bring some back without leaving the
        # container. Recording the written depth each time makes the restore look like
        # a deeper quote.
        open_depth = max(open_depth, depth)
    flush()
    return windows


def k8s_static_check_count_errors(name: str, text: str, expected: int) -> list[str]:
    """Return errors for a stated Kubernetes static-check count that is wrong.

    Scoping is deliberately two bounds deep. A number is only ever read as a
    Kubernetes static-check count when, inside one bounded Markdown window:

    1. its own clause mentions static checks at all;
    2. its own clause names a Kubernetes subject (manifests, ``k8s``, the module),
       so the count belongs to ``deploy/k8s/`` rather than to another static-check
       suite mentioned in the same paragraph;
    3. that same clause contains the number.

    The subject is required *per clause*, not per window, and that is the point: one
    paragraph routinely holds two unrelated claims — "the manifests are documented
    here; the frontend is covered by 12 static checks" — and a window-level subject
    check licenses the second clause to borrow the first one's subject.

    A count the guard cannot parse is reported rather than accepted. "Cannot
    verify" is the honest answer for a shape it does not understand; quietly
    skipping it would leave the exact line it was added to catch unguarded.
    """
    errors: list[str] = []
    module = K8S_STATIC_CHECK_MODULE.as_posix()
    for window in _markdown_claim_windows(text):
        for clause in _K8S_CLAUSE_SPLIT_RE.split(window):
            if not _K8S_STATIC_CHECK_PHRASE_RE.search(clause):
                continue
            if not _K8S_SUBJECT_CONTEXT_RE.search(clause):
                continue
            for match in _K8S_STATIC_CHECK_COUNT_RE.finditer(clause):
                group = next((name for name in _K8S_COUNT_GROUPS if match.group(name)), None)
                token = match.group(group) if group else ""
                stated = _parse_stated_count(token)
                # The span of the count itself, not of the match: the Chinese
                # alternative starts at 静态检查, and a numeral-continuation check
                # anchored on the phrase would look at the wrong preceding words.
                count_start = match.start(group) if group else match.start()
                if _continues_a_larger_numeral(clause, count_start):
                    errors.append(
                        f"{name}: cannot verify the static-check count in {match.group(0)!r}; "
                        f"it continues a longer numeral, and {module} defines {expected} test functions"
                    )
                elif stated is None:
                    errors.append(
                        f"{name}: cannot verify the static-check count in {match.group(0)!r}; "
                        f"{module} defines {expected} test functions"
                    )
                elif stated != expected:
                    errors.append(
                        f"{name}: states {stated} Kubernetes static checks but {module} defines {expected} "
                        f"({match.group(0)!r})"
                    )
    return errors


def check_k8s_static_check_counts(errors: list[str], root: Path | None = None) -> None:
    """Every stated K8s static-check count must match the module that defines them."""
    base = ROOT if root is None else root
    module = base / K8S_STATIC_CHECK_MODULE
    if not module.is_file():
        return
    expected = k8s_static_check_count(module)
    if expected == 0:
        errors.append(
            f"{K8S_STATIC_CHECK_MODULE.as_posix()}: no module-level test functions found; "
            "the count guard has nothing to compare the documents against"
        )
        return
    for path in CANONICAL_DOCS:
        if not path.exists():
            continue
        errors.extend(k8s_static_check_count_errors(_display(path), path.read_text(encoding="utf-8"), expected))


def _markdown_sections(text: str) -> list[tuple[str, str]]:
    """Split ``text`` into ``(heading, body)`` pairs on level-2 headings.

    Content above the first ``## `` heading is returned under an empty heading so
    a stray list in the document preamble is still inspected rather than dropped.
    """
    sections: list[tuple[str, str]] = []
    heading = ""
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            sections.append((heading, "\n".join(body)))
            heading = line[3:].strip()
            body = []
        else:
            body.append(line)
    sections.append((heading, "\n".join(body)))
    return sections


def enumerated_count_errors(name: str, text: str) -> list[str]:
    """Return errors for a section whose prose contradicts its own enumeration.

    Two failure modes, both silent before this guard existed:

    1. the enumeration is not ``1..N`` — a duplicated, skipped or reordered
       marker, so the list reads as complete but is not;
    2. the prose hardcodes how many items there are, which goes stale the moment
       an item is added or removed and is what produced "六个问题" over Q1..Q7.
    """
    errors: list[str] = []
    for heading, body in _markdown_sections(text):
        numbers = [int(match) for match in _ENUMERATED_ITEM_RE.findall(body)]
        if not numbers:
            continue
        expected = list(range(1, len(numbers) + 1))
        if numbers != expected:
            errors.append(
                f"{name}: section {heading!r} enumerates {numbers} instead of a contiguous 1..{len(numbers)} sequence"
            )
        restated = _RESTATED_COUNT_RE.findall(body)
        if restated:
            errors.append(
                f"{name}: section {heading!r} restates a hardcoded count of its own "
                f"enumeration ({sorted(set(restated))}) while listing {len(numbers)} items; "
                "delete the number so the prose cannot drift out of sync"
            )
    return errors


def check_enumerated_section_counts(errors: list[str]) -> None:
    """No canonical document may miscount its own enumerated section."""
    for path in CANONICAL_DOCS:
        if not path.exists():
            continue
        errors.extend(enumerated_count_errors(_display(path), path.read_text(encoding="utf-8")))


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
    check_docs_classification(errors)
    check_ragas_failure_contract(errors)
    check_local_runtime_validation_contract(errors)
    check_uvicorn_proxy_headers_disabled(errors)
    check_evidence_classification_guards(errors)
    check_version_label_semantics(errors)
    check_prd_design_targets(errors)
    check_enterprise_readiness_contracts(errors)
    check_enterprise_readiness_coverage(errors)
    check_exporter_truth_contract(errors)
    check_interview_baseline_exporter_split(errors)
    check_capability_rows_are_unique(errors)
    check_evidence_levels_are_defined(errors)
    check_qdrant_evidence_reconciliation(errors)
    check_evidence_vocabulary_is_canonical(errors)
    check_docs_index_vocabulary(errors)
    check_operational_metric_references(errors)
    check_audit_tracker_lineage(errors)
    check_observability_is_optional(errors)
    check_legacy_jaeger_agent_config_is_absent(errors)
    check_canonical_runtime_is_not_observability_gated(errors)
    check_frontend_evidence_classification(errors)
    check_removed_plan_path_is_historical(errors)
    check_enumerated_section_counts(errors)
    check_slo_objective_counts(errors)
    check_k8s_static_check_counts(errors)

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
