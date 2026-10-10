# Production readiness

Single answer to one question: **what state is each capability in, and what would
still be required before this could take real traffic?**

This document is the detailed table behind the condensed
[Production Readiness](../README.md#production-readiness) section of the
top-level README. It owns the per-capability status; it introduces no capability
and promotes no evidence. For the classification vocabulary see
[Evidence map](evidence-map.md#classification-vocabulary); for the architecture
contract see [Architecture baseline](architecture-baseline.md); for the full
capability/evidence grading see [Evidence map](evidence-map.md).

## How to read this document

Three columns, deliberately separate:

- **Code status** — does the implementation exist in this repository, and is it
  covered by collected deterministic tests or CI? `REPO_VERIFIED` means yes.
- **Real-environment status** — has it been exercised against a real external
  dependency? `LOCAL_REAL_VALIDATION` means on a single local host. Everything
  else is `PENDING` or `DESIGN_TARGET`.
- **Promotion criterion** — the specific artifact or environment run that would
  move it forward. Not "more testing"; a named thing.

A row may be `REPO_VERIFIED` in code status and `PENDING` in real-environment
status. That is the normal state of this repository and it is not a defect: it
is the difference between "implemented and covered" and "shown to work against
the real thing".

### Why the request path itself is `PENDING`

The five `LOCAL_REAL_VALIDATION` items listed in
[Architecture baseline → local real validation](architecture-baseline.md#可观测性与评测边界)
are **individual mechanisms**, each exercised against its own real dependency:
Redis session persistence, Redis cross-process login rate limiting, nginx
`TRUSTED_PROXIES` client-IP resolution, authenticated Elasticsearch 8.11, and an
authenticated Prometheus scrape.

None of them is a run of `/api/query` or `/api/chat` through the whole stack. In
that recorded run Qdrant was not exercised and the model stack was blocked, so
claiming the complete request path as `LOCAL_REAL_VALIDATION` would credit it
with evidence it does not have. The monolith path therefore stays
`REPO_VERIFIED` / `PENDING`; closing it requires #60
(`VAL-E2E-001`) plus `VAL-STORE-001` and `VAL-GPU-001`.

## Verified in code (no external dependency required)

These are implemented on the canonical online path and covered by deterministic
tests. They need no external asset to be correct, so the code status is the
whole story.

| Capability | Code status | Real-environment status | Notes |
|---|---|---|---|
| FastAPI monolith request path (`app.py`, `/api/query`, `/api/chat`, `/api/health`) | `REPO_VERIFIED` | `PENDING` (no recorded run of these endpoints through the full stack; see note) | Canonical deployment form. Docker Compose is the canonical form; `deploy/k8s/` is a second form, static-checked only |
| RS256 authentication, uint32 permission-mask contract, malformed-claim fail-closed | `REPO_VERIFIED` | `PENDING` for the full request path; the two mechanisms below are individually `LOCAL_REAL_VALIDATION` | HS256 `JWT_SECRET` remains an opt-in legacy fallback. Individually validated: nginx `TRUSTED_PROXIES` resolution, Redis-backed cross-process login rate limit |
| Layered RBAC: store-side pushdown + pre-fusion document filtering + L2 cache physical partitioning | `REPO_VERIFIED` | `PENDING` (no artifact against real Qdrant/ES with a real multi-role corpus) | Defence in depth, not a single control. Cross-role / cross-department / cross-cache-partition regression is in-process only (`tests/test_rbac_cross_identity.py`) and constructs no store client — see [Security regression coverage](security-regression-coverage.md) |
| Elasticsearch 8 BM25 sparse retrieval with Painless bitmask filtering | `REPO_VERIFIED` | `LOCAL_REAL_VALIDATION` (authenticated ES 8.11: writer mapping, `search_after`, online BM25) | |
| Dynamic 2–4 path recall selection driven by complexity and visual relevance | `REPO_VERIFIED` | `PENDING` | Selection logic is deterministic and tested; its measurable *benefit* is unmeasurable while `complexity` / `visual_required` are absent from the golden set (#86) |
| Two-stage rerank call chain (BiEncoder → Top 150 → CrossEncoder ensemble → Top 10) | `REPO_VERIFIED` (call chain + deterministic fallback) | `PENDING` (weights absent, so only the fallback path has ever run) | The second stage has never been exercised with real weights (#85 `VAL-RERANK-001`). A gated real-model entry point now exists — see [Rerank validation entry point](#rerank-validation-entry-point) |
| Evidence Gate and Answer Gate decisions, including regulation-conflict rejection | `REPO_VERIFIED` | `PENDING` | See [Fail-closed degradation](#fail-closed-degradation-under-absent-rerank-weights). Degradation scenarios are pinned by `tests/test_gate_degradation_scenarios.py` |
| Per-request run audit: stage executed / skipped / not-reached / degraded, rerank provenance, gate mode, outcome | `REPO_VERIFIED` | `PENDING` (no run against a real stack) | `core/run_report.py`, surfaced on `/api/query` behind `RAG_AUDIT_REPORT=1`. One real recorded request with a real reranker |
| Prompt trust boundary: evidence confined to a marked data region, current request in a separate instruction region, boundary-marker escaping on every untrusted channel | `REPO_VERIFIED` | `PENDING` | Structural prompt constraint. **Not** prompt-injection immunity and **not** proof that jailbreaking is impossible |
| Offline ingestion: multi-format parsing, OCR routing for scanned PDF pages, deterministic chunking, content-hash incremental state, snapshot carry-forward, full rebuild, snapshot verification, epoch sealing | `REPO_VERIFIED` | `PENDING` (no real-corpus ingestion artifact) | Epoch activation is an explicit human step; the scheduler never activates automatically |
| Query rewriting and conversation history with evidence locking | `REPO_VERIFIED` | `PENDING` | |
| 4B / 14B stateless routing and bounded generation retry contract | `REPO_VERIFIED` (routing contract) | `PENDING` (`vllm` not installed, weights absent) | The GPU topology in `config.json` has never been executed here. `python3 -m router.gpu_gate` measures it — see [GPU topology gate](#gpu-topology-gate-is-not-a-deployment) |
| Structured 9-field business-action audit, dual sink (Redis Stream + daily JSONL) | `REPO_VERIFIED` | `PENDING` (no production audit-log inspection) | |
| Prometheus alert rules (6) over metrics this application actually emits; Grafana dashboards (10 panels) | `REPO_VERIFIED` (config/JSON) | `PENDING` (rule evaluation under sustained real traffic; see #85 `VAL-ALERT-001`) | Every threshold is `DESIGN_TARGET`. An earlier in-process `AlertingManager` is legacy and is not on the request path |
| OTLP span exporter | `REPO_VERIFIED` (implementation, **off by default**) | `PENDING` (runtime closure: app → exporter → collector → backend, then read a span back) | |
| Retrieval benchmark, performance artifact and RAGAS harnesses with their artifact contracts | `REPO_VERIFIED` (frameworks) | `PENDING` (results) | Unmeasured is recorded as `null` / `NOT RUN`, never as zero |
| Evidence-consistency guard enforced in CI | `REPO_VERIFIED` | — | `scripts/check_repo_consistency.py` checks mechanically decidable contracts only. It does not judge arbitrary natural-language claims |
| Retrieval regression suites against in-process storage (`QdrantClient(":memory:")`) | `REPO_VERIFIED` | — | Proves pipeline behaviour, **not** real-engine behaviour. Distinct from any real-service run |

## Implemented but unvalidated against real infrastructure

Each row needs an environment this repository does not ship. The procedure for
each is in [Deferred runtime validation](deferred-runtime-validation.md); the
missing entries are tracked in #85.

| Capability | Code status | Real-environment status | Promotion criterion |
|---|---|---|---|
| Real Qdrant service: dense recall, epoch-versioned point IDs, payload filters | `REPO_VERIFIED` | `PENDING` | One run against a real Qdrant server with a committed artifact (#85 `VAL-STORE-001`). Note the asymmetry: Elasticsearch has `LOCAL_REAL_VALIDATION`, Qdrant does not |
| Retrieval metrics (Recall / HitRate@k / MRR@10 / NDCG@10) | `REPO_VERIFIED` (metrics, provenance) | `PENDING` (results) | A real ES/Qdrant run with an artifact carrying `git_sha`, dataset sha256, model revision, hardware, sample count, latency, command and limitations (#18) |
| QPS / P95 / P99 latency | — | `PENDING` | The defined load applied to the real API + LLM + retrieval stack, with a seven-file artifact in which unmeasured stays `null` |
| RAGAS quality scores | `REPO_VERIFIED` (harness) | `PENDING` | An approved evaluator provider plus credentials, then a real run. `--require-ragas` fails fast without them and emits no report — unavailable is *not* zero |
| BGE / CLIP / PaddleOCR real models | `REPO_VERIFIED` (adapter contracts) | `PENDING` | Real weights plus a smoke run (#8) |
| QLoRA fine-tuning | `REPO_VERIFIED` (tooling) | `PENDING` | A reproducible training run plus adapter artifacts |
| Airflow scheduling | `REPO_VERIFIED` (DAG registration) | `PENDING` | A real Airflow DAG execution. DAG code existing does not mean a scheduler is running |
| 4B / 14B vLLM GPU topology | `REPO_VERIFIED` (routing contract) | `PENDING` | A real GPU deployment under load (#12). Measured on this host: both endpoints unreachable, both weight directories absent, and **one** GPU visible against an assumed two (`router.gpu_gate`) |
| Kubernetes deployment and readiness admission | `REPO_VERIFIED` (manifests + 31 static checks) | `PENDING` | A real cluster deployment with the readiness contract observed (#54) |
| Browser → real RAG backend end-to-end run | `REPO_VERIFIED` (client + API metadata contract) | `PENDING` | One real browser run against the monolith with real ES/Qdrant and real models (#60) |
| Frontend production deployment | — | `PENDING` | Deployment-specific state; this repository does not assert it |
| Redis Cluster / Sentinel, multi-node Elasticsearch, TLS termination, external load balancer | — | `PENDING` | Multi-node infrastructure (#85 `VAL-TOPO-001`) |

## Fail-closed degradation under absent rerank weights

The most consequential runtime property of this system is easy to misread, so it
is stated explicitly rather than left to be discovered.

With no CrossEncoder weights present, `retrieval/cross_encoder_ensemble.py` falls
back to a deterministic path, so `ce_top1_score` and `ce_top3_mean_score` are 0.
The Evidence Gate score is then
`w3·agreement + w4·doc_consistency` = `0.2·agreement + 0.2·doc_consistency`, whose
maximum attainable value is **0.40** — below `low_confidence = 0.55`
(`config.json` → `retrieval.evidence_gate`; decision logic at
`retrieval/evidence_gate.py`).

**Consequence: on this repository as committed, with weights absent, every query
is rejected.** The system answers nothing rather than answering something
unsupported. That is the intended failure direction — a gate that degrades by
opening the gate is not a gate — and it is also the reason the missing assets are
listed as `PENDING` rather than treated as cosmetic.

This consequence is now asserted, not narrated:
`tests/test_evidence_gate_degradation.py` drives the deterministic fallback
output through the gate, checks the `0.40 < 0.55` ceiling against the configured
values, and asserts refusal for every fixture (`VAL-DEGRADE-001`, promoted to
`REPO_VERIFIED`). The precheck `python3 -m retrieval.rerank_status` reports the
same state (`status: BLOCKED`, `failure_mode: fail_closed`, `no_ce_ceiling`,
`low_confidence`) without loading a model; the gate logs it at construction and
exposes it as `EvidenceEnsembleGate.runtime_status()` / `EvidenceGateResult.gate_mode`.
The gate arithmetic and thresholds are deliberately unchanged.

## Rerank validation entry point

The second-stage reranker has only ever run its deterministic fallback in this
repository, and the risk that creates is specific: the fallback returns
BiEncoder order with `ce_score_ensemble = 0`, so a run that fell back is
*indistinguishable from one that reranked* unless something says otherwise.

`python3 -m retrieval.rerank_validation` is that something. Every result carries
a provenance label, and `is_reranking_evidence` is true only when **both**
`status=OK` and `provenance=cross_encoder`.

```bash
python3 -m retrieval.rerank_validation --smoke                 # load + score smoke pairs
python3 -m retrieval.rerank_validation --compare               # real vs deterministic fallback
python3 -m retrieval.rerank_validation --smoke --require-real  # exit 3 unless real
```

Exit codes: `0` ok · `1` failed · `2` configuration error · `3` pending (an
absent asset, not a failure).

Measured on this host — `--smoke` and `--compare`:

```text
status      : PENDING
provenance  : deterministic_fallback
reranking evidence : False
reason      : CE-A: path does not exist: ./models/cross-encoder-law;
              CE-B: path does not exist: ./models/cross-encoder-base
```

The comparison side deliberately **omits** `top1_rate_delta` rather than
reporting `0.0`: a fallback-vs-fallback comparison measures nothing, and a zero
would read as "no improvement" instead of "not measured".

The harness never downloads weights — loading is attempted only from the
configured local paths, asserted by a source-level test. Nothing here is a
reranking result, and no `CrossEncoder improved X` statement may be derived from
a run in this repository. Promoting this row requires real weights plus a
recorded run: `python3 -m retrieval.rerank_validation --compare` on a host that
has them, with the artifact cited.

## A silent degradation was invisible on the request path

With the weights absent, every query is refused at the Evidence Gate — but the
answer looked like any other refusal, the status code was `200`, and nothing
recorded that the reranker was dead. `core/run_report.py` builds a per-request
audit from the *observed* context and distinguishes four states a log line
collapses into one:

| status | meaning |
|---|---|
| `executed` | the stage ran and left a result |
| `skipped` | routing bypassed it (e.g. CLIP on a text-only query) |
| `not_reached` | an earlier gate refused, so it never ran |
| `degraded` | it ran, but on a fallback (e.g. the CE ensemble) |

Surfaced on `/api/query` as an optional `audit` field, gated behind
`RAG_AUDIT_REPORT=1` so the default response shape is unchanged, and it never
fails a request.

The reranker state is recorded as `provenance: cross_encoder |
deterministic_fallback`, and a run whose `rerank.provenance` is the fallback can
never set `answered_with_real_rerank`.

### Scope of the RBAC tests

`tests/test_rbac_cross_identity.py` (31 tests) covers cross-role,
cross-department and cross-cache-partition behaviour, including the fusion-order
case that actually matters: a recall path returning a document from another
department *as if the store-side pushdown had not applied*. The test asserts the
leak is present after RRF fusion and is then removed by the post-fusion
whole-list filter — the second filter is what closes the gap, so a test that only
checked the clean case would not prove it.

**It constructs no Qdrant client and no Redis connection**, and a test asserts
that fact about the file itself. It therefore proves pipeline behaviour and
cannot be cited as store-level RBAC validation; that remains `VAL-STORE-001`.

**Status: `REPO_VERIFIED` (report construction, pinned by
`tests/test_run_report.py`).** The report has not been emitted by a real request
against a real stack, so the artifact itself is `PENDING`.

## The Answer Gate's no-evidence branch is unreachable today

`AnswerGate.verify` returns `passed=True` unconditionally when `top_doc is None`,
with no check on the answer and none on `is_regulation`. On its face that reads
as "no evidence, allow" — the wrong direction for a gate.

It is **not reachable on the online path**, and the reason is structural rather
than incidental: reaching the call at `core/pipeline.py:545` requires passing
the Evidence Gate decision branch at `:493-505`, which returns early on
`reject`; and `EvidenceEnsembleGate.evaluate` returns `reject` whenever
`rerank_results` is falsy (`retrieval/evidence_gate.py:105-115`). So
`ctx.rerank_results[0]` at `:547` is always a real document.

Verified by execution, not by reading: an empty candidate list with any agreement
score (0.0 / 0.35 / 0.9 / 1.0) produces `decision="reject"` and `top_docs=[]`.

**The behaviour is deliberately left unchanged** — it is not an active leak, and
rewriting it would change answers on a path that cannot currently be taken. What
was added is the invariant:
`tests/test_answer_gate_no_evidence_reachability.py` fails if the Evidence Gate
ever becomes permissive about empty candidates. It also contains the positive
control, so the invariant cannot be "satisfied" by a gate that refuses
everything.

### Two degradation behaviours recorded rather than changed

Neither is a safety defect, so neither was "fixed":

- **`_compute_agreement_score({})` returns `0.35`** — with no candidates the
  KMeans branch never runs, so `clustering_score` keeps its neutral default of
  `0.5` and Jaccard stays `0.0`. That is *higher* than a genuine single-path run
  (`~0.19`), which is counter-intuitive but unreachable as a gate decision: all
  paths empty means `rerank_results` is empty, and the gate rejects at
  `evidence_gate.py:105` before this score is consulted. Changing it would alter
  arithmetic on a path that cannot produce an answer.
- **Losing a recall path is a real degradation**: agreement falls `0.49 → 0.19`
  and, with a healthy reranker, the decision downgrades `pass` (0.839) →
  `enhanced_generate` (0.779). Measured, asserted, and left as-is — refusing
  every partially-degraded query would be a correctness cost, not a safety gain.
  The test that pins this also asserts the score *falls*, so the signal cannot be
  absorbed silently later.

A third divergence is recorded but **not** changed, because it would alter a
public HTTP contract: `core/pipeline.py:578-584` documents "infrastructure
failure → HTTP 503" and re-raises for the API layer to answer 503, but
`VLLMGenerationError` inherits `GenerationError → ServiceError → Exception`,
**not** `InfrastructureError`. A vLLM timeout or connection failure therefore
falls through to the generic handler at `:586-592` and the caller receives
**HTTP 200** carrying `"系统处理出现异常，请稍后重试。"`. Verified by MRO inspection in
`tests/test_gate_degradation_scenarios.py`.

Whether a generation outage *should* answer 503 is a deployment decision — it
depends on whether the frontend retries on 503 — not a correctness fix, so it
is left for an operator. The context does record `degraded=True` and the
failure reason either way, and the run report renders it as `outcome: error`.

## GPU topology gate is not a deployment

`config.json` declares a two-GPU topology: `gpu0` serves `Qwen3-14B` on 8100,
`gpu1` serves `Qwen3-4B` on 8101. That is a plan. `python3 -m router.gpu_gate`
measures it read-only:

```text
status : PENDING
gpu metrics measured : False  (this gate never measures throughput)
  [MISS] Qwen3-14B      http://localhost:8100   reachable=False weights=False
  [MISS] Qwen3-4B       http://localhost:8101   reachable=False weights=False
  topology: assumed=2 observed=1 cuda=True
    gpu0: NVIDIA GeForce RTX 5060 Ti (16310 MiB)
  routing: deployment_mode=development complex_tier_live=False
```

Three separate facts, each of which alone would overstate the system:

1. **Both endpoints are unreachable and both weight directories are absent.**
2. **One GPU is visible against an assumed two**, so the configured topology
   cannot be exercised here at all. Serving a 14B model on a single 16 GB card
   is a different deployment, and its numbers would say nothing about the
   intended one.
3. **The 14B path is never called even when configured**, because
   `deployment_mode` is `development` and `models/llm_client.py:220-234`
   downgrades the complex tier to simple. Reading the routing contract alone
   suggests both tiers are live.

The gate is **structurally incapable of emitting a throughput number**:
`gpu_metrics_measured` is always `False`, the report carries no latency or QPS
field, and `tests/test_gpu_topology_gate.py` asserts the module contains no
timing or percentile construct. A real load run belongs in
`benchmarks/performance.py` under its own artifact contract.

## Microservice components

`api-gateway/`, `retrieval-service/`, `generation-service/`,
`monitoring-service/`, `cache-service/` and `rewrite-service/` are retained code
components. They are **not** the canonical deployment form and are **not**
integrated with the current frontend.

Module-level import status, reproduced rather than inferred: **all six `main`
modules now import** under the exact commands in
`docker-compose.microservices.yml`. Issue #84 found that 5 of 6 previously failed
with `ModuleNotFoundError` because each added only the project root to `sys.path`
and then imported sibling modules by bare top-level name:

| Service | Previously failing import | Fix |
|---|---|---|
| `rewrite-service` | `No module named 'metrics_collector'` | own dir + `monitoring-service/` on `sys.path` |
| `retrieval-service` | `No module named 'monitoring_service'` | `monitoring-service/` on `sys.path`; collector imported top-level |
| `generation-service` | `No module named 'complexity_evaluator'` | own dir + `monitoring-service/` on `sys.path` |
| `cache-service` | `No module named 'redis_cache'` | own dir on `sys.path` |
| `monitoring-service` | `No module named 'alerting'` | own dir on `sys.path` |

The fix normalises the shared collector: `retrieval-service` and
`generation-service` now import it as the top-level `metrics_collector` from
`monitoring-service/` instead of a non-existent `monitoring_service` package, and
each service adds its own directory for its bare sibling imports. A contract test
(`tests/test_microservice_imports.py`) imports every service `main` in its own
subprocess and asserts a FastAPI app exposing the route its compose healthcheck
calls, so the commands cannot silently rot again.

**Import is not deployment.** This is `REPO_VERIFIED` at the "module imports and
constructs an app" level only. Whether each container starts with its real
dependencies and whether the set works end to end against the current frontend
remain `PENDING`; no `docker compose -f docker-compose.microservices.yml up` run
is recorded. The six directories must not be presented as an assembled
microservice architecture.

Also relevant: `retrieval_service` is a tracked **symlink** to
`retrieval-service/`, created because a hyphenated directory cannot be imported
as a package, with roughly two dozen imports depending on it. Any checkout
without symlink support (Windows without Developer Mode) cannot use the
repository. Removing it needs a real shim package, because the
`retrieval-service/` code imports `retrieval_service.*` throughout, so it is
recorded here as a **non-blocking** portability item rather than fixed in this
pass.

## Deliberately not covered

Three things are absent on purpose and are not gaps in this document:

- **Kubernetes cluster deployment.** `deploy/k8s/` and 31 static checks exist and
  are honestly marked `PENDING`; a cluster is not available here.
- **Large-scale load testing.** The performance artifact contract is written, the
  harness exists, and what is missing is a real endpoint to point it at.
- **A commercial admin backend.** Out of scope; the repository is the retrieval,
  gating, permissions and evaluation path.

## Related documents

- [Architecture baseline](architecture-baseline.md) — the end-to-end contract the
  code is held to.
- [Evidence map](evidence-map.md) — canonical classification per capability, and
  the benchmark artifact acceptance criteria.
- [Deferred runtime validation](deferred-runtime-validation.md) — every
  validation not yet executed, with environment, procedure and expected artifact.
- [Pre-launch checklist](pre-launch-checklist.md) — operational pre-launch steps.
- [SLO and incident runbook](slo-runbook.md) — five objectives (all
  `DESIGN_TARGET`) and eight degradation procedures.
- [Security regression coverage](security-regression-coverage.md) — fixed threat
  list, the control and test behind each, and the gaps that stay open.