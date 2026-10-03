# Interview Evidence Map

This file separates what an interview can honestly claim from what the public
repository can actually prove. It exists because "real production experience"
and "publicly reproducible repository results" are different kinds of evidence
and must never be mixed.

The map is derived from the current code, configuration, collected tests and
the local real-dependency validations. It is not a product feature list and it
does not introduce new capabilities.

## Classification vocabulary

| Level | Meaning | Interview-safe framing | Must not say |
|---|---|---|---|
| `HISTORICAL_PRODUCTION` | Work actually done in a former employer's production environment. Proprietary corpus, logs, models and dashboards are not in this repository. | "In my previous production system I ..." | "The repository proves this scale" |
| `REPO_VERIFIED` | Code/config exists and is covered by collected deterministic tests or CI in this repository. | "This is implemented and test-covered" | "This is production-validated" |
| `LOCAL_REAL_VALIDATION` | Exercised in this repository against real external dependencies (Redis, nginx, authenticated Elasticsearch, authenticated Prometheus) on a single local host. | "Validated locally against the real dependency" | "Production cluster / HA / SLO verified" |
| `DESIGN_TARGET` | Recorded target/design in PRD or plans; no implementation or no reproducible benchmark. | "The design target was ..." | "The running system achieves ..." |
| `PENDING` | Code may exist, but the real asset/runtime/credential needed to validate it is unavailable here. | "Implemented; real validation is pending" | "Already validated" |

Rules:

1. A level may only be upgraded with new evidence: new executable test or
   artifact for `REPO_VERIFIED`, a new local real-dependency run for
   `LOCAL_REAL_VALIDATION`, or a reproducible artifact for benchmark/model
   quality.
2. `LOCAL_REAL_VALIDATION` is never a substitute for production cluster, HA,
   latency/QPS or model-quality evidence.
3. Deterministic fake embedders, fake OCR providers and mocked Redis clients are
   not real-model or real-infrastructure validation.
4. Historical plans under `docs/superpowers/` are not evidence.

## Capability evidence

