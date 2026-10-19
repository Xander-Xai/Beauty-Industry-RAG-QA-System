# Benchmark artifacts

Current classification, stated before anything else:

- Retrieval benchmark **framework**: `REPO_VERIFIED` (metrics, provenance,
  readiness probing and this artifact contract are implemented and test-covered).
- Retrieval benchmark **result**: `LOCAL_REAL_VALIDATION` for the **Real Corpus V1
  BM25** scope; `PENDING` for Dense / Hybrid, for the legacy 301-row dataset and
  for every other evaluation family in this repository.

Two evaluation scopes exist and their results must not be conflated. The
distinction is repeated wherever a claim is made below.

## Scope A — Real Corpus V1: complete

The first retrieval result in this repository that cleared **every** publication
gate.

| | |
|---|---|
| Golden set | 27 records, all `REVIEWED`, human-signed by `Xander-Xai` on 2026-10-19 |
| Golden Set Contract v2 | **27/27 valid**, 0 invalid, 0 integrity errors |
| Sources | both approved through the TrustRegistry by `Xander-Xai`, each bound to its own file byte hash |
| Indexed documents | **212/212** passed per-document source verification (`APPROVED_EXTERNAL`), 0 `PENDING_REVIEW`, 0 `UNTRUSTED` |
| Gold chunks | **21/21** resolved in the index and textually identical to the corpus |
| Corpus correspondence | `verdict = full`, ratio 1.0 |
| Execution | real Elasticsearch 8.11.0, real `BM25Retriever`, no fixture, clean working tree |
| Gate | `results_are_benchmark=true` · `publishable=true` · `source_approval_verified=true` · `provenance_verified=true` · `attributable=true` · `synthetic_retriever=false` |

Recall@10, same 27 signed records, same corpus, same epoch — only the declared
analyzer differs:

| analyzer | Recall@10 | note |
|---|---:|---|
| `standard` (index default) | **0.7037** | the formal baseline |
| `smartcn` | **0.9259** | fixes 6 of the 8 top-10 misses, 0 regressions |
| `ik_smart` | **0.9630** | fixes 7 of the 8 top-10 misses, 0 regressions |

Full metric sets, latency, the per-sample breakdown and the limits that must
travel with these numbers are in
[`docs/validation/real-v1-approved-bm25-run.md`](../../docs/validation/real-v1-approved-bm25-run.md)
and [`docs/validation/chinese-analyzer-ab.md`](../../docs/validation/chinese-analyzer-ab.md).

**Evidence is recomputable, not asserted.** Per-sample gold chunk ids, retrieved
chunk ids and first-relevant ranks are committed under
[`docs/validation/evidence/`](../../docs/validation/evidence/):

- [`real-v1-approved-bm25-evidence.json`](../../docs/validation/evidence/real-v1-approved-bm25-evidence.json)
- [`real-v1-approved-bm25-per-sample.jsonl`](../../docs/validation/evidence/real-v1-approved-bm25-per-sample.jsonl)

A third party can re-derive every published metric with no Elasticsearch, no
model weights and no network access:

```bash
python3 scripts/validation/verify_benchmark_evidence.py
# All published metrics reproduced from committed evidence.
```

