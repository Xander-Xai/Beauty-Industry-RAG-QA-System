# RAG evaluation readiness

This record states, for every metric the retrieval/answer evaluation is meant to
produce, whether it is measured, partially measured, or blocked, and exactly what
is missing. It is written from the code and from runs executed in this
repository. No number here is fabricated; a metric that did not run is recorded
as `BLOCKED`, never as a placeholder.

Status vocabulary used below (evidence levels follow
[the evidence map](../evidence-map.md#classification-vocabulary)):

- `VERIFIED` — produced by a real run and the data contract holds.
- `PARTIAL` — the harness and part of the chain are real; a required asset is
  missing, so the metric is not asserted.
- `BLOCKED` — a required dependency (model weight, corpus, live endpoint) is
  absent here.
- `PENDING` — implemented, not attempted (no dependency checked yet).

## 1. Golden-set contract coverage (issue #86)

The v2 contract is `benchmarks/golden_set_contract.py`
(`golden-set-contract/v2`). A row is evidence only when it carries a stable
`sample_id`, a real `(doc_id, chunk_id)` per annotated passage in a named
`corpus_version`, an explicit `visual_required` boolean, a `complexity_label`,
and recorded annotation provenance (`annotator` / `method` / `annotated_at` /
`source` / `review_status` / `reviewed_by` / `reviewed_at`). A row whose
`review_status` is not `REVIEWED` is `INVALID` — an unreviewed label, including an
LLM proposal, cannot be scored.

Measured against the dataset at `6d2576e` (SHA-256
`36cdaf4452a573eb77856c7c406ac091898f9895c2d7b5cd034348f2c4aac7df`):

| Contract field | Coverage |
|---|---|
| `question` | 301 / 301 |
| `sample_id` | 0 / 301 |
| `annotations` (`doc_id` + `chunk_id`) | 0 / 301 |
| `corpus_version` | 0 / 301 |
| `visual_required` | 0 / 301 |
| `complexity_label` | 0 / 301 |
| annotation provenance | 0 / 301 |
| **contract-valid rows** | **0 / 301** |

The validator is fail-closed. Measured on the committed dataset
(`sha256 36cdaf4452a573eb77856c7c406ac091898f9895c2d7b5cd034348f2c4aac7df`,
301 rows, 0 contract-valid, **exit 1**):

```text
python3 scripts/validation/validate_golden_set_contract.py --dataset tests/evaluation/golden_set.jsonl
# -> total: 301 / valid: 0 / invalid: 301
# -> "301/301 samples are INVALID ...", exit 1
```

The benchmark CLI refuses to run on a non-contract dataset when asked
(**exit 2**, same `0/301`):

```text
python3 -m benchmarks.retrieval_benchmark --require-contract --dataset tests/evaluation/golden_set.jsonl ...
# -> "dataset contract error: 0/301 samples satisfy the golden-set-contract/v2 contract ... refusing to score", exit 2
```

Both numbers describe the **whole dataset**. A run scoped with `--limit N`
reports only its own denominator (`--limit 2` prints `0/2`) — that is a
property of the selected subset, not of the dataset, and must never be quoted
as the dataset's state. `tests/validation/test_golden_set_evidence_docs.py`
re-measures these figures and fails if any document drifts from them.

Every artifact also records `dataset_contract`, `attributable` and
`synthetic_retriever`, and `results_are_benchmark` is
`any_results AND attributable AND NOT synthetic_retriever`, so neither
normalized-exact-text matching nor a scripted fixture ranking can be published
as a benchmark result. `synthetic_retriever` is derived from what the run
actually did (`summary["any_synthetic"]`), not asserted by the CLI: it used to be
hardcoded `false`, which let an injected fixture ranking carry
`results_are_benchmark: true`.

Identifier resolution against a real index is implemented
(`--resolve-qdrant` / `--resolve-es`, existence-only via
`qdrant_resolver` / `elasticsearch_resolver`).

Corpus correspondence is now **measured** rather than assumed.
`benchmarks/corpus.py` fingerprints the configured Elasticsearch index and Qdrant
collection (document/point count plus an order-independent content SHA-256) and
reports what fraction of the golden passages they actually contain;
`probe_corpus` blocks on that measurement instead of on a constant. Every
artifact records the result under `metadata.json → corpus`, including blocked
runs. Measured on this host: **0 of 60** sampled golden passages were found in
the live Qdrant collections (262 distinct passages exist), and the configured
Elasticsearch endpoint was unreachable. Building an index from the golden
passages themselves would be self-referential and is refused by design — the
probe is read-only by construction.

**Status: `BLOCKED` (evaluation toolchain `VERIFIED_CODE` / repository vocabulary
`REPO_VERIFIED`; Golden Set data unusable for official retrieval-quality
scoring).**

The distinction matters: the contract validator, its fail-closed gate and the
benchmark publication gate are implemented, executed and test-covered
(`benchmarks/golden_set_contract.py`, `benchmarks/annotation.py`,
`tests/evaluation/test_golden_set_contract.py`,
`tests/evaluation/test_annotation_lifecycle.py`,
`tests/benchmark/test_publication_gate.py`, `tests/benchmark/test_corpus.py`,
`tests/benchmark/test_multi_relevant_metrics.py`,
`tests/integration/test_retrieval_benchmark_blocking.py`,
`tests/runtime/test_live_corpus_probe.py`). The *data* it judges is not usable
for a retrieval-quality score. A working validator over an unusable dataset is
not a partial retrieval result — it is a correct refusal.

## 2. Retrieval ablation A–E

The five configurations exist in `benchmarks/backends.py`:
`dense`, `bm25`, `hybrid_rrf`, `hybrid_rrf_biencoder`,
`hybrid_rrf_biencoder_crossencoder`. Live probe result in this environment
(`python3 -m benchmarks.retrieval_benchmark --list-configs`):

| Config | Required backends | Blocked by |
|---|---|---|
| A `dense` | Qdrant + BGE weights + corpus | corpus, BGE weights, service |
| B `bm25` | Elasticsearch + corpus | corpus, service |
| C `hybrid_rrf` | A + B + RRF | corpus, services |
| D `hybrid_rrf_biencoder` | C + BiEncoder | corpus, services, BGE weights |
| E `hybrid_rrf_biencoder_crossencoder` | D + CrossEncoder | corpus, services, BGE weights, CrossEncoder |

All five report `BLOCKED`. The unconditional blockers are:

1. **No corpus contains the golden passages.** Measured, not assumed: the live
   corpus holds **0 of 60** sampled golden passages, so a recall measured
   against it would fall toward zero for reasons that have nothing to do with
   retrieval quality. Building a corpus from the golden set itself is refused.
2. **No model weights and no network to fetch them.** `models/bge-base-zh-v1.5`
   and the two configured CrossEncoder directories are absent, and
   `huggingface.co` is unreachable from this host (pypi is reachable).

Consequently **Hit@5 / Recall@5 / MRR@10 / NDCG@10 per configuration are not
produced.** The harness will not record a CrossEncoder fallback as a two-stage
rerank result: `probe_crossencoder` is hardwired unavailable until a validated
weights path is wired, so config E can only be `BLOCKED`, never `EXECUTED` with a
fallback.

A real run reproduces this. Executed on this host over all 301 samples:

```text
python3 -m benchmarks.retrieval_benchmark --configs bm25,dense,hybrid_rrf \
  --dataset tests/evaluation/golden_set.jsonl --output-dir /tmp/bench_out \
  --allow-dirty --require-results
# -> executed: none
# -> bm25: BLOCKED — unavailable backends: bm25=service_unreachable;
#            corpus=corpus_does_not_contain_ground_truth
# -> dense: BLOCKED — ...
# -> hybrid_rrf: BLOCKED — ...
# exit 5
```

The artifact records `results_are_benchmark: false`, `attributable: false`,
`sample_count: 301` and the full corpus evidence, including the per-store
fingerprints and the 0/60 correspondence ratio.

**Status: `BLOCKED` (harness `REPO_VERIFIED`, results `PENDING`).**

## 3. Storage / RBAC / epoch regression (real dependencies)

Unlike the model-dependent metrics, this ran against real services on a single
host:

- **Qdrant v1.12.0** — epoch-versioned point ids, payload (status/epoch) filters,
  doc-level RBAC re-filter before fusion, text+image `rrf_fusion` merge,
  Qdrant-down degradation: 20/20 checks passed
  ([record](qdrant-local-real-validation.md)). Two identities with distinct
  `role_mask` / `dept_mask` produced different visible sets; no cross-permission
  result survived.
- **Elasticsearch 8.11 (authenticated)** — anonymous/wrong-credential rejection,
  writer mapping + `search_after` pagination, online BM25, bad-credential
  degradation.
- **Redis** — cache key changes with both the knowledge epoch and the permission
  fingerprint; no cross-identity cache hit.

**Status: `VERIFIED` (`LOCAL_REAL_VALIDATION`, single host; not cluster/HA).**
This confirms the **RBAC 越权泄露 = 0** on the tested paths; the assertion is a
real-store regression, not a load measurement.

## 4. Answer-level evaluation

The labeled set `tests/evaluation/answer_eval_set.jsonl` (12 samples, human
labels) covers six categories: sufficient evidence, insufficient evidence,
conflicting regulation, fabricated-source trap, unauthorized document,
image/text conflict. The scorer `tests/evaluation/answer_eval.py` computes:

- `answer_evidence_support_rate` (strict and inclusive)
- `unsupported_answer_rate` (高风险无依据回答率)
- `refusal_recall` (应拒答时的拒答召回率)
- `false_refusal_rate` (错误拒答率)
- `rbac_leak_count`
- `high_risk_unsupported_rate`

Each record keeps the raw answer, cited evidence, gate decision and the human
judgment together. A record without a named human reviewer and an
`evidence_support` label is rejected, so an LLM self-score cannot stand in for a
human label; `tests/evaluation/test_answer_eval.py` pins the arithmetic and the
fail-closed shape rules.

**Blocked part:** producing the answers requires the live pipeline, which needs
the generation model endpoints and the retrieval corpus. Both are absent here, so
no answer-quality percentage is claimed.

**Status: harness `REPO_VERIFIED`; answer-quality results `BLOCKED`.**

## 5. Performance and QPS

`tests/load/locustfile.py` and `benchmarks/performance.py` exist, but a run needs
a reachable canonical API plus live retrieval and model endpoints.

**Status: `BLOCKED`.** No QPS / P50 / P95 / P99 / TTFT value is claimed.

## 6. Environment checklist to promote the blocked metrics

1. **Models** — place real weights at the configured paths, or set
   `EXTERNAL_MODEL_ASSET_REQUIRED`-aware overrides:
   - `models/bge-base-zh-v1.5` (BiEncoder / dense embedding)
   - `models/cross-encoder-law`, `models/cross-encoder-base` (two-stage rerank)
   - `models/clip-vit-base-patch16` (visual path)

   Once present, the revision is recorded automatically: `model_identity()`
   resolves a marker file, else a content fingerprint, and distinguishes "not
   installed" from "unknown".
2. **Corpus** — build and seal one epoch with the offline pipeline from a source
   set that is **independent** of the golden passages, then register it as the
   benchmark corpus and map each golden passage to a real `(doc_id, chunk_id)`
   and that epoch's `corpus_version`. Confirm the mapping with
   `RUN_RUNTIME_VALIDATION=1 python3 -m pytest tests/runtime/test_live_corpus_probe.py`,
   which reports the measured correspondence ratio and the store fingerprints.
3. **Golden set v2** — fill the contract fields in
   `tests/evaluation/golden_set_v2/annotations_v1.jsonl`, mapping **each** of the
   1081 ground-truth passages to its own `(doc_id, chunk_id)` (a single
   row-level identifier with several passages is rejected), record
   `corpus_sha256`, set `review_status: REVIEWED` with a distinct `reviewed_by`,
   then pass `validate_golden_set_contract.py` and re-run with
   `--require-contract`. Audit with
   `scripts/validation/audit_annotations.py`; the outstanding work is listed in
   `tests/evaluation/golden_set_v2/HUMAN_ANNOTATION_BACKLOG.md`.
4. **Generation endpoints** — a reachable vLLM (or compatible) endpoint for
   answer-level and performance runs.
5. **Load tooling** — a declared workload and a reachable API; then run
   `tests/load/locustfile.py` and write the performance artifact.

## 7. Status summary

| Metric | Status |
|---|---|
| Golden-set contract coverage | `BLOCKED` — 0/301 valid (toolchain `VERIFIED_CODE`; data unusable for official scoring) |
| Golden-set v2 annotation records | `BLOCKED` — 0 reviewed records; the annotation pass has not been performed |
| Corpus fingerprint + golden-set correspondence | `BLOCKED` — measured 0/60 sampled passages present in the live corpus |
| Hit@5 / Recall@5 / MRR@10 / NDCG@10 ablation A–E | `BLOCKED` — corpus correspondence 0%, no weights |
| Real Qdrant store / epoch / RBAC isolation | `VERIFIED` — 20/20 real-server checks |
| Real authenticated Elasticsearch path | `VERIFIED` — authenticated ES 8.11 run |
| Cache epoch / permission isolation | `VERIFIED` — real Redis key round-trip |
| Answer evidence support / unsupported / refusal metrics | `BLOCKED` — harness `REPO_VERIFIED` |
| QPS / P50 / P95 / P99 / TTFT | `BLOCKED` — no live stack |
| RAGAS quality score | `PENDING` — no evaluator credential |

No `VERIFIED` row depends on a fabricated corpus or a stand-in model. Every
`BLOCKED` row names the exact missing asset above. The annotation rows are
`BLOCKED` because a human has not done the work — not because a tool failed;
`tests/evaluation/golden_set_v2/HUMAN_ANNOTATION_BACKLOG.md` enumerates what is
outstanding.
