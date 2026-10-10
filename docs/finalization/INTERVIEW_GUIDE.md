# Interview guide

A walkthrough of this repository for a technical interview. Every claim here is
backed by a path in [`EVIDENCE_MANIFEST.md`](EVIDENCE_MANIFEST.md). Two honesty
rules govern the whole document:

1. **No metric is quoted.** The retrieval numbers are `BLOCKED` — no corpus, no
   weights — so nothing here says "Hit@5 was X".
2. **The open-source work is not back-dated.** This repository is post-employment
   open-source work. It is not a description of any employer's production system,
   and the single-GPU host here is not a dual-A5000 production deployment.

The code discussed here is on `main` as `f3d0305` (PR #92, squash-merged); the
enumerated failure cases below are the closes in [`RESUME_CLAIMS.md`](RESUME_CLAIMS.md)
bucket D.

## 30-second introduction

> "This is a beauty-industry RAG question-answering system — regulatory,
> ingredient, formulation and label questions. The interesting part is not the
> model; it's the honesty layer. The retrieval benchmark refuses to publish a
> number unless the dataset has stable document identity, a per-record human
> review state, and a corpus that actually contains its own ground truth. The
> Evidence Gate is fail-closed. And I hardened the audit trail so it can't report
> a rerank result that never executed. When the assets aren't present, every
> metric reports BLOCKED rather than a fabricated value."

## 90-second introduction

> "The pipeline is a FastAPI monolith: query rewrite → parallel recall over BM25
> and dense → RRF fusion → BiEncoder → CrossEncoder ensemble → Evidence Gate →
> generation → Answer Gate, with RBAC masks on every retrieval path and a
> two-level cache partitioned by role and department.
>
> What I'd point at is the evaluation-integrity work. The committed golden set has
> 301 Q&A pairs but zero stable document ids and no review state. I built a data
> contract that refuses to score a row unless every annotated passage resolves to a
> real `doc_id::chunk_id` in a named corpus epoch, and a review lifecycle where an
> LLM proposal stays `DRAFT_UNVERIFIED` until a named person reviews it.
>
> In the final closeout I found and fixed six defects where the system could
> publish something untrue — the worst was that the rerank validation harness
> reported `is_reranking_evidence: true` after a *total* weight-load failure, and
> its 'real vs fallback' comparison was mathematically incapable of detecting a
> reordering. And I closed a cross-principal session leak that sat entirely
> outside the RBAC machinery.
>
> The honest summary: the engineering is done and tested; the metrics are
> NOT_VERIFIED because I don't have the corpus or the CrossEncoder weights, and I
> won't invent them."

## Full architecture (one screen)

```text
HTTP  POST /api/query
  → middleware: CORS, request-log, audit
  → require_identity  (JWT or AUTH_DEV_MODE headers; one validator, fail-closed)
  → OnlineRAGPipeline.process(ctx):
      ①  cache lookup (L1 in-proc / L2 Redis, keyed by role+dept)      ── early return on hit
      ②  query rewrite + complexity routing
      ③  KV admission                                                  ── early return on reject
      ④  parallel recall: BM25(ES) | dense(Qdrant) | rewrite | CLIP
             └─ RBAC filter per channel, RRF fuse, ES fallback, RBAC guard again
      ⑤  BiEncoder wide rerank → CrossEncoder ensemble rerank (GPU)
      ⑥  EvidenceGate   (fail-closed; no weights ⇒ ceiling 0.40 < 0.55 ⇒ refuse)
      ⑦  vLLM generation (bounded, sanitised errors)
      ⑧  AnswerGate     (faithfulness + NLI)
      ⑨  output → cache write → session update
```

RBAC is a **post-retrieval Python filter re-applied per channel and again after
fusion** (`retrieval/parallel_recall.py`), plus an ES-side pushdown and a physical
cache-key partition by `role_mask:dept_mask`.

## Five technical follow-ups (and the answers)

1. **Why `doc_id::chunk_id` and not `doc_id`?**
   Because a document is chunked. If the relevance key were `doc_id`, a retriever
   returning *any one* chunk of a document would score a hit for *every* annotated
   chunk of it — inflating recall. The key qualifies the chunk with its document,
   and the *same* function is used on both sides of the comparison, so a mismatch
   fails loudly rather than silently reading zero.
   *(See `benchmarks/relevance.py::relevance_key`.)*

2. **How do you stop an LLM-written label from scoring as ground truth?**
   `review_status` is a required field with **no default**, and only `REVIEWED` is
   scorable. Promotion requires a named `reviewed_by` (≠ `annotator`), an ISO
   date, and for an `llm_candidate` a `promoted_by`. The scoring gate
   (`require_attributable`) runs before any percentage is computed.
   *(See `benchmarks/annotation.py`.)*

3. **What stops the benchmark from scoring a mock retriever?**
   The CLI wires no retriever; a synthetic one can only be injected by tests. The
   run metadata sets `results_are_benchmark = any_results AND attributable AND
   NOT synthetic_retriever`. A blocked run, a non-attributable dataset, and a
   fixture run all fail that conjunction.
   *(See `benchmarks/retrieval_benchmark.py`.)*

4. **The Evidence Gate is fail-closed — how do you know it's not just refusing
   everything?**
   There are explicit positive controls that a well-evidenced answer **passes**:
   `tests/test_gate_degradation_scenarios.py` asserts `passed is True` for a
   faithful answer and `decision in ("pass","enhanced_generate")` for strong
   evidence. A negative-only test suite would be satisfied by a brick.
   *(See `tests/test_gate_degradation_scenarios.py`.)*

5. **A single-GPU host vs a dual-A5000 claim — how is that kept honest?**
   The topology gate compares the visible GPU **count** to the configured count
   and reports `PENDING` when short; `hardware_info()` records the actual device
   names; and the production-readiness docs label the dual-A5000 figures as
   `HISTORICAL_PRODUCTION`, not measurements from this repo. The gate does **not**
   compare GPU *models*, which is listed as a remaining risk.
   *(See `router/gpu_gate.py`, `core/run_report.py::hardware_info`.)*

## Three real failure cases and the fix process

### Case 1 — the rerank harness that manufactured its own evidence

**Symptom.** While auditing `retrieval/rerank_validation.py` I read
`run_comparison`: it called `run_smoke(pairs)` and then, without inspecting
`smoke.status`, returned `status="OK"`, `provenance="cross_encoder"`. Since
`is_reranking_evidence` is `status == "OK" and provenance == "cross_encoder"`, a
**total weight-load failure** would publish a rerank-evidence artifact.

**Reproduce.** I forced `_load_ranker` to raise and called `run_comparison`.
Output: `run_smoke().status = FAILED` but `run_comparison().status = OK`,
`is_reranking_evidence = True`, `comparison.cross_encoder = null`.

**Deeper problem.** Investigating, I found the "ranking" could never rank: the
scorer did `model.predict([...]).mean()`, averaging the *entire batch* into one
number, so every document tied and the sort was a no-op. Worse, `SMOKE_PAIRS`
always put the correct answer at index 0, so both the "real" and "fallback" sides
scored `1.0` structurally and the delta was pinned at `0.0`. The harness could
not have detected a reranker that reorders everything wrongly.

**Fix.** Propagate the smoke status; score each pair individually and reject a
misaligned vector instead of padding with `0.0`; rotate the candidate presentation
so the positive is never first. Two new tests plus a positive control lock it in.

**Lesson.** A metric that is *structurally* constant is worse than no metric — it
looks like evidence and cannot be falsified.

### Case 2 — ground truth that silently shrank

**Symptom.** Multi-passage ground truth was collapsing to a single item per query,
inflating Recall@5 from `found / passages` to a guaranteed `1.0`.

**Fix.** `relevant_items_from_annotations` keeps every annotation; a row-level id
shared across several passages is refused rather than guessed
(`AmbiguousRelevanceIdentityError`). The 1081 ground-truth passages across 301
rows span 262 distinct ones — collapsing them was a real distortion, not a
theoretical one.

**Lesson.** In retrieval evaluation the denominator is the thing that gets
quietly broken.

### Case 3 — a permission leak outside the RBAC machinery

**Symptom.** `SessionState` was keyed only on a caller-supplied `session_id`.
`/api/chat` returned `session_state.dialog_rounds` and `/api/dialog_history`
returned rounds plus `locked_doc_ids`. Nothing bound the session to a user, so one
principal could read another's questions **and the answers generated from
documents that principal alone was authorised to see**. Document-level RBAC never
saw session state.

**Fix.** Owner-scoped sessions: an `owner_id` on the record, owner-namespaced
memory and Redis keys, and every online call site passing the verified
`identity.user_id`. A foreign owner's id yields a new **empty** session —
fail-closed without changing the API contract.

**Lesson.** Authorization reviews that stop at the retrieval layer miss the
stateful surfaces. Grep your session/cache keys for a user axis.

## What I would say if asked for a number

"I can't give you one honestly. `--list-configs` shows all four retrieval
configurations `BLOCKED`, because this host has neither an independent corpus nor
the CrossEncoder weights, and the golden set has no human-reviewed labels. What I
can show you is the machinery that *refuses* to produce a number under those
conditions — and the tests that prove a real run would be reported as real."
