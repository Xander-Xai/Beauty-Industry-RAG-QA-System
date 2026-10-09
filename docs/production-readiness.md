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
| Layered RBAC: store-side pushdown + pre-fusion document filtering + L2 cache physical partitioning | `REPO_VERIFIED` | `PENDING` (no artifact against real Qdrant/ES with a real multi-role corpus) | Defence in depth, not a single control. See [Security regression coverage](security-regression-coverage.md) |
| Elasticsearch 8 BM25 sparse retrieval with Painless bitmask filtering | `REPO_VERIFIED` | `LOCAL_REAL_VALIDATION` (authenticated ES 8.11: writer mapping, `search_after`, online BM25) | |
| Dynamic 2–4 path recall selection driven by complexity and visual relevance | `REPO_VERIFIED` | `PENDING` | Selection logic is deterministic and tested; its measurable *benefit* is unmeasurable while `complexity` / `visual_required` are absent from the golden set (#86) |
| Two-stage rerank call chain (BiEncoder → Top 150 → CrossEncoder ensemble → Top 10) | `REPO_VERIFIED` (call chain + deterministic fallback) | `PENDING` (weights absent, so only the fallback path has ever run) | The second stage has never been exercised with real weights (#85 `VAL-RERANK-001`) |
| Evidence Gate and Answer Gate decisions, including regulation-conflict rejection | `REPO_VERIFIED` | `PENDING` | See [Fail-closed degradation](#fail-closed-degradation-under-absent-rerank-weights) |
| Prompt trust boundary: evidence confined to a marked data region, current request in a separate instruction region, boundary-marker escaping on every untrusted channel | `REPO_VERIFIED` | `PENDING` | Structural prompt constraint. **Not** prompt-injection immunity and **not** proof that jailbreaking is impossible |
| Offline ingestion: multi-format parsing, OCR routing for scanned PDF pages, deterministic chunking, content-hash incremental state, snapshot carry-forward, full rebuild, snapshot verification, epoch sealing | `REPO_VERIFIED` | `PENDING` (no real-corpus ingestion artifact) | Epoch activation is an explicit human step; the scheduler never activates automatically |
| Query rewriting and conversation history with evidence locking | `REPO_VERIFIED` | `PENDING` | |
| 4B / 14B stateless routing and bounded generation retry contract | `REPO_VERIFIED` (routing contract) | `PENDING` (`vllm` not installed, weights absent) | The GPU topology in `config.json` has never been executed here |
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
| 4B / 14B vLLM GPU topology | `REPO_VERIFIED` (routing contract) | `PENDING` | A real GPU deployment under load (#12) |
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