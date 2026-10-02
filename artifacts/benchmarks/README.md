# Benchmark artifacts

Runtime output of the reproducible retrieval benchmark lives here:

```text
artifacts/benchmarks/<run-id>/
├── metadata.json          # run_id, git_sha, dataset/config sha256, sample count
├── environment.json       # python/torch/cuda/service versions (unavailable when absent)
├── retrieval_metrics.json # per-config metrics and BLOCKED/EXECUTED status
├── latency_metrics.json   # per-stage p50/p90/p95/p99 (null when a stage never ran)
├── per_query_results.jsonl# one row per query per executed config
├── report.md              # human-readable run report
└── comparison.md          # only when >= 2 configs executed
```

## Generate a run

```bash
# what can actually execute in this environment
python -m benchmarks.retrieval_benchmark --list-configs

# attempt a real run
python -m benchmarks.retrieval_benchmark --config bm25 --limit 5
```

## Reading the output honestly

* A run in which **no** configuration executed contains **no** retrieval-quality
  result. `retrieval_metrics.json` records the blocking reasons instead.
* A run whose `report.md` says it used a **synthetic fixture retriever** is a
  metric self-check, not a benchmark. The CLI cannot produce such a run; only
  tests inject a fixture retriever.
* `latency_metrics.json` uses `null` (never `0`) for stages that did not run.
* Results in this directory are repository benchmark results. They are **not**
  historical production metrics.

## Version control

Generated runs are intentionally not committed; `.gitignore` excludes
`artifacts/benchmarks/*` while keeping this README. If a benchmark result should
become durable evidence, promote the specific run directory deliberately and
record `git_sha` plus the dataset/config hashes alongside it.
