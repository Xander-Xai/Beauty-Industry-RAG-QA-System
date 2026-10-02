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
    # Validation records and the benchmark artifact contract make the same
    # evidence claims as the guides, so they are held to the same guard. They
    # were previously unguarded, which left the most claim-heavy docs free to
    # drift.
    *sorted((ROOT / "docs" / "validation").glob("*.md")),
    ROOT / "artifacts/benchmarks/README.md",
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


# A sentence that frames the match as a claim to avoid, not as current truth.
# These docs are *required* to write the denial, so the frame is a signal to
# skip rather than a signal to fail.
_PROHIBITION_FRAME_RE = re.compile(
    r"\bClaiming\b|\bClaims?\b|\bSays?\b|\bDo\s+not\b|\bDon't\b|\bNever\b|\bMust\s+not\b|"
    r"不能说|不得|不要(?:说|声称)|不应(?:说|声称)",
    re.IGNORECASE,
)

# Absence / not-yet wording in the neighbourhood of the match. Covers both the
# "no result exists" form and the specific "no exporter configured" form that
# denies a closed tracing loop.
_NO_EVIDENCE_NEGATION_RE = re.compile(
    r"PENDING|EXTERNAL_MODEL_ASSET_REQUIRED|"
    r"未(?:有|能|执行|验证|产生|配置|运行)|尚未|没有|无可|不(?:会|能|得|是)|"
    r"not\s+(?:yet\s+)?(?:validated|verified|measured|available|produced|configured|"
    r"reproduced|reproducible|closed)|"
    r"no\s+(?:such|exporter|OTLP|closed|real\s+benchmark|artifact)|"
    r"exporter[^\n]{0,12}(?:not|un)\s*configured|"
    r"never|cannot|can't|without|must\s+not|do(?:es)?\s+not|do(?:es)?n't|"
    r"design\s+target|目标|blocked",
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


def otel_runtime_evidence_exists() -> bool:
    """True when an OTLP closed-loop run has been recorded.

    A closed-loop claim requires a recorded trace artifact. This repository keeps
    such evidence under ``monitoring/evidence/`` when a local run has actually
    happened; the directory does not exist today, which is why the runtime
    closed loop is PENDING rather than verified.
    """
    evidence = ROOT / "monitoring" / "evidence"
    if not evidence.is_dir():
        return False
    return any(evidence.glob("*.json"))


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

#: Operational docs whose `rag_*` references an operator will paste into a query.
OPERATIONAL_METRIC_DOCS = (
    "docs/operations-guide.md",
    "docs/slo-runbook.md",
    "docs/pre-launch-checklist.md",
    "docs/deployment-guide.md",
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
    """
    available = emitted_metric_reference_set()
    found: list[tuple[int, str]] = []
    for index, line in enumerate(text.splitlines()):
        for match in re.finditer(r"\brag_[A-Za-z0-9_]+", line):
            token = match.group(0)
            if match.end() < len(line) and line[match.end()] == "_":
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

#: The canonical classification vocabulary. A delivered row that names none of
#: these is asserting an unclassified capability.
_CLASSIFICATION_TOKENS = (
    "REPO_VERIFIED",
    "DESIGN_TARGET",
    "PENDING",
    "STALE",
    "HISTORICAL",
    "LOCAL_REAL_VALIDATION",
    "PARTIAL",
    "VERIFIED",
)

#: Long-lived trackers for evidence that only an external environment can produce.
#: None of this repository's own changes can close them, so the audit must keep
#: recording them as open. The numbers are stable by construction: a closed tracker
#: is deleted from this list in the same commit that closes it.
OPEN_EXTERNAL_VALIDATION_TRACKERS = (8, 12, 18)

_BULLET_SPLIT_RE = re.compile(r"(?m)^(?=\s*[-*]\s)")


def _tracker_section(audit_text: str) -> str | None:
    match = re.search(r"(?ms)^##\s+External validation tracker map\s*$(.*?)(?=^##\s|\Z)", audit_text)
    return match.group(1) if match else None


def _tracker_bullets(tracker: str) -> list[str]:
    """Split the tracker map into bullets, including hard-wrapped continuations."""
    starts = [match.start() for match in _BULLET_SPLIT_RE.finditer(tracker)]
    if not starts:
        return [tracker]
    bounds = starts + [len(tracker)]
    return [tracker[bounds[index] : bounds[index + 1]] for index in range(len(starts))]


def audit_tracker_errors(audit_text: str) -> list[str]:
    """Require the audit to separate delivered scope from open external validation."""
    errors: list[str] = []

    tracker = _tracker_section(audit_text)
    if tracker is None:
        errors.append("repository truth audit: missing the 'External validation tracker map' section")
        return errors

    bullets = _tracker_bullets(tracker)
    for number in OPEN_EXTERNAL_VALIDATION_TRACKERS:
        reference = re.compile(r"#[\[({]?" + str(number) + r"\b")
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

    for area in DELIVERED_AUDIT_AREAS:
        row = next(
            (line for line in audit_text.splitlines() if re.match(rf"^\|\s*{re.escape(area)}\s*\|", line)),
            None,
        )
        if row is None:
            errors.append(f"repository truth audit: no row for delivered area {area!r}")
            continue
        if not any(token in row for token in _CLASSIFICATION_TOKENS):
            errors.append(
                f"repository truth audit: delivered area {area!r} carries no explicit classification; "
                "an unclassified delivered capability reads as a completed result"
            )
    return errors


def check_audit_tracker_lineage(errors: list[str]) -> None:
    """The audit must keep completed implementation and open validation separable."""
    path = ROOT / "docs/repository-truth-audit.md"
    if not path.exists():
        return
    errors.extend(audit_tracker_errors(path.read_text(encoding="utf-8")))


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
        if status not in STATUSES:
            fail(errors, f"repository audit line {line_number + 1}: invalid status {status!r}")
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
    check_evidence_classification_guards(errors)
    check_version_label_semantics(errors)
    check_prd_design_targets(errors)
    check_enterprise_readiness_contracts(errors)
    check_enterprise_readiness_coverage(errors)
    check_exporter_truth_contract(errors)
    check_interview_baseline_exporter_split(errors)
    check_operational_metric_references(errors)
    check_audit_tracker_lineage(errors)
    check_observability_is_optional(errors)
    check_canonical_runtime_is_not_observability_gated(errors)

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
