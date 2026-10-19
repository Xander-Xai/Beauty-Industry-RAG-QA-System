# Benchmark artifacts

Current classification, stated before anything else:

- Retrieval benchmark **framework**: `REPO_VERIFIED` (metrics, provenance,
  readiness probing and this artifact contract are implemented and test-covered).
- Retrieval benchmark **result**: `LOCAL_REAL_VALIDATION` for the **Real Corpus V1
  BM25** scope, `PENDING` for Dense / Hybrid and for the legacy 301-row dataset.
  A real BM25 run over the 27 human-signed real-corpus-V1 records cleared every
  publication gate — including the source-approval gate that requires every one
  of the 212 indexed documents to carry an attributed reviewer's approval — and
  is written up in
  [`docs/validation/real-v1-approved-bm25-run.md`](../../docs/validation/real-v1-approved-bm25-run.md).
  The Chinese-tokenization A/B is in
  [`docs/validation/chinese-analyzer-ab.md`](../../docs/validation/chinese-analyzer-ab.md).
  The earlier run whose source approval was only 1/2 is retained unaltered as a
  historical record in
  [`docs/validation/real-v1-bm25-run.md`](../../docs/validation/real-v1-bm25-run.md).
- Per the rule below, no **raw run artifact** is committed. What is committed is
  a recomputable evidence package under `docs/validation/evidence/`, which lets a
  third party re-derive every published metric without Elasticsearch, model
  weights or network access:
  `python3 scripts/validation/verify_benchmark_evidence.py`.

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

Two gaps, and they are different kinds of gap:

* **Dataset (blocking, human).** The only corpus-bound dataset is the 27
  `DRAFT_UNVERIFIED` candidates, so `golden-set-contract/v2` is 0/27. A run with
  `--require-contract` refuses (exit 2) and the artifact records
  `attributable: false`. This is cleared by a named reviewer promoting records,
  not by code.
* **Corpus (cleared for real-v1).** `corpus/real_v1/` satisfies the `corpus`
  backend: the probe reports `22/22` distinct golden passages present with
  `corpus.verdict = full`. The old "0 of 60 / no corpus" state describes the
  synthetic 301-row set, which still has no independent corpus and stays
  blocked. Indexing the ground truth itself would make every configuration
  score recall 1.0 by construction, so the probe remains read-only.

So on this host BM25 can now *execute* against a real corpus, but no run is a
**benchmark result** until the labels are reviewed. `results_are_benchmark`
stays `false` and the retrieval benchmark **result** stays `PENDING`.

## Real corpus V1: what changed, and what still blocks

`corpus/real_v1/` (209 documents / 212 chunks) now exists and a real BM25 index
answers the corpus probe: `all 22/22 distinct golden passages are present`,
`corpus.verdict = full`. That removes the **corpus** blocker for the 27 real-v1
candidates. It does **not** remove the **dataset** blocker:

* All 27 candidates are `DRAFT_UNVERIFIED`, so `golden-set-contract/v2` is
  0/27 and `--require-contract` exits 2. No official result exists.
* The exploratory BM25 run against them is recorded in
  `docs/validation/real-corpus-v1-pre-review.md` with
  `results_are_benchmark=false` and `attributable=false`. It is a wiring check,
  not a benchmark: the labels are unreviewed, 27 samples is small, and two
  questions lexically overlap their passage enough to suggest reverse
  generation.
* Dense / hybrid stay `BLOCKED` on missing BGE weights.

The formal gate is unchanged and runs only after human review:

```bash
python3 -m benchmarks.retrieval_benchmark \
  --executor real --config bm25 \
  --dataset <reviewed dataset path> \
  --require-contract --require-integrity --require-results \
  --output-dir artifacts/benchmarks
```

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