| Capability | Level | Repository evidence | Upgrade path |
|---|---|---|---|
| FastAPI canonical path | `REPO_VERIFIED` | `app.py`, `api/routes.py`, `core/pipeline.py`; `tests/test_architecture_contract.py`, `tests/test_pipeline_ordering.py` | None needed for the contract |
| Offline ingestion | `REPO_VERIFIED` | `offline/document_processor.py`, `offline/embeddings.py`, `offline/image_processor.py`; `tests/offline/` deterministic suites | Real corpus throughput is PENDING (see below) |
| BGE text embedding | `PENDING` (adapter contract `REPO_VERIFIED`) | Adapter + pooling contract in `offline/embeddings.py`; deterministic tests only | Real configured BGE smoke on real weights |
| CLIP image embedding | `PENDING` (adapter contract `REPO_VERIFIED`) | `offline/embeddings.py`; deterministic embedder tests | Real configured CLIP smoke on real weights |
| OCR (PaddleOCR) | `PENDING` | `offline/image_processor.py` provider interface; deterministic provider tests | Real PaddleOCR runtime smoke |
| Qdrant text/image | `REPO_VERIFIED` | `offline/text_ingestion.py`, `offline/qdrant_writer.py`; in-memory `QdrantClient` integration tests only (no real Qdrant service runtime test) | Real Qdrant service validation |
| Elasticsearch (writer + BM25) | `REPO_VERIFIED` | `offline/elasticsearch_writer.py`, `retrieval/bm25_retriever.py`; fake + real ES integration tests | Multi-node/TLS topology is deployment-specific |
| Elasticsearch security | `LOCAL_REAL_VALIDATION` | `tests/integration/test_es_auth_runtime.py`; local authenticated ES 8.11 (unauth/wrong creds 401) | Multi-node ES/TLS + production credentials |
| RBAC (uint32 bitmask) | `REPO_VERIFIED` | `common/auth.py`, `retrieval/parallel_recall.py`, `api/routes.py`; `tests/test_bitmask_rbac.py`, `tests/test_retrieval_authorization_contract.py` | Production policy audit is deployment-specific |
| Redis session persistence | `LOCAL_REAL_VALIDATION` | `tests/integration/test_redis_session_runtime.py`; real Redis 7.4.9 multi-process write/restore/TTL | Redis Cluster/Sentinel production topology |
| Redis login rate limit | `LOCAL_REAL_VALIDATION` | `tests/integration/test_login_rate_limit_runtime.py`; real Redis 7.4.9 cross-process 429 + window expiry | Production load-balancer topology |
| Trusted proxy / client IP | `LOCAL_REAL_VALIDATION` | `tests/integration/test_trusted_proxy_runtime.py`; real nginx single + multi-hop; `app.py` `proxy_headers=False` | Non-nginx proxies, cloud LB topologies |
| Prometheus metrics scrape | `LOCAL_REAL_VALIDATION` | `tests/integration/test_metrics_auth_runtime.py`; authenticated scrape (no token 401, bearer 200, `up == 1`) | Long-run Prometheus/Grafana operations |
| RRF fusion | `REPO_VERIFIED` | `retrieval/parallel_recall.py`; `tests/test_rrf_fusion.py`, `tests/test_parallel_recall.py` | Relevance benchmark is PENDING |
| BiEncoder rerank | `REPO_VERIFIED` (implementation) | `retrieval/bi_encoder.py`, `core/pipeline.py`; `tests/test_bi_encoder_rerank.py` | Real model weights + evaluation |
| CrossEncoder ensemble | `REPO_VERIFIED` (implementation) | `retrieval/cross_encoder_ensemble.py`; architecture-contract tests | Real model weights + GPU batch measurement |
| Evidence Gate | `REPO_VERIFIED` | `retrieval/evidence_gate.py`; architecture-contract tests | Threshold tuning against real labeled data |
| Answer Gate | `REPO_VERIFIED` | `retrieval/answer_gate.py`; architecture-contract tests | NLI model quality on real assets |
| 4B routing | `REPO_VERIFIED` (routing contract) | `router/stateless_router.py`, `models/llm_client.py`; `tests/test_architecture_contract.py` | Real vLLM 4B GPU deployment |
| 14B routing | `REPO_VERIFIED` (routing contract) | `router/stateless_router.py`, `config.json` `gpu0.models.gen_14b`; contract tests | Real vLLM 14B GPU deployment |
| RAGAS evaluation | `PENDING` (harness `REPO_VERIFIED`) | `tests/evaluation/ragas_eval.py`; deterministic guard; library `evaluate()` keeps a non-quality unavailable fallback, while `--require-ragas` fails fast with no report | Approved evaluator provider + API key + real run |
| Performance benchmark | `PENDING` | Load-test utilities under `tests/load/`; no checked-in artifact | A reproducible benchmark artifact (see criteria below) |
| Performance artifact contract | `REPO_VERIFIED` (framework) / `PENDING` (result) | `benchmarks/performance.py`, `artifacts/performance/`; seven-file contract with `EXECUTED`/`PARTIAL`/`BLOCKED` derived from what happened; unmeasured values are `null`, never `0`; `tests/performance/` | A real run against a live API, LLM and retrieval stack |
| Performance real run | `PENDING` | No artifact is committed. `tests/load/locustfile.py` blocks on an unreachable API or a missing bearer token and records `blocked_reason` | Execute the declared workload and commit one artifact |
| Structured enterprise audit | `REPO_VERIFIED` (implementation) | `common/audit.py`; stable 9-field schema, forced redaction, request-id correlation; `auth.login.*`, `admin.user.create`, `admin.role.update`, `media.access.denied`, `knowledge.epoch.seal`; Redis Stream + daily JSONL sinks; `tests/test_audit_log.py` | Forward the audit stream to a SIEM (out of scope here) |
| SLO + incident runbook | `REPO_VERIFIED` (document) / `DESIGN_TARGET` (objectives) | `docs/slo-runbook.md`; 5 objectives, 8 incident procedures written against the degradation paths that exist in code | Meet an objective in a real environment |
| Prometheus alert rules | `REPO_VERIFIED` (configuration) / `PENDING` (fired in production) | `monitoring/prometheus/alerts.yml`; 6 alerts over metrics verified as emitted; thresholds are `DESIGN_TARGET` | A production Prometheus instance evaluating these rules |
| Grafana dashboard | `REPO_VERIFIED` (JSON) / `PENDING` (validated against a live stack) | `monitoring/grafana/dashboards/rag-overview.json`; 10 panels, only metrics this application emits; no hallucination-rate, live-RAGAS or GPU panel | Import it into a running Grafana and confirm the panels populate |
| Prometheus metrics endpoint | `REPO_VERIFIED` | `api/routes.py` serves `/api/metrics` from `monitoring/otel_tracer.py::MetricsCollector`; `rag_http_*`, `rag_redis_degraded_mode`, `rag_otel_exporter_enabled`; `tests/monitoring/` asserts every alert metric exists | A scrape job in a running Prometheus |
| QLoRA fine-tuning | `PENDING` | `offline/finetune_qlora.py`; utility + mocked tests only | Reproducible training run + adapter artifact |
| Airflow scheduling | `PENDING` | `dags/knowledge_base_dags.py`; DAG registration tests only | Real Airflow DAG execution |
| OpenTelemetry tracing hook | `REPO_VERIFIED` (hook wired) | `core/pipeline.py` → `monitoring/otel_tracer.py`; the tracer runs on the OTel SDK `TracerProvider` and reduces span attributes to an allow-list; `tests/test_monitoring_otel.py` and `tests/monitoring/test_observability.py` cover the collector, the local and OTel span paths, and the export switch | A recorded span from a real request |
| OTLP exporter | `REPO_VERIFIED` (implementation) | `monitoring/otel_exporter.py`; opt-in via `OTEL_EXPORT_ENABLED` (**disabled by default**), non-fatal on failure, span-attribute allow-list; optional dependency isolated in `requirements-otel.txt`; `.env.example` documents the switch; `tests/monitoring/test_observability.py` | Install the exporter package and enable it against a real collector; the end-to-end runtime evidence is tracked separately and is still `PENDING` |
| Legacy Jaeger agent config | `HISTORICAL` (superseded path) | `config.json` → `monitoring.jaeger.enabled=false`; `JAEGER_AGENT_HOST`/`PORT` in `.env.example`; no Python module reads these keys, and the OTel SDK no longer ships a Jaeger exporter | Nothing — do not present this as the exporter path |
| OTLP backend closed loop | `PENDING` | No recorded evidence under `monitoring/evidence/`. Export state is observable as `rag_otel_exporter_enabled`. `docker-compose.observability.yml` can start a collector and a backend, but starting them is not evidence a span arrived | Application -> exporter -> collector -> backend -> a span actually queried |

