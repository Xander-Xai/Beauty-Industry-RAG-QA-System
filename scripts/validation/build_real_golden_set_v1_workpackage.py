#!/usr/bin/env python3
"""Build the human review work package for the real-corpus V1 candidates.

This tool does not review anything. It renders the 27 existing retrieval
candidates and the 3 answer-gate candidates into a worksheet a real person can
work through line by line, and it adds three pieces of *analysis* the previous
README did not carry:

1. **Passage/question derivation risk.** The questions were drafted from the
   corpus, so a high lexical overlap with the annotated passage is evidence of
   possible reverse-generation bias (a question that is easy precisely because
   it reuses the passage's wording). The heuristic is reported as a risk flag,
   never as a verdict, and it changes no label.
2. **Possibly-missing passages.** For each candidate the tool lists sibling
   chunks of a multi-chunk document and lexically related chunks that were not
   annotated. They are *review leads*, not additions: a reviewer must decide.
3. **An independent-query proposal list.** A small set of user-shaped queries
   written without copying a passage, kept in a separate file and explicitly
   outside the dataset. They are future material, not ground truth.

Everything stays ``DRAFT_UNVERIFIED``. Decision, reviewer, date and notes are
emitted blank. No field is inferred from the candidate itself.

Usage::

    python3 scripts/validation/build_real_golden_set_v1_workpackage.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

WORKPACKAGE_SCHEMA = "real-corpus-review-workpackage/1"
ANSWER_GATE_SCHEMA = "real-corpus-answer-gate-workpackage/1"
INDEPENDENT_SCHEMA = "real-corpus-independent-query-proposals/1"

RETRIEVAL_FILE = "retrieval_review_workpackage.jsonl"
RETRIEVAL_MD = "RETRIEVAL_REVIEW_WORKPACKAGE.md"
ANSWER_FILE = "answer_gate_review_workpackage.jsonl"
ANSWER_MD = "ANSWER_GATE_REVIEW.md"
INDEPENDENT_FILE = "independent_query_proposals.jsonl"
INDEPENDENT_MD = "INDEPENDENT_QUERY_PROPOSALS.md"

#: Decisions a reviewer may record. ``""`` is the untouched state.
DECISIONS = ("", "ACCEPT", "MODIFY", "REJECT")

_CJK = re.compile(r"[\u4e00-\u9fff]")
_ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]{1,}")


def _norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", str(text)).lower()


def _tokens(text: str) -> set[str]:
    """CJK bigrams plus ASCII words, enough for a deterministic overlap signal."""
    tokens: set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", str(text)):
        tokens.update(run[index : index + 2] for index in range(len(run) - 1))
    tokens.update(word.lower() for word in _ASCII_WORD.findall(str(text)))
    return tokens


def longest_common_substring(left: str, right: str) -> int:
    """Length of the longest common contiguous substring (classic DP, O(n*m))."""
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    best = 0
    for left_char in left:
        current = [0] * (len(right) + 1)
        for index, right_char in enumerate(right, start=1):
            if left_char == right_char:
                current[index] = previous[index - 1] + 1
                if current[index] > best:
                    best = current[index]
        previous = current
    return best


def derivation_risk(question: str, passages: list[str]) -> dict:
    """Report possible reverse-generation bias; never change a label.

    The signal is lexical: a question assembled from a passage shares long
    contiguous runs and most of its bigrams with that passage. That is a reason
    for a human to look closely, not proof of anything — a legitimately phrased
    question can also quote a term of art.
    """
    question_norm = _norm(question)
    question_tokens = _tokens(question)
    best_lcs = 0
    best_ratio = 0.0
    best_coverage = 0.0
    best_index: int | None = None
    for index, passage in enumerate(passages):
        passage_norm = _norm(passage)
        lcs = longest_common_substring(question_norm, passage_norm)
        ratio = lcs / len(question_norm) if question_norm else 0.0
        passage_tokens = _tokens(passage)
        coverage = len(question_tokens & passage_tokens) / len(question_tokens) if question_tokens else 0.0
        if (ratio, coverage) > (best_ratio, best_coverage):
            best_lcs, best_ratio, best_coverage, best_index = lcs, ratio, coverage, index

    if best_ratio >= 0.5 or best_coverage >= 0.9:
        level = "HIGH"
    elif best_ratio >= 0.3 or best_coverage >= 0.75:
        level = "MEDIUM"
    else:
        level = "LOW"
    return {
        "level": level,
        "longest_common_substring_chars": best_lcs,
        "lcs_ratio_of_question": round(best_ratio, 3),
        "question_bigram_coverage": round(best_coverage, 3),
        "closest_annotation_index": best_index,
        "heuristic": (
            "Lexical overlap only. HIGH/MEDIUM means the question may have been written from the passage "
            "and is therefore easier than a real user query; it is a review prompt, not a label change."
        ),
    }


def sibling_passages(logical_id: str, chunks: list[dict], annotated: set[str]) -> list[dict]:
    """Other chunks of a multi-chunk source document, not already annotated."""
    return [
        chunk for chunk in chunks if str(chunk["logical_id"]) == logical_id and str(chunk["chunk_id"]) not in annotated
    ]


def related_passages(
    question: str,
    chunks: list[dict],
    annotated: set[str],
    *,
    limit: int = 3,
    minimum_overlap: int = 2,
) -> list[dict]:
    """Lexically related chunks that were not annotated.

    These are *leads*: a chunk can share a substance name and still be irrelevant.
    The reviewer decides whether any of them belongs in ``annotations``.
    """
    question_tokens = _tokens(question)
    if not question_tokens:
        return []
    scored: list[tuple[int, str, dict]] = []
    for chunk in chunks:
        chunk_id = str(chunk["chunk_id"])
        if chunk_id in annotated:
            continue
        overlap = len(question_tokens & _tokens(chunk["text"]))
        if overlap >= minimum_overlap:
            scored.append((overlap, chunk_id, chunk))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [
        {
            "doc_id": str(chunk["doc_id"]),
            "chunk_id": str(chunk["chunk_id"]),
            "logical_id": str(chunk["logical_id"]),
            "locator": str(chunk.get("locator", "")),
            "text": str(chunk["text"]),
            "token_overlap": overlap,
            "status": "REVIEW_LEAD_NOT_ANNOTATED",
        }
        for overlap, _, chunk in scored[:limit]
    ]


def _source_index(sources_dir: Path) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for path in sorted(sources_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                index[str(record["logical_id"])] = record
    return index


def build_retrieval_records(candidates: list[dict], chunks: list[dict], sources: dict[str, dict]) -> list[dict]:
    records: list[dict] = []
    for candidate in candidates:
        annotations = list(candidate.get("annotations") or [])
        annotated = {str(annotation["chunk_id"]) for annotation in annotations}
        passages = [str(annotation["text"]) for annotation in annotations]
        logical_ids = sorted({str(annotation["_logical_id"]) for annotation in annotations})

        sibling_leads: list[dict] = []
        for logical_id in logical_ids:
            sibling_leads.extend(
                {
                    "doc_id": str(chunk["doc_id"]),
                    "chunk_id": str(chunk["chunk_id"]),
                    "logical_id": str(chunk["logical_id"]),
                    "locator": str(chunk.get("locator", "")),
                    "text": str(chunk["text"]),
                    "status": "SIBLING_CHUNK_NOT_ANNOTATED",
                }
                for chunk in sibling_passages(logical_id, chunks, annotated)
            )
        leads = _dedupe_leads(sibling_leads + related_passages(candidate["question"], chunks, annotated))

        # A draft hard-negative target is intentionally related-but-wrong; it is
        # surfaced as a lead so the reviewer can confirm the exclusion.
        notes = str((candidate.get("annotation") or {}).get("notes", ""))
        hard_negative_chunk_id = _extract(notes, "hard_negative_chunk_id")
        query_family = _extract(notes, "query_family")
        correction = _extract_prose(notes, "ai_correction")
        considered_excluded = re.findall(r"considered_excluded=([^\s(]+)\s*\(([^)]*)\)", notes)

        records.append(
            {
                "workpackage_schema": WORKPACKAGE_SCHEMA,
                "sample_id": candidate["sample_id"],
                "question": candidate["question"],
                "business_type": candidate.get("business_type"),
                "difficulty": candidate.get("difficulty"),
                "complexity_label": candidate.get("complexity_label"),
                "corpus_version": candidate["corpus_version"],
                "corpus_sha256": candidate["corpus_sha256"],
                "review_status": "DRAFT_UNVERIFIED",
                "scorable": False,
                "official_sources": [
                    {
                        "logical_id": logical_id,
                        "source_document": str(sources.get(logical_id, {}).get("source_document", "")),
                        "source_url": str(sources.get(logical_id, {}).get("source_url", "")),
                        "locator": str(sources.get(logical_id, {}).get("locator", "")),
                        "license_basis": str(sources.get(logical_id, {}).get("license_basis", "")),
                    }
                    for logical_id in logical_ids
                ],
                "annotations": [
                    {
                        "doc_id": annotation["doc_id"],
                        "chunk_id": annotation["chunk_id"],
                        "logical_id": annotation.get("_logical_id", ""),
                        "locator": annotation.get("_locator", ""),
                        "text": annotation["text"],
                        "current_relevance": annotation.get("relevance"),
                    }
                    for annotation in annotations
                ],
                "derivation_risk": derivation_risk(candidate["question"], passages),
                "possibly_missing_passages": leads,
                "hard_negative_chunk_id": hard_negative_chunk_id,
                "query_family": query_family,
                "ai_correction": correction,
                "ai_suggestion": _extract(notes, "ai_suggestion"),
                "considered_excluded": [
                    {"logical_id": logical_id, "reason": reason.strip()} for logical_id, reason in considered_excluded
                ],
                "review": {
                    "decision": "",
                    "reviewer": "",
                    "reviewed_at": "",
                    "notes": "",
                    "relevance_changes": [],
                    "added_passages": [],
                    "removed_passages": [],
                    "confirmed_difficulty": "",
                    "confirmed_complexity_label": "",
                },
            }
        )
    return records


def _embedded_in_longer_name(text: str, term: str) -> bool:
    """True when every occurrence of ``term`` is part of a longer CJK name.

    ``甲醇`` (methanol) occurs inside ``苯甲醇`` (benzyl alcohol), which is a
    different substance. Treating that substring hit as a near-miss would tell a
    reviewer the corpus mentions the searched substance when it does not, so the
    substring case is recorded separately instead of being counted as evidence.
    """
    occurrences = []
    start = text.find(term)
    while start != -1:
        occurrences.append(start)
        start = text.find(term, start + 1)
    if not occurrences:
        return False
    for position in occurrences:
        before = text[position - 1] if position > 0 else ""
        after_index = position + len(term)
        after = text[after_index] if after_index < len(text) else ""
        if not (_CJK.match(before or " ") or _CJK.match(after or " ")):
            return False
    return True


def _extract(text: str, key: str) -> str:
    match = re.search(rf"{re.escape(key)}=([^;\s]+)", text)
    return match.group(1) if match else ""


def _extract_prose(text: str, key: str) -> str:
    """Read a free-form note value that may itself contain semicolons.

    ``_extract`` stops at the first ``;`` so it can read list-like values, but a
    correction sentence legitimately uses semicolons as clause separators. This
    reads until the next structured ``key=`` marker or the end of the note, so
    the full sentence survives.
    """
    match = re.search(rf"{re.escape(key)}=(.*?)(?=;\s*(?:considered_excluded|hard_negative_chunk_id)=|$)", text, re.S)
    return " ".join(match.group(1).split()) if match else ""


def _dedupe_leads(leads: list[dict]) -> list[dict]:
    """Keep one entry per chunk, preferring the stronger sibling/lexical label."""
    order = {"SIBLING_CHUNK_NOT_ANNOTATED": 0, "REVIEW_LEAD_NOT_ANNOTATED": 1}
    best: dict[str, dict] = {}
    for lead in leads:
        existing = best.get(lead["chunk_id"])
        if existing is None:
            best[lead["chunk_id"]] = lead
            continue
        if order.get(lead["status"], 9) < order.get(existing["status"], 9):
            best[lead["chunk_id"]] = lead
    return sorted(best.values(), key=lambda item: (order.get(item["status"], 9), item["chunk_id"]))


def build_answer_gate_records(no_answer: list[dict], chunks: list[dict], sources: dict[str, dict]) -> list[dict]:
    records: list[dict] = []
    for record in no_answer:
        substance_terms = sorted(
            {
                token
                for pair in record.get("searched_pairs", [])
                for token in pair
                if _CJK.search(str(token)) and len(str(token)) >= 2
            }
        )
        near_misses = []
        for chunk in chunks:
            matched = [term for term in substance_terms if term in str(chunk["text"])]
            if not matched:
                continue
            embedded = [term for term in matched if _embedded_in_longer_name(str(chunk["text"]), term)]
            near_misses.append(
                {
                    "doc_id": str(chunk["doc_id"]),
                    "chunk_id": str(chunk["chunk_id"]),
                    "logical_id": str(chunk["logical_id"]),
                    "locator": str(chunk.get("locator", "")),
                    "text": str(chunk["text"]),
                    "matched_terms": matched,
                    "embedded_substring_terms": embedded,
                    "why_not_an_answer": (
                        "contains a searched substance term but not the requested limit/value"
                        if not embedded
                        else "term only occurs inside a longer substance name; not evidence about the searched substance"
                    ),
                }
            )
        near_misses = near_misses[:5]
        records.append(
            {
                "workpackage_schema": ANSWER_GATE_SCHEMA,
                "sample_id": record["sample_id"],
                "question": record["question"],
                "business_type": record.get("business_type"),
                "difficulty": record.get("difficulty"),
                "corpus_version": record["corpus_version"],
                "corpus_sha256": record["corpus_sha256"],
                "evaluation_target": "answer_gate",
                "excluded_from_retrieval_recall": True,
                "review_status": "DRAFT_UNVERIFIED",
                "scorable": False,
                "searched_pairs": record.get("searched_pairs", []),
                "draft_matched_chunk_ids": record.get("matched_chunk_ids", []),
                "draft_evidence_of_absence": record.get("note", ""),
                "near_miss_passages": near_misses,
                "reviewer_questions": [
                    "Does any corpus passage answer this question in different wording?",
                    "Is the question outside this corpus's scope rather than unanswerable in general?",
                    "Should it stay as an answer-gate sample, be rewritten, or be rejected?",
                ],
                "review": {
                    "decision": "",
                    "reviewer": "",
                    "reviewed_at": "",
                    "notes": "",
                    "confirmed_unanswerable_from_this_corpus": "",
                },
            }
        )
    return records


#: Queries written as a user would ask, without copying a passage. They are not
#: in the dataset and must not be scored; a human decides whether any of them is
#: worth adding after the first review pass.
INDEPENDENT_QUERIES: tuple[dict, ...] = (
    {
        "proposal_id": "IQ-001",
        "question": "我买的进口面霜在国内卖，包装上必须有中文标签吗？",
        "user_intent": "consumer checking whether an imported cosmetic needs a Chinese label",
        "why_independent": "written from a consumer scenario, not from an article's wording",
        "human_must_decide": ["is it answerable from this corpus", "which passages support it", "difficulty"],
    },
    {
        "proposal_id": "IQ-002",
        "question": "普通化妆品备案之后，配方改了还能直接卖吗？",
        "user_intent": "small brand asking about change control after filing",
        "why_independent": "asks about a workflow consequence rather than quoting a clause",
        "human_must_decide": ["is it answerable from this corpus", "which passages support it", "difficulty"],
    },
    {
        "proposal_id": "IQ-003",
        "question": "成分表里写了烟酰胺，但没写浓度，这样合规吗？",
        "user_intent": "label-reading question about ingredient disclosure",
        "why_independent": "combines a real label observation with a compliance question",
        "human_must_decide": ["is it answerable from this corpus", "which passages support it", "difficulty"],
    },
    {
        "proposal_id": "IQ-004",
        "question": "原料目录里的最高历史使用量，和《化妆品安全技术规范》的限量是一回事吗？",
        "user_intent": "formulator distinguishing two different regulatory concepts",
        "why_independent": "written as a conceptual confusion, not as a quote lookup",
        "human_must_decide": ["is it answerable from this corpus", "which passages support it", "difficulty"],
    },
    {
        "proposal_id": "IQ-005",
        "question": "儿童化妆品在标签和原料上有没有额外的要求？",
        "user_intent": "parent or brand asking about children's cosmetics",
        "why_independent": "a category question a real user would ask without knowing the article number",
        "human_must_decide": ["is it answerable from this corpus", "which passages support it", "difficulty"],
    },
    {
        "proposal_id": "IQ-006",
        "question": "同一个原料既有中文名又有 INCI 名，标签上应该用哪个？",
        "user_intent": "packaging/labeling decision",
        "why_independent": "asks for a naming decision rather than restating the catalogue note",
        "human_must_decide": ["is it answerable from this corpus", "which passages support it", "difficulty"],
    },
)


def build_independent_records() -> list[dict]:
    return [
        {
            "proposal_schema": INDEPENDENT_SCHEMA,
            "proposal_id": item["proposal_id"],
            "question": item["question"],
            "user_intent": item["user_intent"],
            "why_independent": item["why_independent"],
            "origin": "ai_proposed_independent_user_query",
            "review_status": "PROPOSED_UNREVIEWED",
            "in_dataset": False,
            "scorable": False,
            "corpus_version": "",
            "corpus_sha256": "",
            "annotations": [],
            "human_must_decide": item["human_must_decide"],
            "review": {"decision": "", "reviewer": "", "reviewed_at": "", "notes": ""},
        }
        for item in INDEPENDENT_QUERIES
    ]


def _risk_counts(records: list[dict]) -> dict[str, int]:
    return dict(Counter(record["derivation_risk"]["level"] for record in records))


def render_retrieval_markdown(records: list[dict]) -> str:
    risks = _risk_counts(records)
    lines = [
        "# Real Corpus V1 — Retrieval candidate review work package",
        "",
        "Generated by `scripts/validation/build_real_golden_set_v1_workpackage.py`.",
        "",
        "> **This is not ground truth.** All 27 rows are `DRAFT_UNVERIFIED` and `scorable: false`.",
        "> Decision, reviewer, date and notes are blank on purpose. No label changes until a named",
        "> person fills them in and promotes the record through `benchmarks/annotation.py`.",
        "",
        "## Corpus binding",
        "",
        f"- `corpus_version`: `{records[0]['corpus_version'] if records else ''}`",
        f"- `corpus_sha256`: `{records[0]['corpus_sha256'] if records else ''}`",
        "- The hash is the Elasticsearch content fingerprint. It covers `(doc_id, content)`, so it",
        "  stays valid even if the index is later rebuilt with corrected provenance.",
        "",
        "## How to use this worksheet",
        "",
        "For each row:",
        "",
        "1. Open the official source at the recorded URL and locate the passage by `locator`.",
        "2. Compare the passage in `annotations` with the official text. Confirm `doc_id` / `chunk_id`.",
        "3. Decide whether each annotation is `2` (highly relevant), `1` (partially) or `0` (considered, excluded).",
        "4. Read `possibly_missing_passages`. Add any passage that genuinely supports the question, and",
        "   record why a lead was not added. These are review leads, never automatic additions.",
        "5. Re-confirm `difficulty` and `complexity_label` yourself.",
        "6. Record `ACCEPT` / `MODIFY` / `REJECT` plus your name, ISO date and notes.",
        "",
        "A `REJECT` is a valid outcome. So is `MODIFY` with different passages.",
        "",
        "## Question-derivation risk (why this check exists)",
        "",
        "The 27 questions were drafted **from the corpus**. A question that reuses the passage's",
        "wording can inflate retrieval scores for a reason that has nothing to do with retrieval",
        "quality. The table below reports a lexical heuristic, not a verdict:",
        "",
        "| risk | rows | meaning |",
        "|---|---:|---|",
        f"| `HIGH` | {risks.get('HIGH', 0)} | long shared run or almost all question bigrams occur in the passage |",
        f"| `MEDIUM` | {risks.get('MEDIUM', 0)} | partial overlap; read the question again as a user would ask it |",
        f"| `LOW` | {risks.get('LOW', 0)} | little lexical overlap; the question is phrased more independently |",
        "",
        "`HIGH` is not automatically a defect. It means: rewrite the question in user language before",
        "accepting it, or record why the exact wording is realistic.",
        "",
        "## Sign-off table",
        "",
        "Everything left of the divider was produced automatically and is reproducible from the",
        "generator. Everything right of it is **not filled in**: no human has reviewed these rows.",
        "",
        "| sample_id | gold chunks (relevance) | risk | difficulty / complexity | AI suggestion |",
        "|---|---|---|---|---|",
    ]
    for record in records:
        gold = ", ".join(
            f"`{annotation['logical_id'].split('/')[-1]}` = **{annotation['current_relevance']}**"
            for annotation in record["annotations"]
        )
        family = f" (family: `{record['query_family']}`)" if record.get("query_family") else ""
        lines.append(
            f"| `{record['sample_id']}`{family} | {gold} | {record['derivation_risk']['level']} | "
            f"{record['difficulty']} / {record['complexity_label']} | **{record.get('ai_suggestion') or 'ACCEPT'}** |"
        )
    lines += [
        "",
        "| sample_id | human decision | reviewer | reviewed_at | notes |",
        "|---|---|---|---|---|",
    ]
    for record in records:
        lines.append(f"| `{record['sample_id']}` | ☐ ACCEPT ☐ MODIFY ☐ REJECT |  |  |  |")

    lines += [
        "",
        "## Review table",
        "",
        "| sample_id | question | risk | difficulty | complexity | decisions |",
        "|---|---|---|---|---|---|",
    ]
    for record in records:
        lines.append(
            f"| `{record['sample_id']}` | {record['question']} | {record['derivation_risk']['level']} | "
            f"{record['difficulty']} | {record['complexity_label']} | ☐ ACCEPT ☐ MODIFY ☐ REJECT |"
        )

    lines += ["", "---", ""]
    for record in records:
        lines += _render_retrieval_entry(record)
    return "\n".join(lines) + "\n"


def _render_retrieval_entry(record: dict) -> list[str]:
    risk = record["derivation_risk"]
    lines = [
        f"## {record['sample_id']}",
        "",
        f"**Question:** {record['question']}",
        "",
        "| field | value |",
        "|---|---|",
        f"| `sample_id` | `{record['sample_id']}` |",
        f"| `business_type` | `{record['business_type']}` |",
        f"| `difficulty` (draft) | `{record['difficulty']}` |",
        f"| `complexity_label` (draft) | `{record['complexity_label']}` |",
        f"| derivation risk | **{risk['level']}** "
        f"(LCS {risk['longest_common_substring_chars']} chars, "
        f"ratio {risk['lcs_ratio_of_question']}, bigram coverage {risk['question_bigram_coverage']}) |",
        "| current `review_status` | `DRAFT_UNVERIFIED` |",
        "",
    ]
    if record.get("query_family"):
        lines += [
            f"> **Paraphrase-robustness probe.** This is the deliberate variant of "
            f"`{record['query_family']}`: same gold passage, different phrasing and register. "
            "It is not a duplicate and is not scored as one.",
            "",
        ]
    lines += [
        "**AI recommendation (not a human decision)**",
        "",
        "| field | value |",
        "|---|---|",
        f"| AI suggestion | **{record.get('ai_suggestion') or 'ACCEPT'}** |",
        f"| AI correction this round | {record.get('ai_correction') or '_none; record carried over unchanged_'}" + " |",
        "",
    ]
    if record.get("considered_excluded"):
        lines += ["**Considered and excluded by the AI pass**", ""]
        for excluded in record["considered_excluded"]:
            lines.append(f"- `{excluded['logical_id']}` — {excluded['reason']}")
        lines.append("")
    lines += [
        "**Official source and locator**",
        "",
        "| logical_id | document | locator | URL |",
        "|---|---|---|---|",
    ]
    for source in record["official_sources"]:
        lines.append(
            f"| `{source['logical_id']}` | {source['source_document']} | {source['locator']} | {source['source_url']} |"
        )
    lines += ["", "**Annotated passages (verbatim from the corpus)**", ""]
    for annotation in record["annotations"]:
        lines += [
            f"- `doc_id`: `{annotation['doc_id']}`",
            f"  `chunk_id`: `{annotation['chunk_id']}`",
            f"  `locator`: `{annotation['locator']}`",
            f"  current relevance: **{annotation['current_relevance']}**",
            "",
            "```text",
            annotation["text"],
            "```",
            "",
        ]
    lines += [
        "**Possibly missing / related passages** (review leads, not added automatically)",
        "",
    ]
    if record["possibly_missing_passages"]:
        for lead in record["possibly_missing_passages"]:
            lines += [
                f"- `{lead['status']}` — `{lead['logical_id']}` / `{lead['chunk_id']}`"
                + (f" (token overlap {lead['token_overlap']})" if "token_overlap" in lead else ""),
                "",
                "```text",
                lead["text"],
                "```",
                "",
            ]
    else:
        lines += ["_None found by the sibling/lexical scan. This does not prove none exists._", ""]
    if record["hard_negative_chunk_id"]:
        lines += [
            f"**Recorded hard-negative target:** `{record['hard_negative_chunk_id']}`. "
            "Confirm it is similar but non-supporting; it must not enter `annotations`.",
            "",
        ]
    lines += [
        "**Decision (fill by hand)**",
        "",
        "| field | value |",
        "|---|---|",
        "| decision (`ACCEPT` / `MODIFY` / `REJECT`) |  |",
        "| reviewer |  |",
        "| reviewed_at (ISO date) |  |",
        "| relevance changes |  |",
        "| added passages |  |",
        "| removed passages |  |",
        "| confirmed difficulty |  |",
        "| confirmed complexity_label |  |",
        "| review notes |  |",
        "",
        "---",
        "",
    ]
    return lines


def render_answer_gate_markdown(records: list[dict]) -> str:
    lines = [
        "# Real Corpus V1 — Answer Gate review (3 candidates)",
        "",
        "> **These samples are not retrieval ground truth and are excluded from the Retrieval Recall",
        "> denominator.** They exist to test whether the system refuses when this corpus cannot",
        "> support an answer. All three remain `DRAFT_UNVERIFIED` and `scorable: false`.",
        "",
        "## Why keyword absence is not proof of unanswerability",
        "",
        "The draft flagged these questions because no chunk contained the searched token pairs.",
        "That is evidence of *lexical* absence only. A corpus can answer a question without using",
        "the same words (for example, a passage may state a rule that implies the answer), and a",
        "question can be unanswerable for reasons unrelated to the corpus. **A human must judge",
        "semantic answerability**; the `near_miss_passages` below are there to make that judgement",
        "possible rather than to pre-decide it.",
        "",
        "## Review table",
        "",
        "| sample_id | question | draft matched chunks | decision |",
        "|---|---|---:|---|",
    ]
    for record in records:
        lines.append(
            f"| `{record['sample_id']}` | {record['question']} | "
            f"{len(record['draft_matched_chunk_ids'])} | ☐ KEEP ☐ REWRITE ☐ REJECT |"
        )
    lines += ["", "---", ""]
    for record in records:
        lines += [
            f"## {record['sample_id']}",
            "",
            f"**Question:** {record['question']}",
            "",
            f"- `evaluation_target`: `{record['evaluation_target']}`",
            f"- excluded from Retrieval Recall: **{record['excluded_from_retrieval_recall']}**",
            f"- `corpus_version`: `{record['corpus_version']}`",
            f"- `corpus_sha256`: `{record['corpus_sha256']}`",
            f"- draft `matched_chunk_ids`: `{record['draft_matched_chunk_ids']}`",
            f"- searched pairs: `{record['searched_pairs']}`",
            "",
            "**Near-miss passages (contain a searched substance term, not the requested value)**",
            "",
        ]
        if record["near_miss_passages"]:
            for passage in record["near_miss_passages"]:
                lines += [
                    f"- `{passage['logical_id']}` / `{passage['chunk_id']}` — {passage['why_not_an_answer']}",
                    "",
                    "```text",
                    passage["text"],
                    "```",
                    "",
                ]
        else:
            lines += ["_No near-miss passage contains even the substance term._", ""]
        lines += [
            "**Reviewer must answer**",
            "",
        ]
        lines += [f"{index}. {question}" for index, question in enumerate(record["reviewer_questions"], start=1)]
        lines += [
            "",
            "**Decision (fill by hand)**",
            "",
            "| field | value |",
            "|---|---|",
            "| decision (`KEEP` / `REWRITE` / `REJECT`) |  |",
            "| confirmed unanswerable from this corpus (`YES` / `NO`) |  |",
            "| reviewer |  |",
            "| reviewed_at (ISO date) |  |",
            "| review notes |  |",
            "",
            "---",
            "",
        ]
    return "\n".join(lines) + "\n"


def render_independent_markdown(records: list[dict]) -> str:
    lines = [
        "# Independent user-query proposals (not in the dataset)",
        "",
        "> These are **proposals**, not samples. They are not in `candidates_v1.jsonl`, carry no",
        "> `doc_id`/`chunk_id`, are not scorable, and must not be counted in any metric. A human",
        "> decides whether any of them is worth adding as a future, independently written query.",
        "",
        "Why they exist: all 27 current questions were drafted from the corpus. A small number of",
        "queries written from user intent, before looking at a passage, is the cheapest check",
        "against a benchmark that only measures how well the retriever finds text it already",
        "quoted.",
        "",
        "| proposal_id | question |",
        "|---|---|",
    ]
    for record in records:
        lines.append(f"| `{record['proposal_id']}` | {record['question']} |")
    lines += ["", "---", ""]
    for record in records:
        lines += [
            f"## {record['proposal_id']}",
            "",
            f"**Question:** {record['question']}",
            "",
            f"- user intent: {record['user_intent']}",
            f"- why independent: {record['why_independent']}",
            f"- `review_status`: `{record['review_status']}`",
            f"- `in_dataset`: **{record['in_dataset']}**",
            "- a human must decide: " + "; ".join(record["human_must_decide"]),
            "",
            "| decision (`ADOPT_LATER` / `REJECT`) | reviewer | reviewed_at | notes |",
            "|---|---|---|---|",
            "|  |  |  |  |",
            "",
            "---",
            "",
        ]
    return "\n".join(lines) + "\n"


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def assert_no_human_annotations(path: Path) -> None:
    """Refuse to regenerate a worksheet a real person has already worked through.

    This script rebuilds the worksheet with blank ``review`` fields every time it
    runs. That is correct while the worksheet is unsigned, but once a reviewer
    has written a decision, a reviewer name and a date into it, regenerating
    would silently destroy the only record that a human decision was ever made.

    The candidate generator already refuses this overwrite
    (``build_real_golden_set_v1.assert_no_human_annotations``). The worksheet
    needs the same guard, because it is where a reviewer actually types.

    A worksheet carrying AI pre-review text (``ai_suggestion`` / ``ai_correction``)
    is *not* a human record and is regenerated freely; only a filled-in ``review``
    block stops the rebuild.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        review = record.get("review") or {}
        if not isinstance(review, Mapping):
            continue
        filled = sorted(
            field
            for field in (
                "decision",
                "reviewer",
                "reviewed_at",
                "notes",
                "relevance_changes",
                "added_passages",
                "removed_passages",
                "confirmed_difficulty",
                "confirmed_complexity_label",
            )
            if review.get(field)
        )
        if filled:
            raise SystemExit(
                f"refusing to overwrite {path}: record {record.get('sample_id')!r} already carries a human "
                f"review decision ({', '.join(filled)}); export it to the review-decision ledger with "
                "scripts/validation/import_real_v1_review_decisions.py before regenerating"
            )


