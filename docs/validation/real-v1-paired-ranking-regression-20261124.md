# Real Corpus V1: paired ranking regression audit (2026-11-24)

## Evidence boundary

Offline recomputation of the previously published Elasticsearch analyzer A/B experiment, not a new retrieval or model run. Frozen source at commit `212fc0dae3c06a2e297d8a5b41e43d9c961afa9a`:

- [Per-sample rankings](evidence/real-v1-approved-bm25-per-sample.jsonl), Git blob `3d670a3fbc0748249824927a69a0f73ec3badcfa`.
- [Original experiment](chinese-analyzer-ab.md) and [approved BM25 report](real-v1-approved-bm25-run.md).

27 signed samples, 212 indexed chunks. Compare `ab_standard` to `ab_smartcn` and `ab_ik_smart`, not to a new dense or reranking run. Recomputed from ranked chunk IDs and gold chunk IDs, not copied from claimed metric fields. Recall is matched unique gold / gold count; reciprocal rank is 1 / first relevant rank (zero on miss); NDCG uses binary relevance and ideal DCG at 10. All differences are candidate minus baseline, macro-averaged over identical samples.

## Paired results

| Population | n | Candidate | Delta Recall@10 | Delta MRR@10 | Delta NDCG@10 | Delta Hit@1 |
|---|---:|---|---:|---:|---:|---:|
| All | 27 | SmartCN | +0.222222 | +0.058583 | +0.107225 | -0.037037 |
| All | 27 | IK Smart | +0.259259 | +0.053248 | +0.105459 | -0.037037 |
| Regulation | 14 | SmartCN | +0.214286 | +0.033730 | +0.091239 | -0.071429 |
| Regulation | 14 | IK Smart | +0.214286 | +0.014087 | +0.064491 | -0.071429 |
| Ingredient | 13 | SmartCN | +0.230769 | +0.085348 | +0.124440 | 0 |
| Ingredient | 13 | IK Smart | +0.307692 | +0.095421 | +0.149577 | 0 |

Both candidates improve reciprocal rank on 13 samples. SmartCN has one reciprocal-rank regression (REAL-V1-005: rank 1 to 2). IK Smart has two (REAL-V1-005: rank 1 to 2; REAL-V1-024: rank 3 to 5). Remaining misses: SmartCN REAL-V1-001 and REAL-V1-015; IK Smart REAL-V1-001.

## Follow-up acceptance

- REAL-V1-001: investigate candidate recall before reranking; a reranker cannot recover absent candidates.
- REAL-V1-005 and REAL-V1-024: explicitly inspect ranking regressions in subsequent same-corpus dense/hybrid/rerank comparisons.
- REAL-V1-015: compare recovery across analyzers; do not conflate analyzer selection with embedding gain.
- Preserve the full 27-sample population and fixed corpus; this is a diagnostic subset, not a new holdout or an independent quality claim.
- For future experiments, record both recall and ranking metrics; do not promote a candidate solely for recall improvements. Obtain actual BGE/CrossEncoder assets, validate index provenance, and retain per-arm failures.
- No production, Dense, Hybrid, Rerank, RAGAS, or GPU performance claim is made. Issue #18 and #125 remain open.

## Reproduction

Run from repository root with Python standard library only:

```python
import json
import math
from pathlib import Path

rows = [
    json.loads(line)
    for line in Path("docs/validation/evidence/real-v1-approved-bm25-per-sample.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()
    if line.strip()
]
assert len(rows) == 27
assert len({row["sample_id"] for row in rows}) == 27


def score(row, arm):
    gold = set(row["gold_chunk_ids"])
    ranks = row[arm]["retrieved_chunk_ids"][:10]
    assert gold and len(ranks) == len(set(ranks))
    first = next((i for i, key in enumerate(ranks, 1) if key in gold), None)
    dcg = sum(1 / math.log2(i + 1) for i, key in enumerate(ranks, 1) if key in gold)
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(10, len(gold)) + 1))
    return (
        len(gold.intersection(ranks)) / len(gold),
        1 / first if first else 0,
        dcg / ideal,
        int(first == 1),
    )


for arm in ("ab_smartcn", "ab_ik_smart"):
    for group in ("all", "regulation", "ingredient"):
        subset = [r for r in rows if group == "all" or r["business_type"] == group]
        pairs = [(r["sample_id"], score(r, "ab_standard"), score(r, arm)) for r in subset]
        deltas = [sum(c[i] - b[i] for _, b, c in pairs) / len(pairs) for i in range(4)]
        print(arm, group, len(pairs), deltas)
        print("MRR regressions:", [s for s, b, c in pairs if c[1] < b[1]])
        print("Misses:", [s for s, _, c in pairs if c[0] == 0])
```

The paired arithmetic was independently executed against the pinned JSONL during this audit. The Python snippet above documents the equivalent reproduction; no retrieval backend was contacted.