**This stage's baseline is frozen.** Any later model, data, index or golden-set
change must be a **new experiment version** and must not overwrite it — see
[the freeze declaration](../../docs/validation/real-v1-approved-bm25-run.md#9-阶段基线冻结声明).

## Scope B — still open

Not completed, and not completed by Scope A:

- the **legacy 301-row dataset**: no stable identifiers, so retrieval metrics on
  it are still not attributable to document identity; its annotation and
  identity binding remain open (issue #86);
- **Dense / BGE** and **Hybrid (BM25 + BGE + RRF)** real evaluation — the BGE
  weights are absent, and **no substitute or mock number was produced**;
- **Answer Eval** real observation data, its CLI entry point and its join to the
  retrieval golden set;
- **RAGAS** quality score;
- production deployment, and the regulatory-applicability control that would let
  a 2021 snapshot answer a *current* regulation question.

No scope-B item may be closed by citing a Scope-A number.

## Scope B — the legacy 301-row dataset, in detail

The 301-row dataset is a **different dataset** from the 27-record V1 set, and
its state is unchanged by any of the above. Measured against
`tests/evaluation/golden_set.jsonl`:

* `golden-set-contract/v2` is **0/301 valid** — no stable `doc_id` / `chunk_id`,
  no `visual_required`, no `complexity` label;
* relevance therefore falls back to Level 2 normalized exact-text matching, so
  its metrics are not attributable to document identity;
* it has no independent corpus, so a run against it stays `BLOCKED` on
  `corpus_does_not_contain_ground_truth`.

Its own documentation keeps this scoped and measured, and has not been rewritten
to look finished: [`docs/benchmark-data-quality.md`](../../docs/benchmark-data-quality.md)
and [`docs/validation/rag-eval-readiness.md`](../../docs/validation/rag-eval-readiness.md).

Indexing the ground truth itself would make every configuration score recall
1.0 by construction, so the corpus probe remains read-only.

## Historical: the pre-review state (PR #111 – #113)

Kept so the sequence is legible. **These statements describe a past state and
must not be read as current.** The candidate workspace was
`DRAFT_UNVERIFIED`, `golden-set-contract/v2` measured 0/27, and the exploratory
run against it recorded `results_are_benchmark=false` and `attributable=false`.
That run was a wiring check, not a benchmark.

* [`docs/validation/real-corpus-v1-pre-review.md`](../../docs/validation/real-corpus-v1-pre-review.md)
  — the PR #111 review: index-rebuild safety, the corrected source-trust
  classification, the review worksheets and the exploratory-BM25 boundary.
* [`docs/validation/real-v1-bm25-run.md`](../../docs/validation/real-v1-bm25-run.md)
  — the PR #113 run, whose source approval was only 1/2. Its conclusion is
  **retained unaltered**; under the current gate that mixed-provenance index
  cannot be published, which
  `tests/benchmark/test_source_approval_gate.py::test_pr113_mixed_index_result_is_not_publishable`
  reproduces end to end.

The historical commands below are kept for provenance only. **They are not
directly usable for a current publishable run**: they predate the source-approval
gate and they do not pass `--require-source-approval`.

```bash
# Historical (pre-review): no executor, no gates — exploratory only.
python -m benchmarks.retrieval_benchmark --config bm25 --limit 5
```

## The current formal command

The publication gate is the conjunction of five facts, none of which the caller
may assert: a configuration executed, the golden set is attributable, the
retriever was not a fixture, the working tree is attributable, and **every
document in the queried Elasticsearch index carries an attributed reviewer's
approval of the exact bytes reviewed**
([`benchmarks/source_approval.py`](../../benchmarks/source_approval.py)). A run
that fails any of them records `publishable: false` and the blocking reasons.

```bash
python -m benchmarks.retrieval_benchmark \
  --executor real --config bm25 \
  --dataset tests/evaluation/golden_set_v1/reviewed/annotations_v1_reviewed.jsonl \
  --top-k 10 \
  --require-contract --require-integrity --require-results \
  --require-source-approval \
  --output-dir artifacts/benchmarks
```

`--require-source-approval` is not optional for a publishable run: it turns the
source-approval verdict into a process exit code, and the artifact fields
`source_approval_verified` / `provenance_verified` / `publishable` /
`publication_blockers` are always recorded whether or not the flag is passed.

To see what can actually execute in the current environment:

```bash
python -m benchmarks.retrieval_benchmark --list-configs
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
* A separate fail-closed check refuses to query an index that does not hold the
  configured `knowledge_version_epoch`
  ([`retrieval/bm25_retriever.py`](../../retrieval/bm25_retriever.py)). Without
  it, a config/index epoch mismatch made every query return an empty ranking —
  indistinguishable from "nothing relevant exists".

## Runtime output

Runtime output of the reproducible retrieval benchmark lives here:

```text
artifacts/benchmarks/<run-id>/
├── metadata.json          # run_id, git_sha, dataset/config sha256, sample count,
│                          # dataset_contract, attributable, synthetic_retriever,
│                          # results_are_benchmark, source_approval,
│                          # provenance_verified, publishable,
│                          # publication_blockers, and `corpus` (store fingerprints
│                          # + measured golden-passage correspondence)
├── environment.json       # python/torch/cuda/service versions, plus each model's
│                          # path/revision (or present=false when not installed)
├── retrieval_metrics.json # per-config metrics and BLOCKED/EXECUTED status
├── latency_metrics.json   # per-stage p50/p90/p95/p99 (null when a stage never ran)
├── per_query_results.jsonl# one row per query per executed config
├── report.md              # human-readable run report
└── comparison.md          # only when >= 2 configs executed
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
* A published number is a **local, single-node** measurement over a **2021
  historical corpus**. It is not a current-regulation accuracy figure and not a
  production latency or throughput figure. The scope boundary is stated in
  [`docs/validation/production-index-governance.md`](../../docs/validation/production-index-governance.md).

## Version control

Generated runs are intentionally not committed; `.gitignore` excludes
`artifacts/benchmarks/*` while keeping this README. What is committed instead is
the recomputable evidence package under `docs/validation/evidence/`, which
carries the per-sample data needed to re-derive the published metrics without a
cluster. If a benchmark result should become durable evidence in the raw
six-file form, promote the specific run directory deliberately and record
`git_sha` plus the dataset/config hashes alongside it.
