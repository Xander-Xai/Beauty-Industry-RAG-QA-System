"""Offline retrieval Bad Case triage from a completed benchmark's per-query JSONL.

This is a diagnostic, not a benchmark validator. Publication still requires
the separate dataset contract, source approvals, real-executor and provenance
gates; this module never upgrades a fixture or blocked run to measured.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def analyze_failures(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, str]] = set()
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each JSONL row must be an object")
        config, sample = row.get("config"), row.get("sample_id")
        relevant, retrieved = row.get("relevant_items"), row.get("retrieved_items")
        if not isinstance(config, str) or not config or not isinstance(sample, str) or not sample:
            raise ValueError("missing config or sample_id")
        if (config, sample) in seen:
            raise ValueError("duplicate config/sample_id")
        seen.add((config, sample))
        if not isinstance(relevant, list) or not isinstance(retrieved, list):
            raise ValueError("missing relevant_items or retrieved_items list")
        if not all(isinstance(item, str) for item in relevant + retrieved):
            raise ValueError("passage identities must be strings")
        if len(set(retrieved)) != len(retrieved):
            raise ValueError("duplicate retrieved passage identity")
        total += 1
        group = groups.setdefault(
            config,
            {
                "rows": 0,
                "scored": 0,
                "excluded_no_gold": 0,
                "top1_hits": 0,
                "late_hits_at_2_to_10": 0,
                "misses_at_10": 0,
                "failed_case_ids": [],
                "late_hit_case_ids": [],
                "by_business_type": {},
            },
        )
        group["rows"] += 1
        if not relevant:
            group["excluded_no_gold"] += 1
            continue
        group["scored"] += 1
        gold = set(relevant)
        ranked = retrieved[:10]
        matched_rank = next((i for i, key in enumerate(ranked, 1) if key in gold), None)
        full_rank = next((i for i, key in enumerate(retrieved, 1) if key in gold), None)
        if "first_relevant_rank" in row and row["first_relevant_rank"] != full_rank:
            raise ValueError("first_relevant_rank contradicts ranked passage identifiers")
        expected_rr = 1 / full_rank if full_rank is not None and full_rank <= 10 else 0.0
        if "rr" in row and (
            isinstance(row["rr"], bool)
            or not isinstance(row["rr"], (int, float))
            or not math.isfinite(row["rr"])
            or not math.isclose(row["rr"], expected_rr, rel_tol=1e-8, abs_tol=1e-12)
        ):
            raise ValueError("rr contradicts ranked passage identifiers")
        expected_recall = len(gold.intersection(ranked)) / len(gold)
        if "recall_at_10" in row and (
            isinstance(row["recall_at_10"], bool)
            or not isinstance(row["recall_at_10"], (int, float))
            or not math.isfinite(row["recall_at_10"])
            or not math.isclose(row["recall_at_10"], expected_recall, rel_tol=1e-8, abs_tol=1e-12)
        ):
            raise ValueError("recall_at_10 contradicts ranked passage identifiers")
        asserted_hit = row.get("hit_at_10")
        if asserted_hit is not None and (type(asserted_hit) is not bool or asserted_hit != (matched_rank is not None)):
            raise ValueError("hit_at_10 contradicts ranked passage identifiers")
        business_type = str(row.get("business_type") or "unknown")
        bucket = group["by_business_type"].setdefault(business_type, {"scored": 0, "misses_at_10": 0})
        bucket["scored"] += 1
        if matched_rank is None:
            group["misses_at_10"] += 1
            group["failed_case_ids"].append(sample)
            bucket["misses_at_10"] += 1
        elif matched_rank == 1:
            group["top1_hits"] += 1
        else:
            group["late_hits_at_2_to_10"] += 1
            group["late_hit_case_ids"].append(sample)
    for group in groups.values():
        group["failed_case_ids"].sort()
        group["late_hit_case_ids"].sort()
        group["by_business_type"] = dict(sorted(group["by_business_type"].items()))
    return {
        "kind": "RETRIEVAL_DIAGNOSTIC_ONLY",
        "rows": total,
        "configurations": dict(sorted(groups.items())),
        "results_are_benchmark": None,
        "publication_eligibility": "NOT_ASSESSED",
        "note": "Requires source approval and benchmark provenance gates; missing gold is excluded, not scored as failure.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-query", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        with args.per_query.open(encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        summary = analyze_failures(rows)
    except (ValueError, OSError, TypeError) as exc:
        parser.error(f"invalid per-query artifact: {type(exc).__name__}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"diagnostic written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
