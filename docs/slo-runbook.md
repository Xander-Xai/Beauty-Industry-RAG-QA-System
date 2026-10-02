# SLO and incident runbook

Scope: the canonical deployment, which is **FastAPI monolith (`app.py`) + Docker
Compose**. The `api-gateway/`, `retrieval-service/`, `generation-service/` and
`monitoring-service/` directories are optional components and are not part of the
availability target below.

This document is a **contract**, not a report. Every number in the SLO section is
a `DESIGN_TARGET`. Nothing here states that the system has ever met a target.

## Evidence classification used in this document

| Level | Meaning |
|---|---|
| `DESIGN_TARGET` | A committed objective. Not measured. No artifact proves it. |
| `REPO_VERIFIED` | Implemented in this repository and covered by collected tests. |
| `LOCAL_REAL_VALIDATION` | Exercised here on a single host against a real dependency. |
| `PENDING` | Needs an asset, credential or runtime that is unavailable in this repository. |
| `HISTORICAL_PRODUCTION` | A former employer's production environment. Not reproducible here. |

Boundaries that hold everywhere in this document:

- **Local validation is not production cluster validation.** See
  [v2.5 working-milestone runtime/security validation](validation/v2.5-runtime-security-validation.md).
- **Historical production is not a repository benchmark.** The 10–15 short-burst
  QPS and 1500+ daily request figures are `HISTORICAL_PRODUCTION` context and can
  never be cited as a measurement of this repository. See
  [Interview evidence map](interview-evidence-map.md).
- **A design target is not a measured result.** PRD latency/QPS figures remain
  `DESIGN_TARGET` until an artifact under `artifacts/performance/` recorded for
  the *same* workload says otherwise. No such artifact is committed, so every
  measured figure in this repository is currently `PENDING`.

---

## Service level objectives

Five objectives. Each states the measurement window, the signal it is computed
from, and what happens when it is missed. A sixth "objective" is deliberately
absent: quality. Retrieval and answer quality are measured by the retrieval
benchmark harness, whose result is `PENDING` — so there is no honest quality SLO
to set yet.

| # | Objective | SLI | Target | Status |
|---|---|---|---|---|
| SLO-1 | Availability of the authenticated query API | fraction of `/api/query` + `/api/chat` requests not returning 5xx, per rolling 30 days | ≥ 99.5% | `DESIGN_TARGET` |
| SLO-2 | Server error rate | fraction of all `/api/*` responses that are 5xx, per 5 minutes | < 1% | `DESIGN_TARGET` |
| SLO-3 | Query latency | p95 of `/api/query` end-to-end, over a declared workload | ≤ 2000 ms | `DESIGN_TARGET` |
| SLO-4 | Dependency health | fraction of scrapes where Redis, Qdrant and Elasticsearch all report reachable, via `GET /api/health` | ≥ 99% | `DESIGN_TARGET` |
| SLO-5 | Knowledge release integrity | sealed epochs that passed full snapshot validation, over all releases | 100% | `DESIGN_TARGET` |

SLO-3's 2000 ms target is inherited from the PRD. It is **not** a measurement, and
this repository holds no latency artifact. Producing the first real number is
tracked in Issue #20's follow-up and depends on a real LLM endpoint, which this
repository does not run.

### Why 99.5% and not 99.99%

This is an internal enterprise knowledge assistant for roughly 200 users, not a
public API. A 0.5% monthly error budget is a few tens of minutes, which is the
right order of magnitude for a system where a human can retry. Choosing 99.99%
would be aspirational and unmeasurable without the production telemetry this
repository does not have.

### Error budget policy (target)

- Under budget: normal change cadence.
- Budget exhausted: freeze non-essential change, prioritise the alert with the
  largest burn rate, and re-run the diagnosis in the relevant runbook section.
- Budget exhausted for two consecutive windows: treat as an availability incident
  and escalate.

---

## Alert → response map

Alert rules live in [`monitoring/prometheus/alerts.yml`](../monitoring/prometheus/alerts.yml).
Every threshold in that file is a `DESIGN_TARGET`; none was derived from
production history, because no production history for this system exists.

