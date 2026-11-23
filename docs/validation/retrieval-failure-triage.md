# Retrieval failure triage (offline diagnosis)

## Scope

`benchmarks/failure_triage.py` groups and diagnoses per-query ranked outputs from an existing retrieval benchmark run. It is a diagnostic tool, **not** a replacement for data contracts, source approval, live backend probes or benchmark publication gates.

Run it against `per_query_results.jsonl` from an already executed configuration:

```bash
python -m benchmarks.failure_triage \
  --per-query artifacts/benchmarks/REPLACE_WITH_RUN_ID/per_query_results.jsonl \
  --out /tmp/retrieval-failure-triage.json

pytest -q tests/benchmark/test_failure_triage.py
```

## Classification semantics

For each retrieval configuration and unique sample ID:

- No relevant identifiers: `excluded_no_gold` — excluded from scored queries rather than counted as a miss.
- No relevant passage in positions 1–10: `misses_at_10`.
- First relevant passage in positions 2–10: `late_hits_at_2_to_10`.
- First relevant passage in position 1: `top1_hits`.

Results are also grouped by business type. A **no-gold row can be excluded but cannot claim a positive hit, relevant rank, nonzero reciprocal-rank/recall, malformed metric, or NaN**; a zero-valued placeholder is allowed but never added to the scored denominator. Duplicate sample keys, gold/retrieval identifiers, missing identity lists and contradictory `hit_at_10`, `first_relevant_rank`, `rr` or `recall_at_10` claims cause a hard failure. The latter values are recomputed from ranked identifiers, with MRR@10 treating relevant results beyond rank 10 as misses. No unseen result is fabricated.

The output sets `publication_eligibility=NOT_ASSESSED`; a diagnostic can neither promote synthetic fixture numbers nor override the validated publication decision of the benchmark runner.

## Next verified-evidence steps

1. Reproduce each failed retrieval against the same approved source/corpus version.
2. Check chunk identity, missing source text, query analyzer and domain-specific tokenizer behavior before attributing all misses to embedding quality.
3. Use one frozen dataset, source approvals and stable identifiers to compare BM25, Dense, Hybrid and rerank, only after corresponding weights and backends actually pass preflight.
4. Separately evaluate answer-grounding, permission isolation and refusal behavior; retrieval hit metrics are not answer-quality metrics.

Real Corpus V1's 27-sample approved BM25 experiment is only a single-arm local benchmark. Dense/Hybrid and production service latency remain unverified until separately executed.