def assert_no_human_annotations_all(out_dir: Path) -> None:
    for name in (RETRIEVAL_FILE, ANSWER_FILE, INDEPENDENT_FILE):
        assert_no_human_annotations(out_dir / name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidates", default="tests/evaluation/golden_set_v1/candidates_v1.jsonl")
    parser.add_argument("--no-answer", default="tests/evaluation/golden_set_v1/no_answer_candidates_v1.jsonl")
    parser.add_argument("--chunks", default="corpus/real_v1/chunks.jsonl")
    parser.add_argument("--sources-dir", default="corpus/real_v1/sources")
    parser.add_argument("--out-dir", default="tests/evaluation/golden_set_v1")
    args = parser.parse_args(argv)

    candidates = [
        json.loads(line) for line in Path(args.candidates).read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    no_answer = [
        json.loads(line) for line in Path(args.no_answer).read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    chunks = [json.loads(line) for line in Path(args.chunks).read_text(encoding="utf-8").splitlines() if line.strip()]
    sources = _source_index(Path(args.sources_dir))

    retrieval = build_retrieval_records(candidates, chunks, sources)
    answer_gate = build_answer_gate_records(no_answer, chunks, sources)
    independent = build_independent_records()

    out_dir = Path(args.out_dir)
    assert_no_human_annotations_all(out_dir)
    write_jsonl(out_dir / RETRIEVAL_FILE, retrieval)
    write_jsonl(out_dir / ANSWER_FILE, answer_gate)
    write_jsonl(out_dir / INDEPENDENT_FILE, independent)
    (out_dir / RETRIEVAL_MD).write_text(render_retrieval_markdown(retrieval), encoding="utf-8")
    (out_dir / ANSWER_MD).write_text(render_answer_gate_markdown(answer_gate), encoding="utf-8")
    (out_dir / INDEPENDENT_MD).write_text(render_independent_markdown(independent), encoding="utf-8")

    risks = _risk_counts(retrieval)
    print(f"retrieval candidates: {len(retrieval)} (draft, 0 scorable)")
    print(f"derivation risk: {risks}")
    print(f"answer-gate candidates: {len(answer_gate)} (excluded from retrieval recall)")
    print(f"independent proposals: {len(independent)} (not in dataset)")
    print(f"wrote: {out_dir / RETRIEVAL_FILE}")
    print(f"wrote: {out_dir / ANSWER_FILE}")
    print(f"wrote: {out_dir / INDEPENDENT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