## Business scale — historical production context

The following numbers come from a former company production environment. The
public repository does **not** contain the proprietary corpus, production logs,
model weights or dashboards behind them. They are `HISTORICAL_PRODUCTION`, not
`REPO_VERIFIED` and not a reproducible benchmark.

| Metric | Value | Level | Boundary |
|---|---|---|---|
| Documents | 3000+ | `HISTORICAL_PRODUCTION` | company production context; proprietary corpus/logs/models are not included in the public repository |
| Images | 5000+ | `HISTORICAL_PRODUCTION` | company production context; proprietary corpus/logs/models are not included in the public repository |
| Products | 1500+ | `HISTORICAL_PRODUCTION` | company production context; proprietary corpus/logs/models are not included in the public repository |
| Ingredients | 2000+ | `HISTORICAL_PRODUCTION` | company production context; proprietary corpus/logs/models are not included in the public repository |
| Regulation systems | 8 | `HISTORICAL_PRODUCTION` | company production context; proprietary corpus/logs/models are not included in the public repository |
| Internal users | 200+ | `HISTORICAL_PRODUCTION` | company production context; proprietary corpus/logs/models are not included in the public repository |
| Short-burst QPS | 10–15 | `HISTORICAL_PRODUCTION` | production observation, not a repository benchmark |
| Daily requests | 1500+ | `HISTORICAL_PRODUCTION` | production observation, not a repository benchmark |
| Production serving hardware | RTX A5000 ×2 | `HISTORICAL_PRODUCTION` | the serving host is not part of this repository; no comparable GPU topology has been executed here |
| Later-stage model migration | Qwen2.5 → Qwen3-14B / Qwen3-4B gray-migration validation | `HISTORICAL_PRODUCTION` | proprietary production models, weights, traffic-split configuration and logs are not in this repository; the migration is **not** reproducible here |
| Company recognition | Annual technical innovation award | `HISTORICAL_PRODUCTION` | company recognition of prior work; **not** runtime technical validation of this codebase, and not evidence about this repository |

