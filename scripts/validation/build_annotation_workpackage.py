#!/usr/bin/env python3
"""Build a human annotation work package from the committed golden set.

Why this exists
---------------
The v2 annotation dataset (`tests/evaluation/golden_set_v2/annotations_v1.jsonl`)
is empty on purpose: filling it is a human task. What a human needs to *start* is
a scoped, category-balanced worksheet that names the exact samples to annotate,
carries the candidate evidence already present in the golden set, and states
plainly which fields only a person can supply.

This tool produces that worksheet. It is **not** an annotation dataset and it is
**not** ground truth:

* every row is ``candidate`` / ``DRAFT_UNVERIFIED`` — it is refused by every
  scoring gate by construction;
* it fabricates **no** ``doc_id`` / ``chunk_id`` / ``corpus_version`` /
  ``corpus_sha256`` / ``visual_required`` / ``complexity_label`` — those are
  listed in ``to_fill`` and left absent;
* it names **no** annotator and **no** reviewer and stamps **no** date;
* it marks itself ``scorable: false`` with explicit ``blocking_reasons``.

The categories map to the retrieval behaviours a reviewer must exercise:
regulation-precise, ingredient, product, semantic-paraphrase, multi-document,
image-related, and no-answer/conflict. The last is called out explicitly when the
source set contains no such sample, because a benchmark cannot prove it handles
unanswerable questions by testing only answerable ones.

Usage
-----
    python3 scripts/validation/build_annotation_workpackage.py \
        --dataset tests/evaluation/golden_set.jsonl \
        --out tests/evaluation/golden_set_v2/annotation_workpackage_v1.jsonl \
        --markdown tests/evaluation/golden_set_v2/ANNOTATION_WORKPACKAGE.md

Exit codes
----------
    0  work package written
    2  the source dataset could not be read
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.dataset import DatasetError, load_rows  # noqa: E402

EXIT_OK = 0
EXIT_DATASET_ERROR = 2

WORKPACKAGE_SCHEMA_VERSION = "annotation-workpackage/v1"

#: Paraphrase prefixes observed in the committed set. A row whose question starts
#: with one of these is a rewording of an earlier base question, which is exactly
#: the semantic-rewrite bucket the benchmark must exercise.
_PARAPHRASE_PREFIXES = (
    "请问",
    "请简要说明",
    "我想知道",
    "根据化妆品相关规定",
    "给我介绍一下",
    "能否告诉我",
)

#: The categories a reviewer must cover, in the order they are reported.
CATEGORIES = (
    "regulation_precise",
    "ingredient_query",
    "product_knowledge",
    "semantic_paraphrase",
    "multi_document",
    "image_related",
    "no_answer_or_conflict",
)

#: Samples per category. Sums to 70, inside the 50-80 target. ``product_knowledge``
#: takes every available sample (the committed set has only four); the rest are
#: quotas. ``no_answer_or_conflict`` has no source samples and is reported as
#: needing human authorship.
DEFAULT_QUOTA = {
    "regulation_precise": 15,
    "ingredient_query": 15,
    "product_knowledge": 4,
    "semantic_paraphrase": 12,
    "multi_document": 12,
    "image_related": 12,
    "no_answer_or_conflict": 0,
}


def _is_paraphrase(question: str) -> bool:
    return any(question.startswith(prefix) for prefix in _PARAPHRASE_PREFIXES)


def primary_category(row: dict) -> str | None:
    """Priority-ordered primary category. Never returns a fabricated label.

    The order is deliberate: a visual dependency outranks business type, business
    type outranks the paraphrase/multi-passage shape, so a sample is counted once
    under the behaviour that most constrains its annotation.
    """
    business_type = str(row.get("business_type") or "")
    question = str(row.get("question") or "")
    contexts = row.get("contexts") or []

    if business_type == "image":
        return "image_related"
    if business_type == "product":
        return "product_knowledge"
    if business_type == "regulation":
        return "regulation_precise"
    if business_type == "ingredient":
        return "ingredient_query"
    if _is_paraphrase(question):
        return "semantic_paraphrase"
    if len(contexts) >= 3:
        return "multi_document"
    return None


def build_records(rows: Sequence[dict], quota: dict[str, int] | None = None) -> list[dict]:
    """Select a category-balanced subset and build worksheet records.

    Deterministic: rows are visited in file order and each category takes the
    first ``quota`` samples assigned to it, so re-running on an unchanged dataset
    yields an identical package.
    """
    quota = quota or DEFAULT_QUOTA
    selected: dict[str, list[tuple[int, dict]]] = {name: [] for name in CATEGORIES}

    for index, row in enumerate(rows):
        category = primary_category(row)
        if category is None:
            continue
        bucket = selected[category]
        if len(bucket) < quota.get(category, 0):
            bucket.append((index, row))

    records: list[dict] = []
    for category in CATEGORIES:
        for index, row in selected[category]:
            sample_id = f"{index:04d}"
            records.append(
                {
                    "workpackage_schema": WORKPACKAGE_SCHEMA_VERSION,
                    "workpackage_id": f"WP-{len(records) + 1:04d}",
                    "sample_id": sample_id,
                    "category": category,
                    "question": str(row.get("question") or ""),
                    "business_type": row.get("business_type"),
                    "difficulty": row.get("difficulty"),
                    # Verbatim from the source set; candidate evidence, not
                    # validated ground truth. A reviewer decides whether it stays.
                    "candidate_contexts": list(row.get("contexts") or []),
                    "source_ground_truth": str(row.get("ground_truth") or ""),
                    "candidate": {
                        "source": "llm_candidate",
                        "review_status": "DRAFT_UNVERIFIED",
                        "annotator": "",
                        "method": "manual-passage-mapping",
                        "annotated_at": "",
                        "reviewed_by": "",
                        "reviewed_at": "",
                    },
                    "to_fill": [
                        "corpus_version",
                        "corpus_sha256",
                        "annotations[].doc_id",
                        "annotations[].chunk_id",
                        "annotations[].relevance",
                        "visual_required",
                        "complexity_label",
                        "annotation.annotator",
                        "annotation.annotated_at",
                        "annotation.reviewed_by",
                        "annotation.reviewed_at",
                    ],
                    "blocking_reasons": [
                        "no_corpus_mapping" if not row.get("annotations") else "corpus_mapping_not_verified",
                        "not_human_reviewed",
                    ],
                    "scorable": False,
                }
            )
    return records


def category_counts(records: Sequence[dict]) -> dict[str, int]:
    counts = {name: 0 for name in CATEGORIES}
    for record in records:
        counts[record["category"]] = counts.get(record["category"], 0) + 1
    return counts


def render_markdown(records: Sequence[dict], source: str, source_rows: int) -> str:
    counts = category_counts(records)
    missing = [name for name in CATEGORIES if counts.get(name, 0) == 0]
    lines = [
        "# Human annotation work package",
        "",
        "Generated by `scripts/validation/build_annotation_workpackage.py`. This is a",
        "**worksheet**, not ground truth and not an annotation dataset. Every row is",
        "`source: llm_candidate` / `review_status: DRAFT_UNVERIFIED` and `scorable: false`.",
        "It names no annotator and no reviewer, and stamps no date.",
        "",
        f"- source dataset: `{source}` ({source_rows} samples)",
        f"- worksheet rows: {len(records)}",
        "- scorable rows: 0 (by construction)",
        "",
        "## Category coverage",
        "",
        "| category | rows | what the reviewer must decide |",
        "|---|---|---|",
        f"| `regulation_precise` | {counts.get('regulation_precise', 0)} | the exact clause/passage a regulation question resolves to |",
        f"| `ingredient_query` | {counts.get('ingredient_query', 0)} | the passage that fixes the ingredient's limit/role |",
        f"| `product_knowledge` | {counts.get('product_knowledge', 0)} | the passage that answers a product/standard question |",
        f"| `semantic_paraphrase` | {counts.get('semantic_paraphrase', 0)} | that the rewrite still resolves to the same passage as its base question |",
        f"| `multi_document` | {counts.get('multi_document', 0)} | which distinct documents the evidence actually spans |",
        f"| `image_related` | {counts.get('image_related', 0)} | whether an image genuinely required, vs answerable from text |",
        f"| `no_answer_or_conflict` | {counts.get('no_answer_or_conflict', 0)} | that the corpus truly cannot answer, or that sources conflict |",
        "",
    ]
    if missing:
        lines += [
            "## Categories with no source material",
            "",
            "These categories have **no committed sample** and must be authored by a human",
            "before the benchmark can honestly claim to cover them:",
            "",
        ]
        lines += [f"- `{name}`" for name in missing]
        lines += [
            "",
            "For `no_answer_or_conflict`, the committed set contains no unanswerable or",
            "conflicting-evidence question at all. Testing only answerable questions cannot",
            "show that the system correctly declines when it should. These must be written",
            "against a real corpus, not synthesised from the answerable set.",
            "",
        ]
    lines += [
        "## Blocking conditions (why every row is unscorable)",
        "",
        "1. **No independent corpus.** The committed set has no stable `doc_id`/`chunk_id`,",
        "   so no passage resolves into a real index. A reviewer needs an actual sealed",
        "   corpus and its `corpus_sha256` before a single id can be filled in.",
        "2. **No human review.** No record carries a reviewer or a date. Until a named",
        "   person reviews it, `review_status` stays `DRAFT_UNVERIFIED` and the scoring",
        "   gate refuses it.",
        "",
        "## Reviewer procedure",
        "",
        "1. Obtain the corpus: seal an epoch, then read its fingerprint from the corpus",
        "   probe artifact (`metadata.json -> corpus.fingerprints[].content_sha256`).",
        "2. For each worksheet row, map every supporting passage in `candidate_contexts` to",
        "   a real `(doc_id, chunk_id)` in that epoch, keeping the entries that are truly",
        "   supporting and dropping the ones that are not.",
        "3. Decide `visual_required` and `complexity_label` by hand. Do not derive either",
        "   from `business_type` or from a Router prediction.",
        "4. Emit a record in the `annotations.schema.json` shape with",
        "   `annotation = {annotator: <you>, method: manual-passage-mapping,",
        "   annotated_at: <iso-date>, source: human, review_status: DRAFT_UNVERIFIED}`.",
        "5. A second person reviews and promotes with `benchmarks.annotation.promote_to_reviewed`.",
        "   `reviewed_by` must differ from `annotator`.",
        "6. Validate the finished dataset:",
        "   `python3 scripts/validation/audit_annotations.py --dataset <file>`",
        "   It must exit `0` with `scorable == number of reviewed records`.",
        "",
        "The formal schema and lifecycle are in `tests/evaluation/golden_set_v2/README.md`.",
        "",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default="tests/evaluation/golden_set.jsonl", help="source golden set")
    parser.add_argument("--out", default="tests/evaluation/golden_set_v2/annotation_workpackage_v1.jsonl")
    parser.add_argument("--markdown", default="tests/evaluation/golden_set_v2/ANNOTATION_WORKPACKAGE.md")
    args = parser.parse_args(argv)

    try:
        rows = load_rows(args.dataset)
    except DatasetError as exc:
        print(f"dataset error: {exc}", file=sys.stderr)
        return EXIT_DATASET_ERROR

    records = build_records(rows)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    if args.markdown:
        md_path = Path(args.markdown)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown(records, args.dataset, len(rows)), encoding="utf-8")

    counts = category_counts(records)
    print(f"work package written: {out_path} ({len(records)} rows, 0 scorable)")
    for name in CATEGORIES:
        print(f"  {name:24s} {counts.get(name, 0)}")
    print(f"markdown written: {args.markdown}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
