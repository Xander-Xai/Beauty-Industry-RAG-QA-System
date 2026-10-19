# Benchmark artifacts

Current classification, stated before anything else:

- Retrieval benchmark **framework**: `REPO_VERIFIED` (metrics, provenance,
  readiness probing and this artifact contract are implemented and test-covered).
- Retrieval benchmark **result**: `PENDING`. No run artifact is committed, so no
  retrieval metric is available here.

Runtime output of the reproducible retrieval benchmark lives here:

```text
artifacts/benchmarks/<run-id>/
├── metadata.json          # run_id, git_sha, dataset/config sha256, sample count,
│                          # dataset_contract, attributable, synthetic_retriever,
│                          # results_are_benchmark, and `corpus` (store fingerprints
│                          # + measured golden-passage correspondence)
├── environment.json       # python/torch/cuda/service versions, plus each model's
│                          # path/revision (or present=false when not installed)
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

## The real executor is wired (issue #18)

`benchmarks/executors.py` supplies the production-retriever adapters the CLI
previously lacked. `--executor real` wires them for every configuration whose
live backend probes pass:

```bash
python -m benchmarks.retrieval_benchmark --executor real --config bm25 --limit 5
```

* The default is still `--executor none`, so a run without the flag blocks every
  configuration with `no real retrieval executor is wired for this
  configuration yet` — an explicit refusal, not a defect.
* The adapters call the **production** code paths
  (`retrieval.bm25_retriever`, `retrieval.dense_retriever`,
  `retrieval_service.rerank.rrf_fusion`), and item identity is derived by
  `benchmarks.relevance.relevance_key`, so ground truth and hits agree on what
  "the same passage" means.
* They are **fail-closed**: a backend that does not answer a real probe raises
  instead of returning an empty ranking, so an unreachable stack stays `BLOCKED`
  rather than scoring an all-zero "success".
* Only `bm25`, `dense` and `hybrid_rrf` have a real executor. The BiEncoder /
  CrossEncoder rerank configurations stay scoped out of issue #18 until real
  weights exist, and `build_executor` raises for them rather than falling back.

## What still blocks a real result on this host

One measured gap remains, so even `--executor real` reports `BLOCKED` here:

* **No corpus can satisfy the `corpus` backend.** This is measured, not assumed:
  `probe_corpus` fingerprints the configured Elasticsearch index and Qdrant
  collection and reports how many of the sampled golden-set passages they
  actually contain. On this host the answer is **0 of 60** (there are 262
  distinct passages across 301 queries), so every configuration blocks on
  `corpus_does_not_contain_ground_truth`. Indexing the ground truth itself would
  make every configuration score recall 1.0 by construction, so the probe is
  read-only and refuses that shortcut. Unblocking this needs an independently
  sourced corpus plus a way to register and verify it; inventing either would
  produce a meaningless number.

Until this is resolved, `--list-configs` is the honest ceiling of what this
harness can do on this host, and no configuration may be reported as `EXECUTED`.
The retrieval benchmark **result** therefore stays `PENDING`.

## Reading the output honestly

* A run in which **no** configuration executed contains **no** retrieval-quality
  result. `retrieval_metrics.json` records the blocking reasons instead.
* A run that used a **synthetic fixture retriever** is a metric self-check, not a
  benchmark. `metadata.json` records `synthetic_retriever: true` and
  `results_are_benchmark: false`, and `report.md` carries the warning. The CLI
  never wires a synthetic retriever of its own — `--executor real` wires the
  production adapters, not a fixture — so this state arises only when a caller
  injects one — but the flag is derived from what the run did, never asserted.
* `latency_metrics.json` uses `null` (never `0`) for stages that did not run.
* Results in this directory are repository benchmark results. They are **not**
  historical production metrics.

## Version control

Generated runs are intentionally not committed; `.gitignore` excludes
`artifacts/benchmarks/*` while keeping this README. If a benchmark result should
become durable evidence, promote the specific run directory deliberately and
record `git_sha` plus the dataset/config hashes alongside it.