Interview framing:

> "In my previous production system the corpus was on the order of 3000+
> documents and 5000+ images for 200+ internal users, with short bursts around
> 10–15 QPS. That is company production context; the public repository contains
> a small sanitized subset and no production logs, so those numbers are not a
> reproducible benchmark from this repo."

> "Production model serving ran on RTX A5000 ×2, and in a later stage I validated
> a Qwen2.5 to Qwen3-14B / Qwen3-4B gray migration. Those weights, logs and
> traffic-split configuration were proprietary and are not in this repository, so
> the repository does not reproduce that migration."

Boundary rules for the three rows above the volume metrics:

1. **Do not classify any of this as `REPO_VERIFIED`.** There is no code, config,
   test or artifact in this repository that evidences the serving hardware, the
   gray migration, or the award.
2. **Do not substitute historical production experience for repository
   validation.** The single shared 4B / 14B vLLM GPU topology in `config.json` has
   never been executed in this repository: the weights are absent and `vllm` is
   not installed. That item stays `PENDING`.
3. **An award is not a runtime validation.** It is recognition of company work and
   carries no evidence about this codebase's latency, throughput or correctness.

Do not present these as repository results. Do not invent a benchmark to match
them.

## Benchmark artifact acceptance criteria

A number may only be described as a repository-reproducible benchmark when a
checked-in artifact records at least:

```text
git_sha
dataset_path
dataset_sha256
config_sha256
model_name
model_revision
hardware
CUDA
dependency versions
sample count
retrieval metrics
latency metrics
success/failure counts
commands
timestamp
limitations
```

Anything less is `PENDING`, a design target, or a production observation — never
a repository benchmark.

| Retrieval benchmark | `REPO_VERIFIED` (framework) / `PENDING` (result) | `benchmarks/`; `tests/benchmark/`; artifacts under `artifacts/benchmarks/`; no real benchmark artifact is committed, so no retrieval metric is claimed | A reproducible artifact produced by a real Elasticsearch/Qdrant run over a corpus that contains the ground-truth passages |

## Known claim risks to avoid

- Describing `LOCAL_REAL_VALIDATION` as production cluster / HA / SLO.
- Describing deterministic fake models as real BGE/CLIP/OCR validation.
- Presenting `HISTORICAL_PRODUCTION` scale as `REPO_VERIFIED`.
- Using historical production model-serving experience (RTX A5000 ×2, the
  Qwen2.5 → Qwen3 gray migration) as evidence that this repository's 4B/14B vLLM
  topology was validated. It was not; that remains `PENDING`.
- Treating a company award as runtime technical validation of this codebase.
- Claiming OpenTelemetry/Jaeger export is closed-loop. The exporter is implemented
  and test-covered but disabled by default, and no span has ever been queried from a
  backend.
- Presenting default-zero monitoring gauges without hooks as live metrics.
- Citing a `rag_*` series the exporter does not emit. There is no
  `rag_cache_hit_rate` and no `rag_rewrite_fallback_rate`; the exporter publishes raw
  counters and the ratios are either a PromQL ratio over them or a computed field on
  `/api/stats`.
- Quoting any retrieval metric. The framework is `REPO_VERIFIED`; the result is
  `PENDING` and no artifact exists.
- Saying "performance is verified" as one phrase. The artifact *framework* is
  `REPO_VERIFIED`; no performance *result* exists.
- Saying "alerting is in place" as if alerts had fired. The rules exist as
  configuration; no production Prometheus evaluates them.
- Saying "tracing is closed-loop". The exporter is implemented; no
  application -> collector -> backend -> queried-span run is recorded.
- Saying "the SLO is 99.5%". Every objective is a `DESIGN_TARGET`.
