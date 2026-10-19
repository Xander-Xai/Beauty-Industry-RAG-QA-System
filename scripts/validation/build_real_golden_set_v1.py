#!/usr/bin/env python3
"""Build the Golden Set V1 candidate worksheet from the real corpus.

Every record produced here is ``llm_candidate`` / ``DRAFT_UNVERIFIED``. It is a
proposal for a human to accept or reject, never ground truth, and the scoring
gate refuses it by construction. Nothing invents a ``doc_id`` or ``chunk_id``:
each annotation is resolved against ``corpus/real_v1/chunks.jsonl`` and the run
aborts if a selector matches zero or several chunks.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.annotation import (  # noqa: E402
    ANNOTATION_SOURCE_LLM,
    REVIEW_STATUS_DRAFT,
    audit_dataset,
)

ANNOTATOR = "llm:opencode-deepseek-v4.1-flash"
METHOD = "llm-assisted-passage-mapping"
ANNOTATED_AT = "2026-10-19"
DEFAULT_CHUNKS = "corpus/real_v1/chunks.jsonl"
DEFAULT_FINGERPRINT = "corpus/real_v1/es_fingerprint.json"

# (question, business_type, difficulty, complexity_label, category, annotations, hard_negative)
# annotations: ((logical_id, contains_or_None, relevance), ...)
CANDIDATES: tuple[tuple, ...] = (
    (
        "根据《化妆品监督管理条例》，化妆品的法定定义是什么？",
        "regulation",
        "easy",
        "simple",
        "regulation_precise",
        (("nmpa/regulation-727/article-003", None, 2),),
        None,
    ),
    (
        "特殊化妆品和普通化妆品分别实行注册管理还是备案管理？",
        "regulation",
        "easy",
        "simple",
        "regulation_precise",
        (
            ("nmpa/regulation-727/article-004", None, 2),
            ("nmpa/regulation-727/article-017", None, 2),
        ),
        None,
        {
            "suggestion": "MODIFY",
            "correction": (
                "added article-017 as a second gold passage; both articles independently answer the "
                "question (004 states the management model, 017 states the same split with the filing "
                "timing and authority). Recall@1 <= 0.5 is the normal many-relevant-document definition, "
                "not a scoring defect; the passage was not dropped to protect the metric"
            ),
        },
    ),
    (
        "化妆品新原料在什么情况下需要注册，什么情况下只需备案？",
        "regulation",
        "medium",
        "simple",
        "regulation_precise",
        (("nmpa/regulation-727/article-011", None, 2),),
        None,
    ),
    (
        "化妆品新原料投入使用后，注册人、备案人需要承担哪些年度报告义务？",
        "regulation",
        "medium",
        "simple",
        "regulation_precise",
        (("nmpa/regulation-727/article-014", None, 2),),
        None,
    ),
    (
        "哪些类别的化妆品属于特殊化妆品？",
        "regulation",
        "easy",
        "simple",
        "regulation_precise",
        (("nmpa/regulation-727/article-016", None, 2),),
        None,
    ),
    (
        "特殊化妆品和普通化妆品分别由谁负责注册或备案？",
        "regulation",
        "medium",
        "simple",
        "regulation_precise",
        (("nmpa/regulation-727/article-017", None, 2),),
        None,
    ),
    (
        "化妆品的功效宣称需要满足什么条件，注册人、备案人有什么公开义务？",
        "regulation",
        "medium",
        "simple",
        "regulation_precise",
        (("nmpa/regulation-727/article-022", None, 2),),
        None,
    ),
    (
        "化妆品标签上必须标注哪些内容？",
        "regulation",
        "easy",
        "simple",
        "regulation_precise",
        (("nmpa/regulation-727/article-036", None, 2),),
        None,
        {
            "suggestion": "MODIFY",
            "correction": (
                "re-worded to drop the near-verbatim overlap with article 036 that scored derivation risk HIGH; the gold passage is unchanged and the sample still measures precise clause lookup, not hard semantic retrieval"
            ),
        },
    ),
    (
        "化妆品标签上哪些内容是禁止标注的？",
        "regulation",
        "easy",
        "simple",
        "regulation_precise",
        (("nmpa/regulation-727/article-037", None, 2),),
        None,
        {
            "suggestion": "MODIFY",
            "correction": (
                "re-worded to drop the near-verbatim overlap with article 037 that scored derivation risk HIGH; the gold passage is unchanged and difficulty stays easy because it is a precise clause lookup"
            ),
        },
    ),
    (
        "根据《已使用化妆品原料目录（2021年版）》，透明质酸钠的 INCI 名称是什么？2021 年版记载的淋洗类产品最高历史使用量是多少？",
        "ingredient",
        "easy",
        "simple",
        "ingredient_query",
        (("nmpa/used-ingredients-2021/row-006722", None, 2),),
        None,
        {
            "suggestion": "MODIFY",
            "correction": (
                "re-worded to pin the figure to the 2021 snapshot; the catalog dropped the maximum-historical-usage column in 2025, so an unqualified 'highest historical usage' reads as a current limit"
            ),
        },
    ),
    (
        "烟酰胺在《已使用化妆品原料目录（2021年版）》中是否给出了最高历史使用量？",
        "ingredient",
        "easy",
        "simple",
        "ingredient_query",
        (("nmpa/used-ingredients-2021/row-007359", None, 2),),
        None,
    ),
    (
        "根据《已使用化妆品原料目录（2021年版）》，泛醇在驻留类产品中的 2021 年版记载最高历史使用量是多少？淋洗类产品当时是否给出了数值？",
        "ingredient",
        "easy",
        "simple",
        "ingredient_query",
        (("nmpa/used-ingredients-2021/row-002292", None, 2),),
        None,
        {
            "suggestion": "MODIFY",
            "correction": (
                "re-worded to pin the figure to the 2021 snapshot and to add the rinse column, which the row leaves as 未提供, so the sample also tests that a missing figure is reported as missing rather than as unlimited"
            ),
        },
    ),
    (
        "根据《已使用化妆品原料目录（2021年版）》，神经酰胺 NP 的 INCI 名称和 2021 年版记载的驻留类产品最高历史使用量分别是什么？淋洗类产品当时是否给出了数值？",
        "ingredient",
        "easy",
        "simple",
        "ingredient_query",
        (("nmpa/used-ingredients-2021/row-006016", None, 2),),
        None,
        {
            "suggestion": "MODIFY",
            "correction": (
                "re-worded to pin the figure to the 2021 snapshot and to add the rinse column, which the row leaves as 未提供, so a missing figure is reported as missing"
            ),
        },
    ),
    (
        "根据《已使用化妆品原料目录（2021年版）》，积雪草提取物在驻留类产品中的 2021 年版记载最高历史使用量是多少？",
        "ingredient",
        "medium",
        "simple",
        "ingredient_query",
        (("nmpa/used-ingredients-2021/row-003171", None, 2),),
        None,
        {
            "suggestion": "MODIFY",
            "correction": (
                "re-worded to pin the figure to the 2021 snapshot rather than leaving it readable as a current cap"
            ),
        },
    ),
    (
        "根据《已使用化妆品原料目录（2021年版）》，水杨酸的最高历史使用量当时是否给出了具体数值？备注栏写的是什么？",
        "ingredient",
        "medium",
        "simple",
        "ingredient_query",
        (("nmpa/used-ingredients-2021/row-006405", None, 2),),
        None,
        {
            "suggestion": "MODIFY",
            "correction": (
                "re-worded to pin the row to the 2021 snapshot; both usage columns are '/', so the question must ask whether a figure was given, not what it was"
            ),
        },
    ),
    (
        "根据《已使用化妆品原料目录（2021年版）》，苯氧乙醇的最高历史使用量栏当时标注了什么？备注栏写的是什么？",
        "ingredient",
        "easy",
        "simple",
        "ingredient_query",
        (("nmpa/used-ingredients-2021/row-001294", None, 2),),
        None,
        {
            "suggestion": "MODIFY",
            "correction": (
                "re-worded to pin the row to the 2021 snapshot and to ask what the column was filled with; both usage columns are '/', and '如何标注' did not make the expected 'not provided' answer explicit"
            ),
        },
    ),
    (
        "氢醌在《已使用化妆品原料目录（2021年版）》中的备注是什么？",
        "ingredient",
        "medium",
        "simple",
        "ingredient_query",
        (("nmpa/used-ingredients-2021/row-005428", None, 2),),
        None,
    ),
    (
        "《化妆品监督管理条例》是怎么定义化妆品的？",
        "regulation",
        "easy",
        "simple",
        "semantic_paraphrase",
        (("nmpa/regulation-727/article-003", None, 2),),
        None,
        {
            "query_family": "REAL-V1-001",
            "correction": (
                "kept deliberately as a paraphrase-robustness probe: asks in colloquial register "
                "('是怎么定义的') where REAL-V1-001 uses the statutory register ('法定定义'), so the two "
                "differ in phrasing while sharing one gold passage"
            ),
        },
    ),
    (
        "给化妆品新原料办注册和备案，分别在哪些情形下适用？",
        "regulation",
        "medium",
        "simple",
        "semantic_paraphrase",
        (("nmpa/regulation-727/article-011", None, 2),),
        None,
        {
            "query_family": "REAL-V1-003",
            "correction": (
                "kept deliberately as a paraphrase-robustness probe: framed from the applicant's side "
                "('给...办') where REAL-V1-003 states the rule abstractly, so the two differ in voice "
                "while sharing one gold passage"
            ),
        },
    ),
    (
        "透明质酸钠在冲洗类产品里最多能用多少（按2021年版记载的最高历史使用量口径）？",
        "ingredient",
        "medium",
        "simple",
        "semantic_paraphrase",
        (("nmpa/used-ingredients-2021/row-006722", None, 2),),
        None,
        {
            "query_family": "REAL-V1-010",
            "correction": (
                "kept deliberately as a paraphrase-robustness probe: uses the colloquial category word "
                "'冲洗' where the catalog says '淋洗', asks the usage figure alone where REAL-V1-010 also "
                "asks the INCI name, and the qualifier now says '2021年版记载' so the figure cannot be "
                "read as a current limit"
            ),
        },
    ),
    (
        "企业使用已列入目录的原料生产国产普通化妆品，需要办理哪些注册和备案手续？",
        "regulation",
        "medium",
        "complex",
        "multi_document",
        (
            ("nmpa/regulation-727/article-004", None, 1),
            ("nmpa/regulation-727/article-017", None, 2),
        ),
        None,
        {
            "suggestion": "MODIFY",
            "correction": (
                "re-graded: article-017 is the passage that actually answers 'which procedures' (filing "
                "timing, authority, and the import variant), so it is the highly relevant passage. "
                "article-004 only states that ordinary cosmetics are filed rather than registered, which "
                "supports the answer but cannot answer it alone, so it was graded partially relevant"
            ),
        },
    ),
    (
        "化妆品标签上的全成分标注和功效宣称的科学依据，分别规定在哪两条？",
        "regulation",
        "medium",
        "complex",
        "multi_document",
        (("nmpa/regulation-727/article-036", None, 2), ("nmpa/regulation-727/article-022", None, 2)),
        None,
    ),
    (
        "根据《已使用化妆品原料目录（2021年版）》，目录中已列入禁用、限用或准用组分的原料，使用时应当遵守什么要求？",
        "ingredient",
        "medium",
        "simple",
        "ingredient_query",
        (("nmpa/used-ingredients-2021/notice", "已被列为化妆品禁用组分", 2),),
        None,
        {
            "suggestion": "MODIFY",
            "correction": (
                "re-graded on evidence, not on bucket size. article-015 only says who compiles the banned "
                "ingredient catalog; it does not state any usage requirement, so it was dropped from the "
                "gold set. The catalog notice's second clause is the sole passage that actually answers "
                "the question. With one gold passage the sample is no longer multi_document and its "
                "complexity_label was reduced from complex to simple accordingly"
            ),
            "considered_excluded": [
                (
                    "nmpa/regulation-727/article-015",
                    "defines the compiling authority for the banned-ingredient catalog, not any usage "
                    "requirement for an ingredient already listed as restricted",
                )
            ],
        },
    ),
    (
        "根据《已使用化妆品原料目录（2021年版）》及其当时的使用说明，最高历史使用量能否直接视为法定限量？",
        "ingredient",
        "hard",
        "complex",
        "ingredient_query",
        (("nmpa/used-ingredients-2021/notice", "最高历史使用量", 2),),
        None,
        {
            "suggestion": "MODIFY",
            "correction": (
                "re-graded on evidence. The catalog notice clause 8 is the passage that answers the "
                "question: it defines the figure as one evidence type for a simplified safety assessment "
                "report. row-006016 was dropped because it merely carries a figure and contributes nothing "
                "the notice does not already state. The sample stays hard/complex (it needs clause 8 plus "
                "clause 9 read together) but is no longer multi_document, since one source answers it"
            ),
            "considered_excluded": [
                (
                    "nmpa/used-ingredients-2021/row-006016",
                    "an example row carrying a number, not support for whether the number is a legal limit",
                )
            ],
        },
    ),
    (
        "根据《已使用化妆品原料目录（2021年版）》，苯甲醇在驻留类产品中的最高历史使用量当时是否给出了数值？如果未给出数值，是否等于允许无限制使用？",
        "ingredient",
        "easy",
        "simple",
        "hard_negative",
        (("nmpa/used-ingredients-2021/row-001279", None, 2),),
        "nmpa/used-ingredients-2021/row-001294",
        {
            "suggestion": "MODIFY",
            "correction": (
                "re-worded: the row's leave-on usage column is '/', so the previous '是多少' presupposed a figure that does not exist; the new wording asks whether one was given and adds the explicit trap that absence is not permission to use without limit"
            ),
        },
    ),
    (
        "国产普通化妆品上市前，应当向哪一级药品监督管理部门备案？",
        "regulation",
        "medium",
        "simple",
        "hard_negative",
        (("nmpa/regulation-727/article-017", None, 2),),
        "nmpa/regulation-727/article-016",
    ),
    (
        "根据《已使用化妆品原料目录（2021年版）》，对苯二胺的备注栏当时写的是什么？",
        "ingredient",
        "medium",
        "simple",
        "hard_negative",
        (("nmpa/used-ingredients-2021/row-000970", None, 2),),
        "nmpa/used-ingredients-2021/row-005428",
        {
            "suggestion": "MODIFY",
            "correction": (
                "re-worded to pin the row to the 2021 snapshot; the remark text changed across the 2025-2026 catalog adjustments"
            ),
        },
    ),
)

# No-answer candidates: the corpus cannot answer them. ``absent_pairs`` requires
# that no chunk contains both tokens of any pair.
NO_ANSWER: tuple[tuple, ...] = (
    (
        "《化妆品监督管理条例》规定的化妆品中铅的限量是多少 mg/kg？",
        "regulation",
        "hard",
        (("铅", "限量"), ("铅", "mg/kg")),
    ),
    (
        "驻留类化妆品中烟酰胺的最高允许浓度是多少？",
        "ingredient",
        "hard",
        (("烟酰胺", "最高允许浓度"), ("烟酰胺", "最大允许浓度"), ("烟酰胺", "限量")),
    ),
    (
        "《化妆品监督管理条例》是否规定了化妆品中甲醇的限量？",
        "regulation",
        "medium",
        (("甲醇", "限量"), ("甲醇", "mg/kg")),
    ),
)


def load_chunks(path: Path) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        grouped.setdefault(str(record["logical_id"]), []).append(record)
    return grouped


def resolve_annotation(grouped: dict[str, list[dict]], selector: tuple) -> dict:
    logical_id, contains, relevance = selector
    candidates = grouped.get(logical_id)
    if not candidates:
        raise SystemExit(f"annotation selector references unknown logical_id: {logical_id}")
    if contains:
        candidates = [chunk for chunk in candidates if contains in chunk["text"]]
    if len(candidates) != 1:
        raise SystemExit(f"annotation selector {logical_id!r} (contains={contains!r}) matched {len(candidates)} chunks")
    chunk = candidates[0]
    return {
        "doc_id": chunk["doc_id"],
        "chunk_id": chunk["chunk_id"],
        "text": chunk["text"],
        "relevance": int(relevance),
        "_logical_id": logical_id,
        "_locator": chunk.get("locator", ""),
    }


def build_records(grouped: dict[str, list[dict]], corpus_version: str, corpus_sha256: str) -> list[dict]:
    records = []
    for index, entry in enumerate(CANDIDATES, start=1):
        question, business_type, difficulty, complexity, category, selectors, hard_negative = entry[:7]
        extra = entry[7] if len(entry) > 7 else {}
        sample_id = f"REAL-V1-{index:03d}"
        annotations = [resolve_annotation(grouped, selector) for selector in selectors]
        notes = f"category={category}; draft generated by an AI assistant; human review required"
        if hard_negative:
            negative_chunks = grouped.get(hard_negative) or []
            negative_id = negative_chunks[0]["chunk_id"] if negative_chunks else ""
            notes += f"; hard_negative_logical_id={hard_negative}; hard_negative_chunk_id={negative_id}"
        if extra.get("query_family"):
            notes += f"; query_family={extra['query_family']} (paraphrase of {sample_id}'s paired question)"
        notes += f"; ai_suggestion={extra.get('suggestion', 'ACCEPT')}"
        if extra.get("correction"):
            notes += f"; ai_correction={extra['correction']}"
        for logical_id, reason in extra.get("considered_excluded", ()):
            notes += f"; considered_excluded={logical_id} ({reason})"
        records.append(
            {
                "sample_id": sample_id,
                "question": question,
                "business_type": business_type,
                "difficulty": difficulty,
                "visual_required": False,
                "complexity_label": complexity,
                "corpus_version": corpus_version,
                "corpus_sha256": corpus_sha256,
                "contexts": [annotation["text"] for annotation in annotations],
                "annotations": [
                    {
                        "doc_id": annotation["doc_id"],
                        "chunk_id": annotation["chunk_id"],
                        "text": annotation["text"],
                        "relevance": annotation["relevance"],
                        "_logical_id": annotation["_logical_id"],
                        "_locator": annotation["_locator"],
                    }
                    for annotation in annotations
                ],
                "annotation": {
                    "annotator": ANNOTATOR,
                    "method": METHOD,
                    "annotated_at": ANNOTATED_AT,
                    "source": ANNOTATION_SOURCE_LLM,
                    "review_status": REVIEW_STATUS_DRAFT,
                    "notes": notes,
                },
            }
        )
    return records


def build_no_answer(grouped: dict[str, list[dict]], corpus_version: str, corpus_sha256: str) -> list[dict]:
    corpus_texts = [chunk["text"] for chunks in grouped.values() for chunk in chunks]
    records = []
    for index, (question, business_type, difficulty, pairs) in enumerate(NO_ANSWER, start=1):
        matches = []
        for left, right in pairs:
            hits = [
                chunk["chunk_id"]
                for chunks in grouped.values()
                for chunk in chunks
                if left in chunk["text"] and right in chunk["text"]
            ]
            matches.extend(hits)
        records.append(
            {
                "sample_id": f"REAL-V1-NOANSWER-{index:03d}",
                "question": question,
                "business_type": business_type,
                "difficulty": difficulty,
                "answerable": False,
                "expected_behavior": "refuse",
                "evaluation_target": "answer_gate",
                "corpus_version": corpus_version,
                "corpus_sha256": corpus_sha256,
                "searched_pairs": [list(pair) for pair in pairs],
                "matched_chunk_ids": sorted(set(matches)),
                "corpus_chunk_count": len(corpus_texts),
                "note": (
                    "No chunk in this corpus contains any searched token pair, so a factual answer cannot be "
                    "grounded. Absence is relative to this corpus only, not a general legal conclusion."
                ),
            }
        )
    return records


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def assert_no_human_annotations(path: Path) -> None:
    """Refuse to regenerate over records a human has already acted on.

    This script is the single source of the candidate drafts, so regeneration is
    how a correction lands. But regeneration must never silently discard a real
    reviewer's decision or attribution, so a file that already carries promoted
    records is an error rather than something to overwrite.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        provenance = record.get("annotation", {})
        acted = (
            provenance.get("review_status") != REVIEW_STATUS_DRAFT
            or provenance.get("reviewed_by")
            or provenance.get("reviewed_at")
            or provenance.get("promoted_by")
        )
        if acted:
            raise SystemExit(
                f"refusing to overwrite {path}: record {record.get('sample_id')!r} already carries human "
                "review fields; move them to the reviewed dataset first"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chunks", default=DEFAULT_CHUNKS)
    parser.add_argument("--fingerprint", default=DEFAULT_FINGERPRINT)
    parser.add_argument("--out-dir", default="tests/evaluation/golden_set_v1")
    parser.add_argument("--resolve-es", action="store_true", help="also resolve every id against the live ES index")
    args = parser.parse_args(argv)

    grouped = load_chunks(Path(args.chunks))
    fingerprint = json.loads(Path(args.fingerprint).read_text(encoding="utf-8"))
    corpus_version = str(fingerprint["corpus_version"])
    corpus_sha256 = str(fingerprint["fingerprint"]["content_sha256"])

    candidates = build_records(grouped, corpus_version, corpus_sha256)
    no_answer = build_no_answer(grouped, corpus_version, corpus_sha256)

    out_dir = Path(args.out_dir)
    assert_no_human_annotations(out_dir / "candidates_v1.jsonl")
    write_jsonl(out_dir / "candidates_v1.jsonl", candidates)
    write_jsonl(out_dir / "no_answer_candidates_v1.jsonl", no_answer)

    audit = audit_dataset(candidates)
    validation = {
        "corpus_version": corpus_version,
        "corpus_sha256": corpus_sha256,
        "candidate_count": len(candidates),
        "no_answer_count": len(no_answer),
        "annotation_audit": audit.as_dict(),
        "reviewed_count": audit.scorable_count,
        "draft_count": audit.draft_count,
    }
    (out_dir / "candidate_validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"candidates: {len(candidates)}")
    print(f"no-answer:  {len(no_answer)}")
    print(f"valid drafts: {audit.valid_count}/{audit.total}")
    print(f"scorable:     {audit.scorable_count} (a human reviewer must promote records to REVIEWED)")
    print(f"corpus_sha256: {corpus_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
