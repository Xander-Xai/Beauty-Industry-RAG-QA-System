"""Compare retrieval arms on identical sample and gold identities.

Offline diagnostic only. The publication gate still requires actual executor,
source approval and matching corpus provenance.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from benchmarks.failure_triage import analyze_failures


def compare_arms(rows: Iterable[dict[str, Any]], baseline: str, candidate: str) -> dict[str, Any]:
    if not baseline or not candidate or baseline == candidate:
        raise ValueError("two distinct configuration names are required")
    records = list(rows)
    analyze_failures(records)
    arms = {
        name: {row["sample_id"]: row for row in records if row["config"] == name}
        for name in (baseline, candidate)
    }
    left, right = arms[baseline], arms[candidate]
    if not left or left.keys() != right.keys():
        raise ValueError("sample populations differ or are empty")
    improvements, regressions = [], []
    hit_gains, hit_losses = [], []
    recall_deltas, rr_deltas = [], []
    excluded = 0
    for sample in sorted(left):
        a, b = left[sample], right[sample]
        gold = set(a["relevant_items"])
        if gold != set(b["relevant_items"]) or a.get("business_type") != b.get("business_type"):
            raise ValueError("gold or business type differs across arms")
        if not gold:
            excluded += 1
            continue
        ranks = [row["retrieved_items"][:10] for row in (a, b)]
        recall = [len(gold.intersection(rank)) / len(gold) for rank in ranks]
        rr = [
            next((1 / i for i, item in enumerate(rank, 1) if item in gold), 0.0)
            for rank in ranks
        ]
        delta = recall[1] - recall[0]
        recall_deltas.append(delta)
        rr_deltas.append(rr[1] - rr[0])
        if delta > 0:
            improvements.append(sample)
        elif delta < 0:
            regressions.append(sample)
        if not rr[0] and rr[1]:
            hit_gains.append(sample)
        elif rr[0] and not rr[1]:
            hit_losses.append(sample)
    n = len(recall_deltas)
    return {
        "kind": "PAIRED_RETRIEVAL_DIAGNOSTIC_ONLY",
        "baseline": baseline,
        "candidate": candidate,
        "scored": n,
        "excluded_no_gold": excluded,
        "delta_recall_at_10": sum(recall_deltas) / n if n else None,
        "delta_mrr_at_10": sum(rr_deltas) / n if n else None,
        "recall_improved_ids": improvements,
        "recall_regressed_ids": regressions,
        "hit_gained_ids": hit_gains,
        "hit_lost_ids": hit_losses,
        "publication_eligibility": "NOT_ASSESSED",
    }
