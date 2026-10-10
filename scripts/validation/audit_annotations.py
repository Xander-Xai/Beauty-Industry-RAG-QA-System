#!/usr/bin/env python3
"""Audit a golden-set annotation dataset and emit the human work order.

Usage
-----
    python3 scripts/validation/audit_annotations.py \
        --dataset data/eval/golden_set_v2/annotations_v1.jsonl

    # generate the backlog of decisions a human still has to make
    python3 scripts/validation/audit_annotations.py \
        --backlog data/eval/golden_set_v2/annotations_v1.jsonl \
        --source-dataset tests/evaluation/golden_set.jsonl

Exit codes
----------
    0  every record is schema-valid and at least one is scorable
    1  records are invalid, or none is scorable (drafts do not count)
    2  the dataset could not be read (missing file, malformed JSON)
    3  the source dataset needed for the backlog could not be read

What this tool does **not** do
-----------------------------
It never writes a label, never invents a reviewer and never promotes a draft.
The committed annotation datasets are empty because the labeling has not been
performed; that is a human task, and this tool only reports its size.
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

from benchmarks.annotation import (  # noqa: E402
    REVIEW_STATUS_DRAFT,
    REVIEW_STATUS_REVIEWED,
    AnnotationError,
    audit_dataset,
    dataset_sha256,
    load_records,
)

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_DATASET_ERROR = 2
EXIT_SOURCE_ERROR = 3


def _render_report(report, dataset_path: str) -> str:
    lines = [
        f"dataset            : {dataset_path}",
        f"sha256             : {dataset_sha256(dataset_path)}",
        f"schema             : {report.schema_version}",
        f"records            : {report.total}",
        f"valid              : {report.valid_count}",
        f"scorable           : {report.scorable_count}",
        f"draft              : {report.draft_count}",
        f"corpus_version(s)  : {', '.join(report.corpus_versions) or '-'}",
        f"corpus_sha256      : {report.corpus_sha256 or '-'}",
    ]
    reasons = report.invalid_reasons()
    if reasons:
        lines.append("")
        lines.append("failure reasons:")
        for reason, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"  {count:>5}  {reason}")
    broken = [verdict for verdict in report.verdicts if not verdict.valid]
    if broken:
        lines.append("")
        lines.append(f"first {min(5, len(broken))} rejected record(s):")
        for verdict in broken[:5]:
            lines.append(f"  {verdict.sample_id}: {', '.join(verdict.reasons[:4])}")
    return "\n".join(lines)


def build_backlog(annotations_path: str, source_dataset: str) -> str:
    """Render the decisions a human annotator still has to make.

    Derived strictly from the two datasets: which committed golden-set samples
    have no annotation record yet, and which fields each record is missing. It
    carries no labels of its own — it states what must be decided, never what the
    decision is.
    """
    from benchmarks.dataset import load_rows

    annotated = load_records(annotations_path)
    by_id: dict[str, dict] = {}
    for record in annotated:
        sample_id = str(record.get("sample_id") or "")
        if sample_id:
            by_id[sample_id] = record

    try:
        source_rows = load_rows(source_dataset)
    except Exception as exc:  # noqa: BLE001
        raise AnnotationError(f"{source_dataset}: {exc}") from exc

    missing_rows: list[tuple[str, str]] = []
    field_gaps: dict[str, list[str]] = {}
    reviewed = 0
    drafts = 0
    scorable = 0

    for index, row in enumerate(source_rows):
        sample_id = f"{index:04d}"
        question = str(row.get("question") or "").strip()
        record = by_id.get(sample_id)
        if record is None:
            missing_rows.append((sample_id, question))
            continue
        gaps: list[str] = []
        for field in ("visual_required", "complexity_label", "corpus_version", "corpus_sha256", "annotations"):
            value = record.get(field)
            if value in (None, "", []):
                gaps.append(field)
        provenance = record.get("annotation") if isinstance(record.get("annotation"), dict) else {}
        for field in ("annotator", "method", "annotated_at", "source", "review_status"):
            if not provenance.get(field):
                gaps.append(f"annotation.{field}")
        status = provenance.get("review_status")
        if status == REVIEW_STATUS_REVIEWED:
            reviewed += 1
            if not gaps:
                scorable += 1
        elif status == REVIEW_STATUS_DRAFT:
            drafts += 1
            gaps.append("REQUIRES_HUMAN_REVIEW")
        if gaps:
            field_gaps[sample_id] = gaps

    out = [
        "# Human annotation backlog",
        "",
        "Generated by `scripts/validation/audit_annotations.py`. It lists what a human",
        "annotator still has to decide. It contains **no labels** — the decision itself is",
        "never made here, and nothing in this file may be read as ground truth.",
        "",
        f"- source dataset: `{source_dataset}` ({len(source_rows)} samples)",
        f"- annotation file: `{annotations_path}` ({len(annotated)} records)",
        f"- reviewed records: {reviewed}",
        f"- unreviewed drafts: {drafts}",
        f"- fully reviewed and scorable: {scorable}",
        f"- samples with no annotation record yet: {len(missing_rows)}",
        "",
    ]
    if missing_rows:
        out += ["## 1. Samples needing a first annotation pass", ""]
        out += ["| sample_id | question (first 60 chars) |", "|---|---|"]
        out += [f"| `{sample_id}` | {question[:60]} |" for sample_id, question in missing_rows[:60]]
        if len(missing_rows) > 60:
            out.append(f"| … | {len(missing_rows) - 60} more |")
        out.append("")
    if field_gaps:
        out += ["## 2. Records with missing or unreviewed fields", ""]
        out += ["| sample_id | outstanding items |", "|---|---|"]
        out += [f"| `{sample_id}` | {', '.join(gaps)} |" for sample_id, gaps in list(field_gaps.items())[:60]]
        if len(field_gaps) > 60:
            out.append(f"| … | {len(field_gaps) - 60} more |")
        out.append("")
    if not missing_rows and not field_gaps:
        out += ["## Outstanding work", "", "None: every source sample has a reviewed, complete record.", ""]
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--dataset",
        default="data/eval/golden_set_v2/annotations_v1.jsonl",
        help="annotation dataset to audit",
    )
    parser.add_argument("--json", action="store_true", help="emit the full audit report as JSON")
    parser.add_argument("--backlog", metavar="PATH", help="write the human work order to this Markdown file")
    parser.add_argument(
        "--source-dataset",
        default="tests/evaluation/golden_set.jsonl",
        help="committed golden set the backlog is derived from",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="exit 0 when the dataset is empty (the state before any annotation pass)",
    )
    args = parser.parse_args(argv)

    try:
        records = load_records(args.dataset)
    except AnnotationError as exc:
        print(f"annotation dataset error: {exc}", file=sys.stderr)
        return EXIT_DATASET_ERROR

    report = audit_dataset(records)

    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(_render_report(report, args.dataset))
        if not records:
            print("\nThis dataset is empty: the annotation pass has not been performed.")
            print("That is expected until a human annotator fills it in.")

    if args.backlog:
        try:
            backlog = build_backlog(args.dataset, args.source_dataset)
        except AnnotationError as exc:
            print(f"source dataset error: {exc}", file=sys.stderr)
            return EXIT_SOURCE_ERROR
        out_path = Path(args.backlog)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(backlog, encoding="utf-8")
        print(f"\nbacklog written: {out_path}")

    if not records:
        return EXIT_OK if args.allow_empty else EXIT_INVALID
    if report.invalid_reasons() or report.scorable_count == 0:
        return EXIT_INVALID
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())