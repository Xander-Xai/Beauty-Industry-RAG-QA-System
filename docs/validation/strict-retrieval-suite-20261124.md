# Five-arm retrieval acceptance (2026-11-24)

Use a new isolated experiment index and approved corpus, matching Qdrant collection, BGE/BiEncoder/CrossEncoder weights, and reviewed golden set. Do not overwrite the frozen V1 BM25 evidence.

```bash
python -m benchmarks.strict_retrieval_suite --dataset /path/to/reviewed.jsonl --output-dir artifacts/benchmarks/new-experiment
```

The entrypoint invokes the existing real benchmark with BM25, Dense, Hybrid RRF, Hybrid + BiEncoder and Hybrid + BiEncoder + CrossEncoder. It requests complete suite and publishable evidence, not just one successful arm. Missing assets or inconsistent sources remain BLOCKED/nonzero. Use `benchmarks.paired_retrieval` on the emitted per-query data to compare arms. No results are claimed until the actual stack runs. Full-answer RAGAS remains a separate validation.


架构治理扩展（待合并验收）：以十臂 `--architecture-ablation` 为唯一扩展入口，覆盖单/双 CE 与有/无 BiEncoder。默认五臂及全部严格发布门槛保留。单 CE 使用同步有界批次、独立分数与模型专属 provenance；真实模型运行结果仍未获得。原七臂候选不独立合并，避免重复实现。


Use the existing paired diagnostic on the same run's per-query JSONL, with the
current full chain as baseline and each challenger as candidate. The report
separately lists regulation hit losses and missing grade-2 passages, including
cases where a lower-grade passage still yields Hit@10. Relevance grade is not
an independent critical-regulation annotation; a domain reviewer must judge
criticality before a pruning decision. Total retrieval P95 is compared only
when both arms have valid timing for every matched sample. Missing observations
are null, never zero. This diagnostic does not certify publication, GPU
resource usage, downstream answer safety, or permission behavior.
