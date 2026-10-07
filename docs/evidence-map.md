# Evidence Map

This file separates what this repository can honestly claim from what it can
actually prove. It exists because "real production experience"
and "publicly reproducible repository results" are different kinds of evidence
and must never be mixed.

The map is derived from the current code, configuration, collected tests and
the local real-dependency validations. It is not a product feature list and it
does not introduce new capabilities.

## Classification vocabulary

This table is the **single canonical evidence taxonomy** for this repository. Every
current repository-truth document classifies its claims with
these levels and no others — including the `Status` column of
[the repository truth audit](repository-truth-audit.md) and the `Evidence level`
columns of the validation records. A document that needs a state this table does
not define must extend this table (and the guard that reads it) rather than invent
a local status word.

| Level | Meaning | Safe framing | Must not say |
|---|---|---|---|
| `HISTORICAL_PRODUCTION` | Work actually done in a former employer's production environment. Proprietary corpus, logs, models and dashboards are not in this repository. | "This was done in a previous production environment" | "The repository proves this scale" |
| `HISTORICAL` | A superseded implementation, configuration, design or compatibility artifact **in this repository**, retained only for historical lineage. It is not a current capability, and it is not evidence of former-employer production usage. | "This is a superseded repository path retained for historical/compatibility context" | "This is a current production capability" |
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
4. Superseded plans, formerly stored under `docs/superpowers/` and removed from
   the current branch, are not evidence. They survive only in Git history, so a
   reader cannot open them as a current source — and their absence from the tree
   is itself the record, not a gap.
5. `HISTORICAL` and `HISTORICAL_PRODUCTION` are not interchangeable.
   `HISTORICAL` is about this repository's own superseded code/config/design;
   `HISTORICAL_PRODUCTION` is about a former employer's real production system.
   Neither one may be presented as the other, and neither is a current capability.
6. No document may define a second status vocabulary. A superseded in-repository
   artifact is `HISTORICAL`, never a local word such as "stale" or "partial"; a
   capability whose real asset is missing is `PENDING`, never a local word such as
   "planned" or "blocked". Reconciling to this table never upgrades evidence: it
   only names the level the evidence already had.
7. Retiring a superseded artifact records it as `HISTORICAL`; it does not delete
   the record of it. `HISTORICAL` keeps the lineage visible while withholding the
   claim, and it must never be promoted to `REPO_VERIFIED` or `HISTORICAL_PRODUCTION`
   because the code is still readable in the tree.
8. **A successful CI build is not runtime evidence.** For the frontend this splits
   into four claims that are graded separately and must never be merged:

   | Claim | Level | Evidence |
   |---|---|---|
   | (A) React client + API metadata contract | `REPO_VERIFIED` | `frontend/src/App.jsx`, `tests/test_auth_metadata.py` |
   | (B) `npm ci` + `npm run build` in GitHub Actions | `REPO_VERIFIED` | `.github/workflows/ci.yml` job `frontend-build` |
   | (C) frontend + real backend end-to-end runtime | `PENDING` | no committed browser artifact against the real monolith |
   | (D) production deployment | `PENDING` | deployment-specific state, outside this repository |

   (B) compiles a bundle. It observes no browser, no real backend and no deployed
   environment, so a green `frontend-build` may never be stated as "end-to-end
   validated", "browser-verified against the real backend" or "deployed". The
   committed Playwright demo capture runs the **real UI** against the synthetic
   `docs/demo/mock_api.py`, which makes it a demo capture and explicitly not (C).
   `scripts/check_repo_consistency.py` enforces all four rows and rejects a
   document that denies (B) or inflates it into (C)/(D).

## Run outcomes that are not evidence levels

Some columns describe **what one execution did**, not **how much evidence exists**.
Those tokens are recorded here so they are never mistaken for a level, and so that
reconciling the taxonomy does not have to rewrite an artifact contract's own enum
(which lives in application code and is out of scope for documentation changes).

