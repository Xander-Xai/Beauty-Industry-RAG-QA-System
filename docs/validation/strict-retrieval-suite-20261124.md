# Five-arm retrieval acceptance (2026-11-24)

Use a new isolated experiment index and approved corpus, matching Qdrant collection, BGE/BiEncoder/CrossEncoder weights, and reviewed golden set. Do not overwrite the frozen V1 BM25 evidence.

```bash
python -m benchmarks.strict_retrieval_suite --dataset /path/to/reviewed.jsonl --output-dir artifacts/benchmarks/new-experiment
```

The entrypoint invokes the existing real benchmark with BM25, Dense, Hybrid RRF, Hybrid + BiEncoder and Hybrid + BiEncoder + CrossEncoder. It requests complete suite and publishable evidence, not just one successful arm. Missing assets or inconsistent sources remain BLOCKED/nonzero. Use `benchmarks.paired_retrieval` on the emitted per-query data to compare arms. No results are claimed until the actual stack runs. Full-answer RAGAS remains a separate validation.

## Seven-arm architecture ablation (2026-11-25)

For the architecture decision in #154, add `--include-single-ce` to the same command. It executes the existing five arms and two extra variants (BiEncoder followed by CE-A only, CE-B only). Both use the same recall and BiEncoder candidate depths, strict batch execution and model-asset provenance. The selected CE uses its own tokenizer and raw model score for ranking; scores are not calibrated online gate probabilities. The canonical online dual-CE path and its gates are unchanged. Compare all seven arms with paired quality and latency evidence before changing production ranking or recalibrating Evidence/Answer Gate. This introduces execution paths, not measured results. Missing assets or incomplete populations must still block publication.
