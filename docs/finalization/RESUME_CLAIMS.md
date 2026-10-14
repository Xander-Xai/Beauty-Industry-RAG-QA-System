# Resume claims

What may be written on a resume from this work, what must be framed as
personal independent reproduction, and what must never be claimed.

**Code commit under test:** `61be8a9`; the closeout documents are committed on top. The integration was squash-merged into `main` as `f3d03054d1f108d9ce82abb4499f65a4e0967e46`; the verification results below are for `61be8a9` (code unchanged by the doc commits). · 2026-10-10 UTC. Subject to the same evidence rules as
[`EVIDENCE_MANIFEST.md`](EVIDENCE_MANIFEST.md).

## 0. The boundary that governs everything

This public repository is the maintainer's **post-employment independent reproduction** of the core technical approach of the company RAG project they previously participated in, with limited subsequent engineering improvements. It is **not** copied employer production source, an official employer repository, or a one-to-one production mirror. The honest framing is:

> "After leaving, I independently reproduced the core RAG system design from my enterprise project experience and made some additional engineering improvements. This public code and its tests are separate from the former employer's production system."

The single-GPU host (1× RTX 5060 Ti) used for the local runs is **not** a
dual-RTX-A5000 production deployment. Where a resume implies production scale, it
is wrong.

## The four buckets — never mix them

Every resume line must belong to exactly one bucket, and the buckets must not be
blended into a single sentence.

| bucket | what belongs here | rule |
|---|---|---|
| **A. Enterprise historical production facts** | Anything about a previous employer's *live* system: scale, traffic, the dual-RTX-A5000 topology, any production number. | **Not in this repository.** Classified `HISTORICAL_PRODUCTION` in the docs; usable only as background context, never as this repo's measurement. |
| **B. Post-employment independent reproduction & limited extensions** | Personally reproduced core pipeline plus subsequent engineering improvements, evaluation-integrity work and local real-dependency validations. | Each claim needs a path in [`EVIDENCE_MANIFEST.md`](EVIDENCE_MANIFEST.md); always framed as post-employment personal reproduction, not employer code. |
| **C. Retrieval-quality metrics that remain unprovable here** | Hit@K / Recall@K / NDCG@10 / MRR@10, RAGAS/faithfulness, QPS / P95 / throughput. | **Never quote a value.** No corpus, no weights ⇒ `BLOCKED`; no load test ⇒ `NOT_VERIFIED`. |
| **D. Currently engineering-verified capabilities** | Fail-closed gates, the golden-set data contract and review lifecycle, the RBAC/session controls, the run-report outcomes, the contract tests. | `PASS_CODE` / `PASS_CI`; describes *engineering*, not a measured result. |

## 1. Allowed on a resume (true, verifiable, correctly framed)

These are safe because they describe *engineering done in this repository* and
are backed by tests in [`EVIDENCE_MANIFEST.md`](EVIDENCE_MANIFEST.md):

1. **Designed a fail-closed retrieval-evaluation data contract** that refuses to
   score a golden-set row unless every annotated passage resolves to a real
   `doc_id::chunk_id` in a named corpus epoch and carries an explicit human review
   state. *(Safe: describes the design, not a result.)*
2. **Built a review lifecycle** in which an LLM-proposed label stays
   `DRAFT_UNVERIFIED` and can only become scorable through a named, dated,
   audited human promotion with self-review rejected.
3. **Hardened a RAG audit trail** so it cannot report a rerank result that did
   not execute: found and fixed a path that published
   `is_reranking_evidence: true` after a total weight-load failure, and a
   comparison harness whose `.mean()` reduction made a reorder impossible to
   detect.
4. **Closed a cross-principal session-state leak** (`/api/chat`,
   `/api/dialog_history`) that sat outside the role/department RBAC machinery, by
   owner-scoping session storage.
5. **Implemented layered retrieval authorization**: per-channel filtering, a
   post-fusion guard, ES-side pushdown, and a cache physically partitioned by
   `role_mask:dept_mask`.
6. **Fixed an HTTP status-contract defect** where a documented 503 overload
   response was surfacing as a 500.

## 2. Must be framed as personal independent reproduction / post-employment

- The public repository as a whole is a personal, independent reproduction of the core approach, with some later improvements; it is not an export of the enterprise system.
- The "301-sample golden set", "v2 data contract", "annotation work package" —
  these are this repository's artifacts, not an employer's.
- Any statement of scale or deployment (single GPU, no live traffic).
- Any performance claim from `tests/load/` fixtures — they were not run.

## 3. Must NOT be claimed (no evidence)

### Metrics — never quote any of these

| forbidden claim | why |
|---|---|
| Hit@K / Recall@K / MRR / NDCG of any value | No corpus, no weights → `BLOCKED` |
| RAGAS / faithfulness / context-precision scores | Evaluator gated, never run here |
| QPS / P95 / throughput | No load test executed |
| "CrossEncoder improved ranking by X" | `top1_rate_delta` is structurally `0.0` without weights; and the fix that makes it a real measurement has never run against real weights |
| "Recall improved from A to B" | No before/after measurement exists |

### Capability / provenance claims

| forbidden claim | why |
|---|---|
| "Production-validated on dual RTX A5000" | Host is 1× RTX 5060 Ti; the gate compares count, not model |
| "Delivered in 2024–2025 at <employer>" | Post-employment open-source work |
| "Served N users / Q queries" | No real deployment; no such measurement |
| "Regulation-specialised CrossEncoder trained/deployed" | No weights exist in this repo; the smoke harness is `PENDING` |
| "The microservice path is production-hardened" | `api-gateway/` has no Answer Gate and a fail-open Evidence Gate; `NOT_VERIFIED` |
| "The system answers questions correctly" | It is fail-closed by design without weights; correctness is `NOT_VERIFIED` |

## 4. Three engineering bullets that survive scrutiny

Each is a concrete defect found and fixed, with a test — the safest kind of
resume line because it is a *story*, not a metric:

- **"Found and fixed a rerank validation harness that reported
  `is_reranking_evidence: true` after a total model-load failure, and whose
  `.mean()` reduction made its real-vs-fallback comparison mathematically
  incapable of detecting a reordering (structurally `delta = 0.0`). Added
  positive controls so the fix could not degrade into a blanket refusal."**

- **"Closed a cross-principal permission leak: dialog sessions were keyed only on
  a caller-supplied `session_id`, letting one principal read another's questions
  and the answers synthesised from documents their role/department alone could
  access. Re-keyed sessions on the verified identity, fail-closed, with no API
  change."**

- **"Built a retrieval-evaluation data contract and human-review lifecycle that
  refuses to score a golden-set row without stable `doc_id::chunk_id` identity, a
  bound corpus hash, and a named human review — so an LLM-generated label can
  never be published as ground truth."**

## 5. One-line summary that is true

> "After my enterprise RAG project experience, I independently reproduced its core design in a separate public codebase and made some further engineering improvements. The current repository focuses on evaluation integrity: fail-closed quality gates, human-review data contracts and honest audit trails. It cannot establish the former company's production performance."