| Token | Vocabulary | Meaning |
|---|---|---|
| `EXECUTED` | performance/benchmark artifact run status | the declared workload ran to completion |
| `PARTIAL` | performance/benchmark artifact run status | part of the declared workload ran; unmeasured values stay `null`, never `0` |
| `BLOCKED` | performance/benchmark artifact run status | the run could not start, and `blocked_reason` says why |
| `PASS` | validation record stage outcome | the recorded check for that stage succeeded |
| `NOT RUN` | validation record stage outcome | the stage was not executed, so it carries no evidence |
| `NOT EXECUTED` | deferred-validation index status | the validation was not executed, so it carries no evidence; equivalent to `NOT RUN`, and used by [the deferred runtime validation index](deferred-runtime-validation.md) |
| `SYNTHETIC DEMO` | synthetic-fixture provenance | the value comes from a deliberately fabricated fixture used to exercise a code path (the committed demo figure and its corpus), not from a measurement of anything. It is a statement about **where a number came from**, not about how much evidence exists — which is why it is not a level: a synthetic figure cannot be promoted, demoted, or re-validated, and it can never satisfy an upgrade path |

Rules:

1. A run outcome never appears in an evidence-level column. A doc that wants to
   report both says the outcome and then classifies the evidence separately, e.g.
   "`PASS` in the results column, `LOCAL_REAL_VALIDATION` in the evidence column".
2. `PARTIAL` here means "the workload did not finish". It never means "the evidence
   is partial" — that is the job of a compound level such as
   `REPO_VERIFIED` (framework) / `PENDING` (result), which states which half is missing.
3. `BLOCKED` and `NOT RUN` describe this run. They do not convert a `PENDING`
   capability into a validated one, and a blocked run produces no publishable number.
4. A `SYNTHETIC DEMO` value sits beside the evidence column, never inside it. The
   token explains where a number came from; the evidence column still states what
   this repository can prove. A synthetic fixture exercises a code path — it is not a
   measurement, and it can never satisfy an upgrade path.

## Capability self-verification index

