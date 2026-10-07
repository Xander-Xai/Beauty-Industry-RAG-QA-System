# Interview readiness — final report

> Point-in-time record of the consolidation that froze this repository for
> interview use. It states what the repository is, what it can prove, what it
> cannot, and where to look. It promotes no evidence: everything below is a
> pointer to an existing artifact or a boundary.

## 1. Repository state

| Item | Value |
|---|---|
| Default branch | `main` |
| Consolidation merge wave | `8a3b919` (#58) · `a1fd9e8` (#59) · `f99a55f` (#61) · `4a5f0aa` · `f2ccfbf` · `b90327a` |
| Open pull requests | 0 |
| Remote branches | `main` only |
| Open issues | #8, #12, #18, #32, #54, #60 — all runtime-validation debt, kept open on purpose |
| Closed this wave | #58, #59, #61 |
| CI | required checks green on `main` (Ruff, Dockerfile, frontend build, enterprise-config, tests 3.10/3.11, evaluation guard, pip-audit, secret scan) |
| Consistency guard | `python3 scripts/check_repo_consistency.py` passes |
| Local test suite | `pytest tests/` — 2565 passed, 15 skipped |

The only non-`main` local branch is `feat/retrieval-injection-containment`, which
holds a preserved, uncommitted retrieval-side injection-scan experiment
(`stash@{0}`). It is **out of scope** for this consolidation and was neither
merged nor continued; it is recorded here so it is not mistaken for a shipped
capability.

## 2. Core capabilities (what an interviewer should look at)

1. **Dynamic 2–4 way hybrid retrieval** — BGE dense + BM25, plus a rewrite
   variant and CLIP visual recall selected per request.
2. **Weighted RRF fusion** — query-aware weights (regulation → BM25, visual →
   CLIP).
3. **Two-stage reranking** — BiEncoder wide-keep to 150, dual CrossEncoder
   ensemble to 10.
4. **Evidence Gate + Answer Gate** — pre-generation evidence scoring and
   post-generation answer-vs-evidence consistency, with refusal as a product
   behaviour.
5. **uint32 bitmask RBAC defence in depth** — Elasticsearch push-down plus a
   Python re-filter before fusion; cache keys partitioned by permission
   fingerprint.
6. **Bounded ingestion trust and quarantine** — persisted provenance, explicit
   attributed content-hash-bound approval, and a seal gate that refuses
   unapproved imports.
7. **Knowledge lifecycle** — epoch build/validate/seal with explicit manual
   activation.
8. **RS256 authentication with fail-closed identity ingress** — one canonical
   uint32 mask contract shared by the JWT and dev-header paths.
9. **Multimodal ingestion** — OCR routing and CLIP image vectors alongside text.
10. **Structured audit** — 9-field redacted events to a Redis stream and daily
    JSONL.
11. **Observability** — `rag_*` metrics, 6 alert rules, a 10-panel Grafana
    dashboard, and an opt-in OTLP exporter.
12. **Evaluation and evidence governance** — RAGAS harness, retrieval/performance
    artifact frameworks, and a CI consistency guard that fails on documentation
    drift.

## 3. Evidence matrix

| Level | What is in this repository |
|---|---|
| `HISTORICAL_PRODUCTION` | Former-employer production context only: 3000+ documents, 5000+ images, 200+ users, 10–15 QPS bursts, RTX A5000 ×2, Qwen2.5 → Qwen3 gray migration, award. **Not** reproducible here. |
| `REPO_VERIFIED` | The full online and offline code paths, both gates, RRF, two-stage rerank, RBAC, RS256 auth, ingestion trust, audit, metrics, alert rules, Grafana JSON, SLO/runbook docs, OTLP exporter, and the benchmark/performance **frameworks** — all covered by deterministic tests or CI. |
| `LOCAL_REAL_VALIDATION` | Exactly five: Redis multi-process session persistence, Redis cross-process login rate limiting, nginx/`TRUSTED_PROXIES` client-IP handling, authenticated Elasticsearch, authenticated Prometheus scrape. |
| `PENDING` | Real retrieval benchmark result, real performance artifact, OTLP runtime export closure, live Grafana/alerts, real BGE/CLIP/PaddleOCR smokes, real Airflow run, real 4B/14B vLLM GPU topology, real RAGAS score, real Kubernetes run, browser→real-backend E2E. |

Canonical source: [`docs/evidence-map.md`](evidence-map.md).

## 4. Deferred validation

All twelve items live in [`docs/deferred-runtime-validation.md`](deferred-runtime-validation.md)
with their required environment, procedure, expected artifact, acceptance
criteria, failure interpretation and evidence-promotion rule. Every status is
`NOT EXECUTED`.

`VAL-RETRIEVAL-001` · `VAL-PERF-001` · `VAL-OBS-001` · `VAL-OBS-002` ·
`VAL-MODEL-001` · `VAL-MODEL-002` · `VAL-MODEL-003` · `VAL-SCHED-001` ·
`VAL-GPU-001` · `VAL-RAGAS-001` · `VAL-K8S-001` · `VAL-E2E-001`.

## 5. Known boundaries (do not claim these)

- No committed retrieval or performance benchmark artifact, so **no
  repository-measured metric, QPS, P95 or P99 is claimed**. Historical
  production figures (e.g. 10–15 QPS) and design targets are labelled
  `HISTORICAL_PRODUCTION` / `DESIGN_TARGET` and are not repository measurements.
- No real RAGAS score; the harness is `REPO_VERIFIED`, the result is `PENDING`.
- The 4B/14B vLLM topology has **never been executed here** (weights absent,
  `vllm` not installed).
- No Kubernetes cluster run; manifests are statically checked only.
- No browser-to-real-backend capture; the committed demo uses a synthetic mock
  API.
- Prompt-injection resistance is `PENDING`; the ingestion trust gate bounds
  provenance, not content, and the prompt-layer boundary is a structure, not a
  guarantee.
- Microservice directories (`api-gateway/`, `retrieval-service/`,
  `generation-service/`, `monitoring-service/`) are retained components, not an
  integrated deployment.
- Activation of a knowledge epoch is a manual step; the query path does not
  re-verify the seal.
- `AUTH_DEV_MODE` is a development posture, not a production identity solution.

## 6. Interview navigation

| Question | Open |
|---|---|
| Whole architecture | [`docs/architecture-baseline.md`](architecture-baseline.md) (canonical diagram) |
| Five-minute walkthrough script | [`docs/technical-walkthrough.md`](technical-walkthrough.md) |
| Claim → code → test → level → boundary | [`docs/evidence-map.md`](evidence-map.md) |
| Retrieval / rerank / gates | `retrieval/parallel_recall.py`, `retrieval/bi_encoder.py`, `retrieval/cross_encoder_ensemble.py`, `retrieval/evidence_gate.py`, `retrieval/answer_gate.py` |
| RBAC / authentication | `common/auth.py`, `auth/bitmask_rbac.py`, `auth/jwt_auth.py` |
| Ingestion trust / lifecycle | `offline/source_trust.py`, `offline/validator.py`, `offline/snapshot_builder.py` |
| Evaluation / benchmarks | `benchmarks/`, `tests/evaluation/`, `artifacts/benchmarks/`, `artifacts/performance/` |
| Observability / deployment | `monitoring/`, `docker-compose.yml`, `deploy/k8s/` |
| Evidence boundaries and drift | [`docs/repository-drift-report.md`](repository-drift-report.md), [`docs/repository-truth-audit.md`](repository-truth-audit.md) |

## 7. Final gate

```
PR #62 merged ................ TRUE
PR #63 merged ................ TRUE
PR #64 merged ................ TRUE
#58 / #59 / #61 closed ....... TRUE
stale branches removed ....... TRUE
P0 documentation conflicts ... 0
P1 documentation conflicts ... 0
README / architecture / evidence map consistent ... TRUE
runtime validation debt issued ... TRUE (#8/#12/#18/#32/#54/#60)
GitHub metadata consistent .... TRUE
CI / tests / lint blockers ... none
fake metrics ................. none
evidence promotion ........... none
private data leakage ......... none
```

```
INTERVIEW_READY = TRUE
```

Reaching this gate enters **FREEZE**: no further feature work unless a P0 factual
error, a security issue, a broken `main`, a broken CI, or a concrete interviewer
gap appears. Time now goes to code familiarity, spoken rehearsal, whiteboard
walkthroughs, mock interviews and applications — not to growing the repository.
