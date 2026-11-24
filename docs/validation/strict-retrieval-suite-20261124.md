# Five-arm retrieval acceptance (2026-11-24)

Use a new isolated experiment index and approved corpus, matching Qdrant collection, BGE/BiEncoder/CrossEncoder weights, and reviewed golden set. Do not overwrite the frozen V1 BM25 evidence.

```bash
python -m benchmarks.strict_retrieval_suite --dataset /path/to/reviewed.jsonl --output-dir artifacts/benchmarks/new-experiment
```

The entrypoint invokes the existing real benchmark with BM25, Dense, Hybrid RRF, Hybrid + BiEncoder and Hybrid + BiEncoder + CrossEncoder. It requests complete suite and publishable evidence, not just one successful arm. Missing assets or inconsistent sources remain BLOCKED/nonzero. Use `benchmarks.paired_retrieval` on the emitted per-query data to compare arms. No results are claimed until the actual stack runs. Full-answer RAGAS remains a separate validation.