> A 10-second lookup: a reviewer names a capability, this table gives the
> honest claim, the code, the test, the evidence level, the boundary and the
> likely follow-up. It is a navigation index over the same facts as the
> [capability evidence](#capability-evidence) table below, not a second evidence
> source; when the two disagree, the table below wins and this index is wrong.

| Capability | Reviewer claim (口述) | Level | Implementation | Tests | Runtime artifact | Known boundary | Likely follow-up |
|---|---|---|---|---|---|---|---|
| Dynamic hybrid retrieval (2–4 way) | "Recall is dynamic: 2 ways for simple questions, 3 with a rewrite variant, 4 when a visual judge selects CLIP." | `REPO_VERIFIED` | `retrieval/parallel_recall.py`, `core/pipeline.py` | `tests/test_parallel_recall.py` | none (no retrieval benchmark artifact) | Real relevance metrics are `PENDING` | "How do you decide 2 vs 4?" → complexity judge + visual judge |
| Dense retrieval (BGE + Qdrant) | "Dense recall is BGE text vectors in Qdrant, permission-filtered before fusion." | `REPO_VERIFIED` (adapter + Qdrant) / `PENDING` (real BGE weights) | `offline/embeddings.py`, `offline/qdrant_writer.py`, `retrieval/parallel_recall.py` | `tests/offline/`, `tests/test_parallel_recall.py` | none | Real BGE weights smoke is `PENDING` | "Which embedding model / dimension?" |
| BM25 (Elasticsearch) | "Lexical recall is Elasticsearch BM25 with status/epoch/role/dept pushed down." | `REPO_VERIFIED` | `retrieval/bm25_retriever.py`, `offline/elasticsearch_writer.py` | `tests/test_bm25_retriever.py`, `tests/offline/` | none | Multi-node/TLS ES is deployment-specific | "How is BM25 filtered by RBAC?" |
| CLIP visual recall | "Visual recall is CLIP image vectors in a separate Qdrant collection, only for visually-relevant requests." | `REPO_VERIFIED` (adapter) / `PENDING` (real weights) | `offline/embeddings.py`, `offline/image_processor.py` | `tests/offline/test_image_processing.py` | none | Real CLIP smoke `PENDING` | "How do you know a question is visual?" |
| Query rewrite variant | "A rewrite variant is an extra recall path for complex questions." | `REPO_VERIFIED` | `rewrite/`, `retrieval/parallel_recall.py` | `tests/test_parallel_recall.py` | none | Quality gain unmeasured here | "Does rewrite always help?" |
| Weighted RRF fusion | "RRF fuses paths with query-aware weights: regulation raises BM25, visual raises CLIP." | `REPO_VERIFIED` | `retrieval/parallel_recall.py` | `tests/test_rrf_fusion.py` | none | Weight values are design choices, not tuned on a committed benchmark | "Why RRF over score normalization?" |
| BiEncoder wide-keep | "BiEncoder keeps the top 150 before the expensive rerank." | `REPO_VERIFIED` (implementation) | `retrieval/bi_encoder.py`, `core/pipeline.py` | `tests/test_bi_encoder_rerank.py` | none | Real model weights not validated | "Why two stages?" |
| Dual CrossEncoder rerank | "Two CrossEncoders ensemble down to top 10, batch-predicted within the request." | `REPO_VERIFIED` (implementation) | `retrieval/cross_encoder_ensemble.py` | `tests/test_bi_encoder_rerank.py` | none | Cross-request micro-batch is not on the main path; real GPU batch measurement `PENDING` | "Why an ensemble?" |
| Evidence Gate | "Before generation, an evidence gate reads top-1, top-3 mean, path agreement and doc agreement and can refuse." | `REPO_VERIFIED` | `retrieval/evidence_gate.py` | `tests/test_architecture_contract.py` | none | Thresholds not tuned on real labeled data | "How are the four signals weighted?" |
| Answer Gate | "After generation, an answer gate checks the answer against core evidence; regulation contradictions refuse." | `REPO_VERIFIED` | `retrieval/answer_gate.py` | `tests/test_architecture_contract.py` | none | NLI model quality unmeasured | "What if the gate is wrong?" |
| Citation / provenance fields | "Answers carry citations and audit fields back to the source chunk." | `REPO_VERIFIED` | `core/pipeline.py`, `api/routes.py` | `tests/test_pipeline_ordering.py` | none | — | "Are citations always correct?" |
| Complexity routing | "Complexity judgement runs in parallel with rewrite and selects the model tier." | `REPO_VERIFIED` (routing contract) | `router/stateless_router.py`, `core/pipeline.py` | `tests/test_architecture_contract.py` | none | Tier accuracy unmeasured | "What is 'complex'?" |
| 4B / 14B model routing | "A shared 4B endpoint serves rewrite and simple generation; complex requests route to 14B." | `REPO_VERIFIED` (routing contract) / `PENDING` (real GPU) | `router/stateless_router.py`, `models/llm_client.py`, `config.json` | `tests/test_architecture_contract.py` | none | Real vLLM 4B/14B GPU deployment `PENDING` (weights absent, `vllm` not installed) | "How do you handle KV pressure?" |
| RS256 authentication | "Browser login issues RS256 tokens; malformed claims fail closed." | `REPO_VERIFIED` | `common/auth.py`, `auth/jwt_auth.py`, `api/routes_auth.py` | `tests/test_jwt_auth.py`, `tests/test_auth_identity_resolution.py` | none | `verify_token` validates signature/expiry only by design | "Why RS256 over HS256?" |
| uint32 RBAC | "Role and department are uint32 bitmasks; Elasticsearch pushes them down and Qdrant is re-filtered in Python before fusion, so no result escapes the permission filter." | `REPO_VERIFIED` | `common/auth.py`, `auth/bitmask_rbac.py`, `retrieval/parallel_recall.py` | `tests/test_bitmask_rbac.py`, `tests/test_retrieval_authorization_contract.py` | none | Production policy audit deployment-specific | "Why bitmasks over row-level ACLs?" |
| Cache isolation | "L2 cache keys are partitioned by knowledge epoch and permission fingerprint; an old unpartitioned key misses rather than leaks." | `REPO_VERIFIED` | `core/pipeline.py`, `cache/` | `tests/test_cache.py` | none | — | "How do you invalidate on a permission change?" |
| Rate limiting | "Login is limited to 5/min, counted in Redis across workers, with XFF trusted only from configured proxies." | `LOCAL_REAL_VALIDATION` | `api/routes_auth.py`, `common/auth.py`, `app.py` | `tests/integration/test_login_rate_limit_runtime.py` | local Redis 7.4.9 run record | Production LB topology not validated | "Can XFF be spoofed?" |
| Structured audit | "Every business action writes a 9-field redacted audit event to a Redis stream and daily JSONL." | `REPO_VERIFIED` (implementation) | `common/audit.py` | `tests/test_audit_log.py` | none | SIEM forwarding out of scope | "What is redacted?" |
| Offline ingestion | "Parse → clean → chunk → embed → write Qdrant + ES, with file-state/content-hash incremental updates." | `REPO_VERIFIED` | `offline/`, `run_offline.py` | `tests/offline/` | none | Real corpus throughput `PENDING` | "How does incremental update work?" |
| Multimodal ingestion | "Images get OCR text and CLIP vectors; text goes to ES and Qdrant." | `REPO_VERIFIED` | `offline/image_processor.py`, `offline/snapshot_builder.py` | `tests/offline/test_image_processing.py` | none | — | "How do you align OCR text with the image?" |
| OCR (PaddleOCR) | "OCR is behind a provider interface; the real PaddleOCR runtime smoke is pending." | `PENDING` | `offline/image_processor.py` | deterministic provider tests | none | Real PaddleOCR smoke `PENDING` | "Which OCR engine and why?" |
| Knowledge version / epoch / seal / activation | "Epochs can be built, validated and sealed by the pipeline, but sealing and activation are explicit by default (`auto_seal=false`)." | `REPO_VERIFIED` | `offline/snapshot_builder.py`, `offline/validator.py` | `tests/offline/test_snapshot_builder.py` | none | Query path does not re-verify the seal at activation time | "How do you roll back?" |
| Ingestion trust & quarantine | "Imported sources carry a persisted trust record; unapproved content can be staged but not sealed, and approval is explicit, attributed and content-hash bound." | `REPO_VERIFIED` | `offline/source_trust.py`, `offline/validator.py`, `run_offline.py review-source` | `tests/offline/test_source_trust_contract.py`, `tests/offline/test_cli.py` | none | Provenance, not content inspection; prompt-injection resistance `PENDING` | "Does this stop prompt injection?" |
| RAGAS evaluation | "The harness is implemented, but no real evaluator run exists, so no score is claimed." | `PENDING` (harness `REPO_VERIFIED`) | `tests/evaluation/ragas_eval.py` | evaluation guard tests | none | Needs an approved evaluator provider | "What score did you get?" → none claimed |
| Retrieval benchmark framework | "The framework computes retrieval metrics deterministically; no real benchmark artifact is committed, so no metric is claimed." | `REPO_VERIFIED` (framework) / `PENDING` (result) | `benchmarks/`, `artifacts/benchmarks/` | `tests/benchmark/` | none | Needs a real Qdrant/ES corpus with ground truth | "What is your Recall@k?" → none claimed |
| Performance artifact framework | "The framework records unmeasured values as null, never zero; no real performance run exists." | `REPO_VERIFIED` (framework) / `PENDING` (result) | `benchmarks/performance.py`, `artifacts/performance/` | `tests/performance/` | none | Needs a live stack | "What is your P95?" → none claimed |
| Prometheus metrics endpoint | "`/api/metrics` exposes `rag_*` series from a metrics collector; scraping requires a token." | `REPO_VERIFIED` | `api/routes.py`, `monitoring/otel_tracer.py` | `tests/monitoring/` | none | No running Prometheus in the repo | "Which series do you expose?" |
| Prometheus alerts | "Six alert rules reference only emitted series; thresholds are design targets." | `REPO_VERIFIED` (config) / `PENDING` (fired) | `monitoring/prometheus/alerts.yml` | `tests/monitoring/test_prometheus_alerts.py` | none | No production Prometheus evaluated them | "Have they fired?" → not here |
| Grafana dashboard | "A 10-panel dashboard uses only emitted metrics; it has never been imported into a live Grafana." | `REPO_VERIFIED` (JSON) / `PENDING` (live) | `monitoring/grafana/dashboards/rag-overview.json` | `tests/monitoring/` | none | No live stack | "Is the dashboard real?" |
| OTLP exporter | "The exporter is implemented and disabled by default; the runtime closed loop is pending." | `REPO_VERIFIED` (implementation) / `PENDING` (closed loop) | `monitoring/otel_exporter.py` | `tests/monitoring/test_observability.py` | none | No span ever queried from a backend | "Is tracing live?" → not here |
| Docker Compose | "Compose is the canonical deployment form; the app, Redis, Qdrant, MinIO and Elasticsearch come up together." | `REPO_VERIFIED` (configuration) | `docker-compose*.yml`, `Dockerfile` | CI Dockerfile + compose checks | none | Not a production HA topology | "Why Compose over K8s?" |
| Kubernetes manifests | "A minimal K8s contract exists for the FastAPI monolith (not the api-gateway/ component); static checks pass and a real cluster run is pending." | `REPO_VERIFIED` (manifests, statically checked) / `PENDING` (cluster) | `deploy/k8s/`, `api/readiness.py` | `tests/deploy/test_k8s_manifests.py` | none | Real cluster admission `PENDING` | "Did you run it on a cluster?" → not here |
| Retained microservice components | "Six service directories are retained as readable components, but the canonical runtime is the FastAPI monolith `app.py`." | `REPO_VERIFIED` (components exist) / `PENDING` (integrated deployment) | `api-gateway/`, `retrieval-service/`, `generation-service/`, `monitoring-service/`, `cache-service/`, `rewrite-service/` | none — no service is started by the canonical path | none | A retained directory proves the component is readable, **not** that a microservice deployment was ever assembled or run against the current frontend. Do not present the directory count as an architecture claim. | "Is this a microservice architecture?" → no, the monolith is canonical |

## Capability evidence

| Capability | Level | Repository evidence | Upgrade path |
|---|---|---|---|
| FastAPI canonical path | `REPO_VERIFIED` | `app.py`, `api/routes.py`, `core/pipeline.py`; `tests/test_architecture_contract.py`, `tests/test_pipeline_ordering.py` | None needed for the contract |
| Offline ingestion | `REPO_VERIFIED` | `offline/document_processor.py`, `offline/embeddings.py`, `offline/image_processor.py`; `tests/offline/` deterministic suites | Real corpus throughput is PENDING (see below) |
| Ingestion trust and quarantine | `REPO_VERIFIED` (provenance + approval + seal gate) | `offline/source_trust.py` (canonical trust record, approval ledger bound to a content hash), `offline/validator.py` (seal gate re-derived from persisted provenance), `run_offline.py review-source`; `tests/offline/test_source_trust_contract.py`, `tests/offline/test_cli.py` | Human review of real documents, an ingestion-time content scan, and adversarial-model / prompt-injection resistance are all outside what these tests measure and remain `PENDING` — see [Security regression coverage](security-regression-coverage.md) |
| BGE text embedding | `PENDING` (adapter contract `REPO_VERIFIED`) | Adapter + pooling contract in `offline/embeddings.py`; deterministic tests only | Real configured BGE smoke on real weights |
| CLIP image embedding | `PENDING` (adapter contract `REPO_VERIFIED`) | `offline/embeddings.py`; deterministic embedder tests | Real configured CLIP smoke on real weights |
| OCR (PaddleOCR) | `PENDING` | `offline/image_processor.py` provider interface; deterministic provider tests | Real PaddleOCR runtime smoke |
| Qdrant text/image | `REPO_VERIFIED` (current coverage); historical real-service run in the development lineage — see [Qdrant evidence: two states, kept apart](#qdrant-evidence-two-states-kept-apart) | `offline/text_ingestion.py`, `offline/qdrant_writer.py`; current deterministic regression coverage is the in-memory `QdrantClient` (`QdrantClient(":memory:")`) — that is what a re-run reproduces. PR #6/#7 also records a real local Qdrant service/container integration run performed together with Elasticsearch, and that record is retained as development lineage only | A new current real-service run that commits a reproducible artifact |
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
| Structured enterprise audit | `REPO_VERIFIED` (implementation) | `common/audit.py`; stable 9-field schema, forced redaction, request-id correlation; `auth.login.*`, `admin.user.create`, `admin.role.update`, `media.access.denied`, `knowledge.epoch.seal`, `knowledge.source.trust_decision`, `knowledge.source.quarantine`; Redis Stream + daily JSONL sinks; `tests/test_audit_log.py` | Forward the audit stream to a SIEM (out of scope here) |
| SLO + incident runbook | `REPO_VERIFIED` (document) / `DESIGN_TARGET` (objectives) | `docs/slo-runbook.md`; 5 objectives, 8 incident procedures written against the degradation paths that exist in code | Meet an objective in a real environment |
| Prometheus alert rules | `REPO_VERIFIED` (configuration) / `PENDING` (fired in production) | `monitoring/prometheus/alerts.yml`; 6 alerts over metrics verified as emitted; thresholds are `DESIGN_TARGET` | A production Prometheus instance evaluating these rules |
| Grafana dashboard | `REPO_VERIFIED` (JSON) / `PENDING` (validated against a live stack) | `monitoring/grafana/dashboards/rag-overview.json`; 10 panels, only metrics this application emits; no hallucination-rate, live-RAGAS or GPU panel | Import it into a running Grafana and confirm the panels populate |
| Prometheus metrics endpoint | `REPO_VERIFIED` | `api/routes.py` serves `/api/metrics` from `monitoring/otel_tracer.py::MetricsCollector`, which emits ~30 `rag_*` series across counters, histograms and gauges (`rag_http_*`, cache, rewrite, `rag_evidence_score_seconds`, `rag_nli_contradiction_score_seconds`, `rag_blip_*`, `rag_prefix_cache_*`, `rag_admission_total`, `rag_kv_pressure`, `rag_redis_degraded_mode`, `rag_otel_exporter_enabled`, …) plus the vLLM-resilience `rag_vllm_generation_*` family. **Ratios are never published as series** — no `*_rate` series exists; a ratio is either a PromQL expression over emitted counters or a computed field on `/api/stats`. Only a subset is consumed: the 6 alert rules and the 10 dashboard panels reference a fraction of them, and `rag_kv_pressure`, `rag_blip_*`, `rag_evidence_*`, `rag_nli_*`, `rag_prefix_cache_*` and `rag_admission_total` have neither an alert nor a panel. `tests/monitoring/` asserts every alert metric exists | A scrape job in a running Prometheus |
| QLoRA fine-tuning | `PENDING` | `offline/finetune_qlora.py`; utility + mocked tests only | Reproducible training run + adapter artifact |
| Airflow scheduling | `PENDING` | `dags/knowledge_base_dags.py`; DAG registration tests only | Real Airflow DAG execution |
| OpenTelemetry tracing hook | `REPO_VERIFIED` (hook wired) | `core/pipeline.py` → `monitoring/otel_tracer.py`; the tracer runs on the OTel SDK `TracerProvider` and reduces span attributes to an allow-list; `tests/test_monitoring_otel.py` and `tests/monitoring/test_observability.py` cover the collector, the local and OTel span paths, and the export switch | A recorded span from a real request |
| OTLP exporter | `REPO_VERIFIED` (implementation) | `monitoring/otel_exporter.py`; opt-in via `OTEL_EXPORT_ENABLED` (**disabled by default**), non-fatal on failure, span-attribute allow-list; optional dependency isolated in `requirements-otel.txt`; `.env.example` documents the switch; `tests/monitoring/test_observability.py` | Install the exporter package and enable it against a real collector; the end-to-end runtime evidence is tracked separately and is still `PENDING` |
| OTLP backend closed loop | `PENDING` | No committed closed-loop runtime evidence artifact exists in this repository. Export state is observable as `rag_otel_exporter_enabled`. `docker-compose.observability.yml` can start a collector and a backend, but starting them is not evidence a span arrived | Application -> exporter -> collector -> backend -> a span actually queried |
| Retained microservice components | `REPO_VERIFIED` (components exist) / `PENDING` (integrated deployment) | `api-gateway/`, `retrieval-service/`, `generation-service/`, `monitoring-service/`, `cache-service/`, `rewrite-service/`. Readable components only; the canonical runtime is the FastAPI monolith `app.py`, and no service in these directories is started by the canonical path. `docker-compose.microservices.yml` exists but no compose run against the current frontend has been recorded | Assemble and run the services as an integrated deployment against the current frontend; until then the directory count must not be presented as an architecture claim |

## Qdrant evidence: two states, kept apart

Qdrant has two independent evidence states in this repository's record. They are
both true, and collapsing either into the other is a false statement:

1. **Current, reproducible regression coverage is in-memory.** The deterministic
   Qdrant coverage in this repository uses the in-process `QdrantClient`
   (`QdrantClient(":memory:")`) in `tests/test_offline_text_ingestion.py`,
   `tests/offline/test_offline_end_to_end.py`, `tests/offline/test_snapshot_builder.py`
   and `tests/offline/test_image_processing.py`. A contributor re-running the suite
   reproduces exactly this and nothing more.
2. **The development lineage recorded in PR #6/#7 includes a real local Qdrant
   service/container integration execution**, performed in the same round as the
   real local Elasticsearch execution (PR #7 exists because `search_after` sorted
   on `_id` and failed against a real Elasticsearch 8). That is part of how this
   codebase was developed. It is a historical record of a past execution.

They do not license each other:

- The historical execution is **not** a current reproducible
  `LOCAL_REAL_VALIDATION` artifact. No artifact of that run is committed here, so
  nobody can re-run or verify it from this repository, and the deterministic
  suite does not depend on a Qdrant service.
- It establishes **nothing** about production Qdrant HA or cluster topology, cluster
  performance, retrieval or model quality, QPS or latency. A local development run
  is not a capacity or quality measurement.
- The Elasticsearch result recorded alongside it is likewise development lineage;
  the *current* `LOCAL_REAL_VALIDATION` for Elasticsearch is the separate,
  re-runnable authenticated-ES round recorded in
  [v2.5 working-milestone runtime/security validation](validation/v2.5-runtime-security-validation.md).
- Upgrading the current reproducible state requires a **new** real-service run whose
  artifact is committed. Until then the Qdrant row stays at its current level with
  "real Qdrant service validation" as its upgrade path.

Framing:

> "The Qdrant writers are covered by deterministic tests against the in-process
> Qdrant client, and during the work that landed in PR #6/#7 the same
> writers were also run against a real local Qdrant service alongside a real
> local Elasticsearch. That earlier run is development history rather than a
> checked-in artifact, so what this repository reproduces today is the in-memory
> coverage. Qdrant cluster, HA, throughput and quality are separate work that has
> not been validated here."

Must not say, in either direction:

- "Qdrant has only ever been tested in memory." The historical real-service run
  happened; denying it is as inaccurate as claiming it is reproducible now.
- "Qdrant is validated against a real service." No committed artifact supports that
  today, and local development lineage is not `LOCAL_REAL_VALIDATION`.

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

Framing:

> "In the previous production system the corpus was on the order of 3000+
> documents and 5000+ images for 200+ internal users, with short bursts around
> 10–15 QPS. That is company production context; the public repository contains
> a small sanitized subset and no production logs, so those numbers are not a
> reproducible benchmark from this repo."

> "Production model serving ran on RTX A5000 ×2, and in a later stage a
> Qwen2.5 to Qwen3-14B / Qwen3-4B gray migration was validated. Those weights, logs and
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
- Collapsing the two Qdrant states in either direction: claiming a real Qdrant
  service is currently verified (no artifact is committed), or claiming a real
  Qdrant service was never exercised (PR #6/#7 records that run). See
  [Qdrant evidence: two states, kept apart](#qdrant-evidence-two-states-kept-apart).
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