| Alert | Covers | Runbook section |
|---|---|---|
| `RagAppDown` | target not scrapeable | [HighErrorRate](#higherrorrate) |
| `RagHighErrorRate` | SLO-2 breach | [HighErrorRate](#higherrorrate) |
| `RagRequestSaturation` | in-flight requests elevated | [HighLatency](#highlatency) |
| `RagHighLatencyP95` | SLO-3 breach | [HighLatency](#highlatency) |
| `RagRedisDegraded` | Redis degraded to process-local memory | [RedisUnavailable](#redisunavailable) |
| `RagHighLoginRateLimit` | login rate limiting firing broadly | [RedisUnavailable](#redisunavailable) |

Those six rows are the complete rule set; `tests/monitoring/test_prometheus_alerts.py`
asserts both that every rule is covered here and that every rule references an emitted
metric.

`RagDependencyDown` is **not** a Prometheus alert. It is a `GET /api/health` condition
(`redis=false`, `elasticsearch=false`, `qdrant=false`) and is used as shorthand in the
per-dependency sections below. There is deliberately no alert rule for it: the repository
emits no per-dependency gauge, so such a rule could never fire and would only look like
coverage. Those scenarios are driven by health-endpoint inspection plus the procedures
below.

---

# Incident runbook

Each entry: Alert → User impact → Diagnosis → Immediate mitigation → Degraded
mode → Rollback → Evidence to collect → Recovery verification.

---

## RedisUnavailable

**Alert** — `RagRedisDegraded` (`rag_redis_degraded_mode == 1`), or
`RagDependencyDown` with `redis=false` from `GET /api/health`.

**User impact** — Sessions are not shared across workers, so a user can appear
logged out when their next request lands on a different worker. Login rate
limiting degrades from a shared counter to per-process counting, which makes the
limit weaker in a multi-worker deployment. No data is lost; sessions live in
process memory until Redis returns.

**Diagnosis**

```bash
curl -s http://localhost:8000/api/health | python3 -m json.tool   # check dependencies.redis
docker compose ps redis
docker compose logs --tail=200 redis
grep -i "redis" logs/*.log | tail -50                             # fallback / degrade messages
```

The application logs `会话 Redis 持久化失败（降级内存）` and
`Redis 会话读取失败，降级内存` when it falls back.

**Immediate mitigation**

1. Confirm it is Redis and not the network: `redis-cli -a "$REDIS_PASSWORD" ping`.
2. If Redis is restarting, let it come back — the application already degrades
   without crashing.
3. If Redis is unreachable for longer than the session TTL, reduce worker count
   or drain traffic so users are not silently moved between inconsistent
   process-local sessions.

**Degraded mode** — process-local `SessionState` with in-memory rate limiting.
The service keeps serving. This behaviour is `LOCAL_REAL_VALIDATION` verified on
a single host (real Redis 7.4.9, two independent processes, plus a
deliberate-Redis-down degradation test); production Redis Cluster/Sentinel
topology is **not** validated.

**Rollback** — none needed; there is no configuration change to revert. If Redis
was changed, revert the change and restart.

**Evidence to collect**

- `rag_redis_degraded_mode` and `rag_redis_degraded_events` over the window.
- `GET /api/health` dependency block.
- Redis `INFO clients` / `INFO memory` if the process is reachable.
- Login 429 rate before and after (`rag_http_rate_limited`).

**Recovery verification**

1. `rag_redis_degraded_mode` returns to 0.
2. Write a session on worker A, read it from worker B (the integration test in
   `tests/integration/test_redis_session_runtime.py` with `RUN_RUNTIME_VALIDATION=1`
   does exactly this).
3. Confirm two processes share one login counter again.

---

## ElasticsearchUnavailable

**Alert** — `RagDependencyDown` with `elasticsearch=false`.

**User impact** — BM25 recall path degrades to an empty result. The RAG answer
loses its lexical retrieval leg; dense retrieval and the gates still run, so
answers become narrower and more likely to hit the Evidence Gate's reject branch.
Users see refusals or low-evidence answers rather than errors.

**Diagnosis**

```bash
curl -s http://localhost:8000/api/health | python3 -m json.tool
docker compose ps elasticsearch
docker compose logs --tail=200 elasticsearch
curl -u "elastic:$ELASTICSEARCH_PASSWORD" "$ELASTICSEARCH_HOST:9200/_cluster/health?pretty"
```

Check specifically for `xpack.security.enabled=true` with a wrong
`ELASTICSEARCH_USERNAME`/`ELASTICSEARCH_PASSWORD` pair — an auth failure looks
like an outage to the health check.

**Immediate mitigation**

1. Verify credentials before restarting anything. The BM25 retriever degrades to
   an empty result on bad credentials rather than raising.
2. Restore the index from the most recent snapshot.
3. Confirm `cosmetics_docs` exists and its mapping is intact.

**Degraded mode** — dense-only retrieval. The pipeline continues.

**Rollback** — if Elasticsearch was reindexed, repoint to the previous index
alias.

**Evidence to collect** — `/api/health` output, ES cluster health, the
`elasticsearch.index` setting from `config.json`, and the BM25-related
`rag_degradation_total` movement.

**Recovery verification** — `elasticsearch=true` in `/api/health`, and an
authenticated BM25 query returns hits.

---

## QdrantUnavailable

**Alert** — `RagDependencyDown` with `qdrant=false`.

**User impact** — Dense and image recall paths degrade. `/api/media/{doc_id}`
returns `503`, because the route reads document metadata from Qdrant to perform
the RBAC check and cannot substitute an "allow" when metadata is unavailable.

**Diagnosis**

```bash
curl -s http://localhost:8000/api/health | python3 -m json.tool
docker compose ps qdrant
curl http://localhost:6333/collections
```

Note the gRPC port: `config.json` → `qdrant.grpc_port` is what the production
client uses (`prefer_grpc=True`). A reachable REST port does not prove the dense
path works; the same distinction is enforced by the retrieval benchmark's
`probe_dense`.

**Immediate mitigation**

1. Restore Qdrant, or reindex from the active sealed epoch.
2. Keep `/api/media` returning 503 rather than allowing access — failing closed
   on media authorization is deliberate.

**Degraded mode** — BM25-only retrieval.

**Rollback** — switch `knowledge_version_epoch` back to the previous sealed epoch
if the active epoch's vectors are incomplete; see
[KnowledgeActivationRegression](#knowledgeactivationregression).

**Evidence to collect** — `/api/health`, Qdrant collection list, gRPC port
reachability, and the `rag_degradation_total` counter.

**Recovery verification** — `qdrant=true`, the text collection is non-empty, and
`/api/media/{doc_id}` returns a presigned URL for an authorized role.

---

## LlmEndpointUnavailable

**Alert** — a rising rewrite-fallback ratio, or a rise in
`rag_admission_rejected` with `rag_kv_pressure` near the configured
threshold. There is no `rag_rewrite_fallback_rate` series; the emitted
counter is `rag_rewrite_fallback` (see [HighLatency](#cache-hit-rate-and-fallback-ratio) for the ratio). This path is **not** covered by a dedicated Prometheus alert: the
repository does not emit a per-endpoint model-error counter, so it is detected
via the proxy's own 5xx and the two metrics above.

**User impact** — Query Rewrite falls back, so intent/business-type routing
degrades. In non-production `deployment_mode` a complex request may be routed
down to the 4B endpoint. Generation may fail outright, producing a 5xx or an
Evidence Gate rejection.

**Diagnosis**

```bash
curl -s http://localhost:8000/api/metrics -H "Authorization: Bearer $TOKEN" | grep rag_
curl -s "$VLLM_4B_URL/v1/models"        # single shared 4B endpoint, port 8101
curl -s "$VLLM_14B_URL/v1/models"       # 14B complex-generation endpoint, port 8100
nvidia-smi                                 # GPU present and busy?
```

**Immediate mitigation**

1. Confirm the endpoint process is alive before assuming a model problem.
2. If weights are missing or `vllm` is not installed, this is not a runtime
   incident — the topology has never been executed in this repository and stays
   `PENDING`.
3. If KV pressure is the cause, lower concurrency or reduce
   `gpu_memory_utilization` rather than raising timeouts.

**Degraded mode** — rewrite fallback to the configured simpler tier.

**Rollback** — revert the routing/threshold config change and restart.

**Evidence to collect** — the rewrite-fallback ratio, `rag_kv_pressure`,
`rag_admission_rejected`, the model endpoint logs, and `nvidia-smi`.

**Recovery verification** — the rewrite-fallback ratio returns to its baseline
and a complex query routes to the 14B endpoint again.

**Boundary** — single 4B/14B vLLM GPU topology validation is `PENDING`. Historical
production serving experience (RTX A5000 ×2, a Qwen2.5 → Qwen3 gray migration) is
`HISTORICAL_PRODUCTION` and is **not** evidence for this repository.

---

## HighErrorRate

**Alert** — `RagAppDown` (`up{job="rag-api"} == 0`), or `RagHighErrorRate`: the
5xx ratio of `/api/*` responses exceeds 1% over a 5-minute window
(`DESIGN_TARGET`, SLO-2).

**User impact** — Requests fail outright. Chat sessions may lose a turn.

**Diagnosis**

```bash
# Is the target scrapeable at all? A 401 here means the scrape bearer token is
# missing or wrong, not that the API is down.
curl -i http://localhost:8000/api/metrics | head -1
curl -s http://localhost:8000/api/metrics -H "Authorization: Bearer $TOKEN" | grep rag_http
curl -s http://localhost:8000/api/health  | python3 -m json.tool
grep '"outcome": "failed"' logs/*.log | tail -50     # audit: failed actions
```

The audit stream records `outcome: failed` for admin mutations and epoch seals,
which distinguishes an application bug from an infrastructure failure.

**Immediate mitigation** — identify the dominant status class from
`rag_http_responses_5xx` (with `rag_http_responses_4xx` for context), then
follow the matching dependency runbook.

**Degraded mode** — see the dependency sections; each has a defined reduced mode.

**Rollback** — if a config change preceded the spike, revert it. Otherwise no
rollback; investigate forward.

**Evidence to collect** — the 5xx ratio, the per-status breakdown, failed audit
events, and the request ids of failing calls (the `X-Request-ID` response header
ties a user report to log lines).

**Recovery verification** — the 5xx ratio is below the target for two consecutive
windows and a known-good query completes end to end.

---

## HighLatency

**Alert** — `RagHighLatencyP95`: p95 of `/api/query` exceeds 2000 ms over the
declared workload (`DESIGN_TARGET`, SLO-3).

**User impact** — Users wait. With front-end timeouts they see failures, which
then also burns the error budget.

**Diagnosis**

```bash
curl -s http://localhost:8000/api/metrics -H "Authorization: Bearer $TOKEN" \
  | grep -E "rag_http_request_duration|rag_kv_pressure|rag_cache_hit_|rag_cache_total|rag_redis"
```

Read the cache hit rate first: a collapse in cache hits produces retrieval
work that would otherwise have been skipped. There is no
`rag_cache_hit_rate` series — see
[Cache hit rate and fallback ratio](#cache-hit-rate-and-fallback-ratio) for
the two correct ways to read it. Then check `rag_kv_pressure` and the model
endpoint.

**Immediate mitigation**

1. If cache hit rate collapsed, verify Redis is reachable — a Redis outage both
   degrades sessions and removes the L1/L2 cache.
2. If KV pressure is high, shed load: reduce admitted concurrency or lower
   `gpu_memory_utilization`.
3. If neither, capture a trace (see [TraceLookup](#tracelookup)) and compare
   against a known-good span.

**Degraded mode** — truncation, tier downgrade, or refusal at the Evidence Gate.

**Rollback** — revert the last retrieval or routing configuration change.

**Evidence to collect** — the latency percentiles, cache hit rate, KV pressure,
admission rejections, and the trace ids of the slowest requests.

**Recovery verification** — p95 is below target across a full workload run **and**
a `artifacts/performance/` artifact has been produced for that run. Until the
artifact exists, the target remains unproven and SLO-3 stays `PENDING` as a
measured claim.

---

## KnowledgeEpochValidationFailure

**Alert** — no Prometheus alert. This is a **CLI/operator** event: the
`run_offline.py seal-epoch` command fails during snapshot validation. It is
recorded in the audit stream as `knowledge.epoch.seal` with
`outcome: failed`.

**User impact** — None immediately. The new epoch is **not** sealed, so the
previously sealed epoch stays active and online retrieval is unaffected. The
failure blocks the release.

**Diagnosis**

```bash
python3 run_offline.py full-rebuild --epoch <candidate>
python3 run_offline.py seal-epoch --epoch <candidate>   # validates, then seals
```

Validation covers Qdrant text and image plus Elasticsearch. Re-run the full
rebuild to see which store is inconsistent.

**Immediate mitigation**

1. **Do not activate the candidate epoch.** This is the whole point of
   validate-then-seal.
2. Keep the previous sealed epoch active and serving.
3. Investigate and rebuild the candidate.

**Degraded mode** — the system continues on the previous sealed epoch. No user
impact.

**Rollback** — none required, because activation never happened. If the operator
had already switched `knowledge_version_epoch`, see
[KnowledgeActivationRegression](#knowledgeactivationregression).

**Never** — do not pass `--skip-validation` to force a seal. It is recorded in
the audit event as `skip_validation: true` precisely so a later reader can tell a
validated release from an unvalidated one.

**Evidence to collect** — the validation failure output, the audit event, the
snapshot validator report, and the per-store counts for the candidate epoch.

**Recovery verification** — `seal-epoch` completes validation and seals; a
subsequent `/api/health` is `healthy`; a sampled query returns results
consistent with the candidate epoch.

---

## KnowledgeActivationRegression

**Alert** — no Prometheus alert. Activation is a **manual config edit**, so there
is no alertable server-side event by design. Detection is via the operator
checklist in [Pre-launch checklist](pre-launch-checklist.md).

**User impact** — After switching `knowledge_version_epoch`, retrieval may return
nothing, return stale permissions, or fail the media RBAC check, because the
online path filters on the active epoch and document metadata carries
`doc_version_epoch`.

**Diagnosis**

```bash
grep knowledge_version_epoch config.json
curl -s http://localhost:8000/api/health | python3 -m json.tool
grep '"action": "knowledge.epoch.seal"' logs/*.log | tail -5
curl -s http://localhost:8000/api/stats -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

**Immediate mitigation**

1. Revert `knowledge_version_epoch` to the previous sealed epoch.
2. Restart the online service — the epoch is read at startup.
3. Confirm `/api/health` and a sampled query.

**Degraded mode** — serve the previous sealed epoch. Because the previous epoch
is retained for exactly this reason, rollback is a config edit plus restart, not
a rebuild.

**Rollback** — the primary remedy. `config.json` →
`knowledge_version_epoch` back one step, restart, verify.

**Evidence to collect** — the active epoch before and after, the seal audit
events, `/api/stats`, and a sample of failing queries with their request ids.

**Recovery verification** — a sampled business question returns an answer backed
by evidence from the restored epoch, and `/api/media/{doc_id}` authorizes
correctly for at least two roles.

---

## Cache hit rate and fallback ratio

Neither of these ratios is a Prometheus series. The exporter publishes raw counters only,
so an operator who looks for `rag_cache_hit_rate` or `rag_rewrite_fallback_rate` will
find nothing and may conclude the metric is missing when it is merely derived.

| Ratio | Emitted counters it is computed from |
|---|---|
| Cache hit rate | `rag_cache_hit_L1`, `rag_cache_hit_L2`, `rag_cache_total` |
| Rewrite fallback rate | `rag_rewrite_fallback`, `rag_rewrite_success`, `rag_rewrite_fail` |

Two correct ways to read them:

1. `/api/stats` (requires authentication) returns both as computed fields:
   `cache_hit_rate.{L1,L2,L2_SESSION}` and `rewrite_fallback_rate`.

   ```bash
   curl -s http://localhost:8000/api/stats -H "Authorization: Bearer $TOKEN" \
     | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["cache_hit_rate"], d["rewrite_fallback_rate"])'
   ```

2. PromQL over the counters the collector really emits:

   ```promql
   rate(rag_cache_hit_L1[5m]) / clamp_min(rate(rag_cache_total[5m]), 0.000001)
   rate(rag_cache_hit_L2[5m]) / clamp_min(rate(rag_cache_total[5m]), 0.000001)
   rate(rag_rewrite_fallback[5m])
     / clamp_min(rate(rag_rewrite_success[5m]) + rate(rag_rewrite_fail[5m]), 0.000001)
   ```

`clamp_min` is load-bearing: without it an idle window yields `0/0` → `NaN`.

`tests/monitoring/test_observability.py` asserts that no panel, and
`tests/monitoring/test_prometheus_alerts.py` asserts that no alert rule, references a
derived name as if it were emitted.

---

## TraceLookup

Not an incident — the procedure for using tracing during any of the above.

**Current state.** The tracing hook is on the online pipeline path
(`core/pipeline.py` → `monitoring/otel_tracer.py`) and runs through the OpenTelemetry
SDK `TracerProvider`. Span export is **implemented but disabled by default**: with
`OTEL_EXPORT_ENABLED` unset or false no span processor is attached, so spans are
neither exported nor retained and no span is queryable. The exporter package itself is
an optional dependency (`requirements-otel.txt`), which is why a default install has
no exporter attached — that is a packaging decision, not a missing feature.

```bash
# confirm export state (default: disabled)
grep -E "OTEL_EXPORT_ENABLED|OTEL_EXPORTER_OTLP_ENDPOINT" .env
curl -s http://localhost:8000/api/metrics -H "Authorization: Bearer $TOKEN" \
  | grep rag_otel_exporter_enabled
```

To enable, set `OTEL_EXPORT_ENABLED=true` and an OTLP endpoint, then restart and
confirm the startup log reports an initialised exporter. Exporter initialisation
failure is non-fatal by design: the service continues and logs a warning.

**Boundary.** Three states, kept separate: the exporter is **implemented and
test-covered** (`REPO_VERIFIED`); it is **disabled by default**; and the
application → exporter → collector → backend → queried span loop is **PENDING**. That
last one needs a recorded run under `monitoring/evidence/`, which does not exist, so
this document claims no production tracing validation and no Jaeger/OTLP backend has
been queried. `docker-compose.observability.yml` can start a collector and a backend,
but starting them is not evidence that a span made the trip.

---

## AuditLookup

**Purpose** — answer "who did what, when, and was it allowed".

```bash
# recent enterprise action events from the JSONL sink
tail -n 200 logs/audit/$(date +%F).jsonl | python3 -m json.tool

# filter by action
grep '"action": "admin.role.update"' logs/audit/*.jsonl

# trace one request end to end
grep "$REQUEST_ID" logs/*.log logs/audit/*.jsonl
```

Recorded actions: `auth.login.success`, `auth.login.failure`,
`auth.login.rate_limited`, `admin.user.create`, `admin.role.update`,
`media.access.denied`, `knowledge.epoch.seal`.

There is deliberately **no** `knowledge.epoch.activate` event: this repository
has no activate endpoint, so emitting one would imply an API that does not exist.

**Retention and export.** Events are written to Redis Stream (`audit:events`,
capped at 10000 entries) and to daily JSONL files under `logs/audit/`, plus a
structured stdout line on the `audit` logger. A production deployment forwards
that stream to its SIEM; this repository does not implement log shipping.

**Secret handling.** Every event passes through redaction before it is written, so
passwords, tokens, `Authorization` headers and API keys are replaced with
`[REDACTED]` regardless of what the caller passed. Query text is never stored in
plaintext: it is SHA256-hashed, or fully redacted for development-type queries.

---

## What this runbook deliberately does not claim

- No SLO has been met. Every objective is `DESIGN_TARGET`.
- No alert has fired in production. The rules exist and are tested as
  configuration; no production Prometheus instance scrapes this system.
- No tracing backend has been verified. Exporter implementation is
  `REPO_VERIFIED`; the closed loop is `PENDING`.
- No performance artifact exists. `artifacts/performance/` contains only its
  contract README.
- Historical production figures (10–15 QPS, 1500+ daily requests, RTX A5000 ×2,
  the Qwen2.5 → Qwen3 migration) remain `HISTORICAL_PRODUCTION` and are never a
  substitute for a measurement of this repository.