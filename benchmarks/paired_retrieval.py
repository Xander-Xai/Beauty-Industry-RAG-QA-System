"""Compare retrieval arms on identical sample and gold identities.

Offline diagnostic only. The publication gate still requires actual executor,
source approval and matching corpus provenance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from benchmarks.failure_triage import analyze_failures
from benchmarks.latency import summarize_latency
from benchmarks.metrics import ndcg_graded
from benchmarks.models import GRADE_THRESHOLD_RELEVANT, RELEVANCE_GRADES, RELEVANCE_HIGHLY_RELEVANT


def compare_arms(rows: Iterable[dict[str, Any]], baseline: str, candidate: str) -> dict[str, Any]:
    if not baseline or not candidate or baseline == candidate:
        raise ValueError("two distinct configuration names are required")
    records = list(rows)
    analyze_failures(records)
    arms = {name: {row["sample_id"]: row for row in records if row["config"] == name} for name in (baseline, candidate)}
    left, right = arms[baseline], arms[candidate]
    if not left or left.keys() != right.keys():
        raise ValueError("sample populations differ or are empty")
    improvements, regressions = [], []
    hit_gains, hit_losses = [], []
    rr_improvements, rr_regressions = [], []
    ndcg_improvements, ndcg_regressions = [], []
    hit1_deltas = []
    recall_deltas, rr_deltas, ndcg_deltas = [], [], []
    excluded = 0
    regulation_scored = 0
    regulation_graded = 0
    regulation_hit_losses = []
    regulation_high_grade_losses = []
    regulation_high_grade_misses = []
    latency_pairs = [[], []]
    for sample in sorted(left):
        a, b = left[sample], right[sample]
        gold = set(a["relevant_items"])
        if gold != set(b["relevant_items"]) or a.get("business_type") != b.get("business_type"):
            raise ValueError("gold or business type differs across arms")
        if any(a.get(field) != b.get(field) for field in ("question", "difficulty")):
            raise ValueError("question or difficulty differs across arms")
        grades = a.get("relevant_grades")
        if grades != b.get("relevant_grades"):
            raise ValueError("relevance grades differ across arms")
        if grades is not None and (
            not isinstance(grades, dict)
            or set(grades) != gold
            or any(
                type(grade) is not int or grade not in RELEVANCE_GRADES or grade < GRADE_THRESHOLD_RELEVANT
                for grade in grades.values()
            )
        ):
            raise ValueError("invalid relevance grades")
        # Latency must be matched across both arms, not filled with zero.
        timings = []
        for row in (a, b):
            record = row.get("latency_ms")
            if record is not None and not isinstance(record, dict):
                raise ValueError("invalid latency record")
            value = (record or {}).get("total_retrieval_ms")
            if value is not None and (
                type(value) not in (int, float) or not math.isfinite(value) or value < 0
            ):
                raise ValueError("invalid total retrieval latency")
            timings.append(value)
        if all(value is not None for value in timings):
            for index, value in enumerate(timings):
                latency_pairs[index].append({"total_retrieval_ms": value})
        if not gold:
            excluded += 1
            continue
        ranks = [row["retrieved_items"][:10] for row in (a, b)]
        if a.get("business_type") == "regulation":
            regulation_scored += 1
            if gold.intersection(ranks[0]) and not gold.intersection(ranks[1]):
                regulation_hit_losses.append(sample)
            if grades is not None:
                regulation_graded += 1
                highly_relevant = {key for key, grade in grades.items() if grade == RELEVANCE_HIGHLY_RELEVANT}
                if highly_relevant.intersection(ranks[0]).difference(ranks[1]):
                    regulation_high_grade_losses.append(sample)
                if highly_relevant.difference(ranks[1]):
                    regulation_high_grade_misses.append(sample)
        recall = [len(gold.intersection(rank)) / len(gold) for rank in ranks]
        rr = [next((1 / i for i, item in enumerate(rank, 1) if item in gold), 0.0) for rank in ranks]
        if grades is not None:
            ndcg = [ndcg_graded(grades, rank) for rank in ranks]
            # Nonempty validated gold has a defined NDCG in both arms.
            if ndcg[0] is None or ndcg[1] is None:
                raise ValueError("undefined NDCG for nonempty gold")
            ndcg_delta = ndcg[1] - ndcg[0]
            ndcg_deltas.append(ndcg_delta)
            if ndcg_delta > 0:
                ndcg_improvements.append(sample)
            elif ndcg_delta < 0:
                ndcg_regressions.append(sample)
        delta = recall[1] - recall[0]
        recall_deltas.append(delta)
        rr_delta = rr[1] - rr[0]
        rr_deltas.append(rr_delta)
        hit1_deltas.append(int(rr[1] == 1) - int(rr[0] == 1))
        if rr_delta > 0:
            rr_improvements.append(sample)
        elif rr_delta < 0:
            rr_regressions.append(sample)
        if delta > 0:
            improvements.append(sample)
        elif delta < 0:
            regressions.append(sample)
        if not rr[0] and rr[1]:
            hit_gains.append(sample)
        elif rr[0] and not rr[1]:
            hit_losses.append(sample)
    n = len(recall_deltas)
    matched_latency = len(latency_pairs[0])
    # A P95 comparison on a cherry-picked subset is not a full-population comparison.
    full_latency = matched_latency == len(left)
    p95 = [
        summarize_latency(records)["total_retrieval_ms"]["p95"] if full_latency else None
        for records in latency_pairs
    ]
    return {
        "kind": "PAIRED_RETRIEVAL_DIAGNOSTIC_ONLY",
        "baseline": baseline,
        "candidate": candidate,
        "scored": n,
        "excluded_no_gold": excluded,
        "delta_recall_at_10": sum(recall_deltas) / n if n else None,
        "delta_mrr_at_10": sum(rr_deltas) / n if n else None,
        "delta_hit_at_1": sum(hit1_deltas) / n if n else None,
        "mrr_improved_ids": rr_improvements,
        "mrr_regressed_ids": rr_regressions,
        "ndcg_improved_ids": ndcg_improvements,
        "ndcg_regressed_ids": ndcg_regressions,
        "ndcg_grade_verified_samples": len(ndcg_deltas),
        "delta_ndcg_at_10": sum(ndcg_deltas) / n if n and len(ndcg_deltas) == n else None,
        "recall_improved_ids": improvements,
        "recall_regressed_ids": regressions,
        "hit_gained_ids": hit_gains,
        "hit_lost_ids": hit_losses,
        "regulation": {
            "scored_samples": regulation_scored,
            "grade_verified_samples": regulation_graded,
            "hit_lost_ids": regulation_hit_losses,
            "high_grade_passage_lost_ids": regulation_high_grade_losses,
            "high_grade_passage_missing_candidate_ids": regulation_high_grade_misses,
            "criticality_independently_reviewed": False,
        },
        "paired_latency": {
            "matched_samples": matched_latency,
            "expected_samples": len(left),
            "complete": full_latency,
            "baseline_p95_ms": p95[0],
            "candidate_p95_ms": p95[1],
            "delta_p95_ms": p95[1] - p95[0] if full_latency else None,
        },
        "publication_eligibility": "NOT_ASSESSED",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-query", type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.per_query.resolve() == args.out.resolve():
        parser.error("output must not overwrite input evidence")
    try:
        source = args.per_query.read_bytes()
        rows = [json.loads(line) for line in source.splitlines() if line.strip()]
        report = compare_arms(rows, args.baseline, args.candidate)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        parser.error(f"invalid per-query artifact: {type(exc).__name__}")
    report["input_sha256"] = hashlib.sha256(source).hexdigest()
    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        parser.error(f"cannot create fresh output evidence: {type(exc).__name__}")
    return 0 if report["scored"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
