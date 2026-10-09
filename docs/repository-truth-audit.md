# Repository Truth Audit

## Audit lineage

- Original audit base: `7b03267ccd751178e5e1d69ec6a6ec97281b57cb` (`origin/main`, before the offline merges).
- Historical merged PRs: #3, #4, #5, #6, #7. PR #6/#7 also carry a real-service
  integration execution in their development record: the offline writers were run
  against a real local Qdrant service/container together with a real local
  Elasticsearch (PR #7 exists because `search_after` sorted on `_id` and failed
  against real Elasticsearch 8). That historical run's own artifact is not
  committed; a separate current single-host real-service validation is now
  committed under `artifacts/qdrant/2026-10-09-qdrant-v1.12.0/metadata.json` — see
  [Qdrant evidence: current coverage and historical execution](#qdrant-evidence-current-coverage-and-historical-execution).
- Post-merge reconciliation: PR #9 (squash merge `b1479d8`).
- v2.5 working-milestone runtime/security validation merged via PR #13; RAGAS correctness and dependency isolation merged via PR #14 (both are part of `main` at this audit point).
- Documentation truth and validation-evidence reconciliation merged via PR #15 (squash `aa189d9`).
- Deterministic retrieval benchmark framework merged via PR #17 (squash `8446d19`). Its final tree is
  byte-identical to `main`'s, so the merged source branch `feat/reproducible-rag-benchmark` was verified
  as fully contained in `main` and deleted.
- Enterprise-readiness scope was tracked in [#20](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/20)
  (completed) and delivered by PR #21 (merged): performance artifact contract, structured
  enterprise audit, SLO/runbook, Prometheus alert rules, Grafana dashboard, optional OTLP
  exporter.
- Post-merge truth reconciliation for those capabilities was tracked in
  [#22](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/22) (completed) and
  delivered by PR #23 (merged). It corrected documentation, one internal metric naming defect and
  one unconsumed config block; it introduced no new capability and produced no new external
  validation evidence.
- [#24](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/24) is the final
  repository-truth reconciliation and is **completed** (closed 2026-10-03); PR #25
  delivered it and is **merged**. It is therefore no longer the current open scope, and no new
  reconciliation issue has replaced it — see
  [Reconciliation lineage invariants](#reconciliation-lineage-invariants).
- Runtime validation: see [v2.5 working-milestone runtime/security validation](validation/v2.5-runtime-security-validation.md)
  (local real Redis + multi-process, real nginx, authenticated Elasticsearch, real Prometheus scrape).
- Runtime-exposed metadata audit (tracked in [#45](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/45)):
  the FastAPI `description`, which ships to integrators through Swagger UI and the generated
  OpenAPI `info` block, claimed a "基于双 GPU" topology. Runtime-facing metadata is read as a
  claim about this repository, so it may only state `REPO_VERIFIED` capability; the 4B/14B vLLM
  topology stays `PENDING` and RTX A5000 ×2 stays `HISTORICAL_PRODUCTION`. The claim was removed,
  `info.title` / `info.version` still come from `config.json`, and
  `tests/test_runtime_api_metadata.py` fails if the topology claim returns. No architecture,
  model routing, config or historical production record changed.
- Repository landing (PR #55): merged as `424f43f`. It reworked
  the README top fold, added `docs/repository-metadata.md`,
  and reconciled the top-fold evidence strip with the canonical six-level taxonomy (the strip had
  rendered five rows under a three-level heading and omitted `HISTORICAL`). Documentation only.
  The technical walkthrough navigation document PR #55 also added was later removed: this
  repository carries engineering evidence only, and every current document here is indexable
  from [docs/README.md](README.md). No engineering content was lost with it.
- Bounded vLLM generation resilience contract (PR #56): merged as `0c99724`. The contract and its
  124 deterministic tests are `REPO_VERIFIED`; real vLLM runtime behaviour stays `PENDING` and is
  recorded in [External validation pending](#external-validation-pending).
- Final canonical-runtime consistency audit ([#47](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/47)):
  completed; see [docs/final-canonical-runtime-audit.md](final-canonical-runtime-audit.md). It
  re-verified the canonical surfaces against the code and config on the post-#55/#56 tree and
  produced no evidence promotion.
Reconciled candidate: `HEAD` (resolved by `scripts/check_repo_consistency.py` at verification time;
a commit cannot embed its own SHA without making the value stale).
Post-reconciliation verification date: 2026-10-06.
- Runtime version: `config.json` → `system.version` (`2.3.0`); release history is recorded in
  `CHANGELOG.md`. The repository has no GitHub Release and no tag at audit time.
- Version labelling: the string `v2.5` appears in current docs and in the filename
  `docs/validation/v2.5-runtime-security-validation.md`. It is a **historical working
  milestone / development-phase label**, not a repository release and not the canonical
  runtime version. At this verification date the canonical runtime version was `2.3.0`
  with post-release changes recorded under `[Unreleased]`; it is now `2.4.0`. Filenames
  are deliberately not renamed so existing links keep resolving.
- External validation boundary: see [External validation pending](#external-validation-pending).
  Code + deterministic tests are never evidence of real-model quality, production latency/QPS, or
  large-corpus throughput.

## Status vocabulary

The `Status` column of the audit table below uses the **canonical evidence
vocabulary** defined in
[Evidence map → Classification vocabulary](evidence-map.md#classification-vocabulary):
`HISTORICAL_PRODUCTION`, `HISTORICAL`, `REPO_VERIFIED`, `LOCAL_REAL_VALIDATION`,
`DESIGN_TARGET`, `PENDING`. That table is the only source of evidence levels in this
repository; this audit adds no status word of its own, and a retired vocabulary such
as `PARTIAL` is not a level here.

A compound cell such as `REPO_VERIFIED` (framework) / `PENDING` (result) names
one level per claim in the row, so "implemented" can never be read as "result
achieved".

A `REPO_VERIFIED` status means code exists and is covered by collected tests (and,
where noted, by CI). It never means real-model quality, production latency/QPS, or
large-corpus throughput has been established. `LOCAL_REAL_VALIDATION` means the
capability was exercised here against a real dependency on a single local host; it is
never production cluster, HA or SLO evidence. `PENDING` means the real asset, runtime
or credential is unavailable here, so the capability is not validated.

| Area | Claim | Code evidence | Test evidence | Config evidence | Runtime / CI evidence | Status | Current action |
|---|---|---|---|---|---|---|---|
| Application | FastAPI monolith is the canonical application path | `app.py`, `api/routes.py`, `api/routes_auth.py` | `tests/test_api.py`, `tests/test_pipeline.py` | `common/config.py`, `.env.example` | CI exercises Python tests; no production deployment is claimed | `REPO_VERIFIED` | Keep README quick start on `app.py` |
| FastAPI lifecycle | Startup/shutdown use a `lifespan` context manager (no deprecated `on_event`) | `app.py` | `tests/test_api.py` | n/a | CI imports the app; no deprecation warnings asserted | `REPO_VERIFIED` | Keep `on_event` out of the canonical app |
| Generation topology | One shared 4B vLLM endpoint serves rewrite + simple generation; complex generation routes to 14B | `router/stateless_router.py`, `models/llm_client.py`, `config.json` | `tests/test_architecture_contract.py`, `tests/test_pipeline_ordering.py` | `gpu1.models.vllm_4b`, `gpu0.models.gen_14b` | Unit tests only; no GPU deployment executed here | `REPO_VERIFIED` | Single-4B GPU deployment stays external validation |
| vLLM generation resilience | The canonical generation path (`route_chat`) classifies every failure, retries only transient classes (an explicit 408/429/502/503/504 allow-list; every other non-2xx, 500 included, fails closed), and is bounded by a hard attempt cap — default 2 total attempts, structural ceiling 3 — and a total request deadline; backoff is deterministic and injectable, and retries reuse the caller-resolved endpoint so a 14B request cannot be silently answered by 4B. Failures surface as a sanitised `VLLMGenerationError` carrying only class / endpoint config key / status / attempts. Counters are published onto the existing canonical collector — no second registry, exporter or endpoint | `router/vllm_resilience.py`, `router/stateless_router.py`, `monitoring/otel_tracer.py` | `tests/test_vllm_generation_resilience.py` (124 deterministic tests; the vLLM endpoint is an `httpx.MockTransport` handler and the clock is manual, so no socket, GPU or vLLM server is involved) | `VLLM_MAX_ATTEMPTS` / `VLLM_TIMEOUT_SECONDS` / `VLLM_GENERATION_DEADLINE_SECONDS` / `VLLM_RETRY_*` in `.env.example`, all clamped in code | Deterministic contract tests only. No real vLLM server has been driven through this path, no retry ratio has been observed under real load, and the new counters are not referenced by any alert rule | `REPO_VERIFIED` (contract + deterministic tests) / `PENDING` (real vLLM runtime behaviour) | Say "the bounded retry contract exists and is test-covered". Never say the vLLM path was validated against a real server, that retry amplification was measured, or that these counters are alerted on. `route_completion` (the rewrite path) is explicitly **outside** this contract — it remains single-attempt and unclassified |
| Traffic admission readiness | `GET /api/ready` fails closed (503) only when no retrieval path survives (Qdrant AND Elasticsearch) or a generation endpoint this deployment routes to is unreachable; Redis/MinIO outages are `degraded` only. `GET /api/health` keeps its diagnostic 200 + healthy/degraded contract unchanged | `api/readiness.py`, `api/routes.py`, `api/models.py` | `tests/test_readiness_contract.py`, `tests/test_readiness_endpoint.py`, `tests/deploy/test_k8s_manifests.py` | `model_routing.tiers`, `deployment_mode`, `VLLM_4B_URL`/`VLLM_GEN_14B_URL` | Deterministic tests with injected probes; K8s manifests checked statically. No probe has run against a real cluster or real vLLM servers | `REPO_VERIFIED` (contract) / `PENDING` (real cluster admission) | Real cluster deployment stays `PENDING`; the decision table must be re-derived if a degradation path changes |
| Authentication | RS256 browser tokens verify independently of the legacy HS256 secret; legacy HS256 remains an optional fallback | `auth/jwt_auth.py`, `common/auth.py`, `api/routes_auth.py` | `tests/test_jwt_auth.py`, `tests/test_auth_routes.py`, `tests/test_auth_identity_resolution.py` | `JWT_PRIVATE_KEY_PATH`/`JWT_PUBLIC_KEY_PATH`/`JWT_ALGORITHM`; `JWT_SECRET` optional | CI unit/API tests; no production identity-policy audit | `REPO_VERIFIED` | Keep RS256 primary and the HS256 compatibility path explicitly bounded |
| Login rate limiting | 5 attempts/min; Redis-backed across workers with in-memory fallback; `X-Forwarded-For` honored only behind `TRUSTED_PROXIES` | `api/routes_auth.py` | `tests/test_auth_routes.py`, `tests/integration/test_login_rate_limit_runtime.py` | `REDIS_PASSWORD`/`REDIS_CACHE_*`, `TRUSTED_PROXIES` | Local real Redis (7.4.9): 6 attempts from one IP alternating across two processes -> 6th 429; window expiry; Redis-down -> per-process memory fallback. HTTP multi-worker behind a load balancer still pending | `LOCAL_REAL_VALIDATION` | Production proxy/load-balancer topology stays deployment-specific |
| Session persistence | `SessionState` persists to Redis across workers with in-memory fallback and a stable Pydantic-safe schema | `core/pipeline_context.py` | `tests/test_pipeline_context.py`, `tests/integration/test_redis_session_runtime.py` | `REDIS_PASSWORD`/`REDIS_CACHE_*` | Local real Redis (7.4.9): write in process A, typed restore in process B, update seen in process C, TTL refresh, malformed/incompatible fallback. Cluster/Sentinel not validated | `LOCAL_REAL_VALIDATION` | Production Redis topology stays deployment-specific |
| Trusted proxy | Client IP comes from the TCP peer unless the peer is a trusted proxy; forwarded chain is walked right-to-left | `api/routes_auth.py`, `app.py` | `tests/test_auth_routes.py`, `tests/integration/test_trusted_proxy_runtime.py` | `TRUSTED_PROXIES` | Real nginx (single + multi-hop) and direct access validated; uvicorn `proxy_headers=False` required so the app policy is authoritative. Only nginx validated | `LOCAL_REAL_VALIDATION` | Other LB/proxy products need deployment-specific config |
| Observability endpoints | `/api/stats` and `/api/metrics` require an authenticated identity; `/api/health` is public | `api/routes.py` | `tests/test_metrics_endpoint.py`, `tests/test_api.py`, `tests/integration/test_metrics_auth_runtime.py` | n/a | CI API tests + local authenticated Prometheus scrape (no token 401, bearer 200, target UP, `up == 1`) | `LOCAL_REAL_VALIDATION` | Keep bearer-token scraping documented |
| Microservices | Service components exist; integrated production deployment is not established | `api-gateway/`, `retrieval-service/`, `generation-service/`, `monitoring-service/`, `cache-service/`, `rewrite-service/` | Component tests exist; no complete frontend-to-service e2e evidence | Compose files and per-service settings | CI is not a production deployment or end-to-end verification | `REPO_VERIFIED` (components) / `PENDING` (integrated deployment) | Treat as secondary components pending independent deployment validation |
| Document parsing | TXT/PDF/DOCX/XLSX parsing with scanned-PDF OCR routing | `offline/document_processor.py`, `offline/chunking.py` | `tests/offline/test_document_processor.py`, `tests/offline/test_chunking.py` | `knowledge_base.chunk_size`, `max_document_bytes`, `max_binary_document_bytes`, `xlsx_rows_per_block` | Deterministic fixtures; no real-corpus parsing benchmark | `REPO_VERIFIED` | Claim parsing implementation only |
| OCR | OCR adapter and image pipeline exist; real PaddleOCR runtime is external | `offline/image_processor.py` | `tests/offline/test_image_processing.py` (deterministic provider) | `knowledge_base.ocr.*`; optional `offline/requirements-ocr.txt` | PaddleOCR not installed in default CI; real OCR smoke not run | `REPO_VERIFIED` (adapter) / `PENDING` (real runtime) | Real PaddleOCR smoke pending external runtime |
| BGE | BGE text embedding adapter shares the online pooling contract | `offline/embeddings.py`, `offline/text_ingestion.py` | `tests/offline/*`, `tests/test_offline_text_ingestion.py`; `scripts/smoke_bge_ingestion.py` | `embedding.text.model_path`/`model_revision`/`dimension` | CI uses deterministic embedder; real model smoke `EXTERNAL_MODEL_ASSET_REQUIRED` | `REPO_VERIFIED` (adapter contract) / `PENDING` (real weights) | Real configured BGE smoke pending external asset |
| CLIP | CLIP image embedding adapter (512d) shares the online preprocessing contract | `offline/embeddings.py` | `tests/offline/test_image_processing.py` | `embedding.image_clip.*` | Deterministic embedder integration; real CLIP smoke not run | `REPO_VERIFIED` (adapter contract) / `PENDING` (real weights) | Real configured CLIP smoke pending external asset |
| Qdrant text | Text writer with epoch/seal/staging lifecycle | `offline/text_ingestion.py` | `tests/test_offline_text_ingestion.py`, `tests/offline/test_offline_end_to_end.py` | `embedding.text.collection`, `qdrant.*` | Current deterministic coverage is the in-memory `QdrantClient` (`QdrantClient(":memory:")`); no checked-in test targets a real Qdrant service. PR #6/#7 records a real local Qdrant service/container run together with Elasticsearch — development lineage, not a reproducible artifact (see below) | `REPO_VERIFIED` (deterministic coverage) / `PENDING` (real-service artifact) | Keep lifecycle contract covered; keep the two Qdrant evidence states separate |
| Qdrant image | Epoch-aware image writer; legacy points retrievable in `default` | `offline/qdrant_writer.py` | `tests/offline/test_image_processing.py` | `embedding.image_clip.collection` | Current deterministic coverage is the in-memory `QdrantClient`; the same PR #6/#7 historical real-service run applies and equally licenses no current result | `REPO_VERIFIED` (deterministic coverage) / `PENDING` (real-service artifact) | Keep epoch-isolation regression coverage |
| Elasticsearch | `cosmetics_docs` writer with explicit mapping and `search_after` epoch pagination | `offline/elasticsearch_writer.py` | `tests/offline/test_elasticsearch_writer.py`, `tests/offline/test_offline_end_to_end.py` | `elasticsearch.*`; `requirements.txt` pins client `<9` | Fake client unit tests (the fake rejects `_id` sorting) + real Elasticsearch integration; epoch reads sort on `chunk_id` | `REPO_VERIFIED` | Keep mapping and pagination-sort regression coverage |
| Elasticsearch security | Compose enables `xpack.security.enabled=true`; online/offline clients prefer env credentials over `config.json` | `docker-compose.yml`, `retrieval/bm25_retriever.py`, `common/config.py` | `tests/test_docker_compose.py`, `scripts/validation/validate_es_auth.py` | `ELASTICSEARCH_USERNAME`/`ELASTICSEARCH_PASSWORD` | Compose config parsing + local authenticated ES 8.11 (anon/wrong 401; writer mapping + `search_after`; BM25 online search). Cluster/TLS/multi-node not validated | `LOCAL_REAL_VALIDATION` | Production ES topology stays deployment-specific |
| Source state | SQLite incremental state; content-hash and permission-mask authoritative, committed only after a successful snapshot | `offline/state_store.py`, `offline/snapshot_builder.py` | `tests/offline/test_state_store.py`, `tests/offline/test_reconciliation_fixes.py` | `knowledge_base.state_db_path` | Temp DB tests; runtime DB untracked | `REPO_VERIFIED` | Do not claim an mtime/size short-circuit |
| Incremental snapshot | Incremental build produces a complete target-epoch snapshot | `offline/snapshot_builder.py` | `tests/offline/test_snapshot_builder.py`, `tests/offline/test_offline_end_to_end.py` | `knowledge_base.*` | Deterministic in-memory integration | `REPO_VERIFIED` | Keep carry-forward coverage |
| Carry-forward | Unchanged documents are copied across epochs; incompatible versions require full rebuild | `offline/carry_forward.py` | `tests/offline/test_snapshot_builder.py` | `embedding.*.model_revision` | Deterministic integration | `REPO_VERIFIED` | Keep version-compatibility guard |
| Full rebuild | Reprocesses all sources into a new epoch without auto-activation | `offline/rebuild.py`, `offline/snapshot_builder.py` | `tests/offline/test_snapshot_builder.py` | `knowledge_base.data_dir` | Deterministic integration | `REPO_VERIFIED` | Keep manual activation boundary |
| Validator | Pre-seal snapshot validation across stores | `offline/validator.py` | `tests/offline/test_snapshot_builder.py`, `tests/offline/test_offline_end_to_end.py` | `offline.scheduler.auto_seal` | Deterministic integration | `REPO_VERIFIED` | Validation failure must block sealing |
| Epoch seal | Validate-then-seal CLI; sealed epochs immutable; activation is manual | `run_offline.py`, `offline/snapshot_builder.py`, `offline/text_ingestion.py` | `tests/offline/test_cli.py`, `tests/test_offline_text_ingestion.py` | `knowledge_version_epoch` | Deterministic integration | `REPO_VERIFIED` | Keep `--skip-validation` as explicit danger only |
| CLI | Lifecycle subcommands with legacy TXT alias | `run_offline.py` | `tests/offline/test_cli.py`, `tests/test_offline_text_ingestion.py` | n/a | Deterministic tests | `REPO_VERIFIED` | Document only implemented commands |
| Scheduler | Framework-independent cycles; config-driven cadence | `offline/scheduler.py` | `tests/offline/test_scheduler.py` | `offline.scheduler.*` | Deterministic tests | `REPO_VERIFIED` | Scheduler never activates an epoch |
| Airflow integration | DAGs registered only when Airflow and modules exist | `dags/knowledge_base_dags.py` | `tests/test_knowledge_base_dags.py` | `offline.scheduler.*` cadence | Airflow not installed; no real DAG execution | `REPO_VERIFIED` (DAG registration) / `PENDING` (real execution) | Real Airflow execution pending |
| Feedback | Unified review-gated feedback pipeline | `offline/feedback_loop.py` | `tests/offline/test_feedback_loop.py` | `offline.feedback.*` | Deterministic tests | `REPO_VERIFIED` | Only `accepted` records enter training exports |
| QLoRA | Training utility exists; trained adapter is not included | `offline/finetune_qlora.py`, sample data and dedicated requirements | `tests/test_finetune_pipeline.py` (mocked); no reproducible training run | Separate optional dependency set | No training artifact or runtime validation | `REPO_VERIFIED` (utility) / `PENDING` (trained adapter) | Describe the utility only |
| AdapterManager | PEFT lifecycle code integrates with `LLMClient` | `models/adapter_manager.py`, `models/llm_client.py` | `tests/test_adapter_manager.py`, `tests/test_llm_client.py` | `config.json` adapter path and `peft_config.auto_discover` | CI covers mocked/unit paths, not external weights | `REPO_VERIFIED` (lifecycle code) / `PENDING` (external weights) | Keep asset boundary explicit |
| RRF | Weighted reciprocal rank fusion implemented in retrieval, with a single fusion entry (`rrf_fusion` inside `ParallelRecallManager.execute()`); the post-fusion step is de-duplication only | `retrieval/parallel_recall.py`, `retrieval-service/rerank/rrf_fusion.py`, `core/pipeline.py` | `tests/test_rrf_fusion.py`, `tests/test_parallel_recall.py`, `tests/test_parallel_recall_rrf_weights.py`, `tests/test_architecture_contract.py` | Fusion weights in `config.json` | CI unit coverage including a contract test that fusion runs exactly once per request and that query-aware weights survive the whole online path; no relevance benchmark | `REPO_VERIFIED` | Claim implementation only |
| BiEncoder | BiEncoder reranking implemented in the online pipeline | `retrieval/bi_encoder.py`, `core/pipeline.py` | `tests/test_bi_encoder_rerank.py` | BGE model paths in `config.json`; weights external | CI uses mocks; no production model run | `REPO_VERIFIED` (implementation) / `PENDING` (real weights + evaluation) | Model assets and evaluation are separate |
| RAGAS | Harness/reporter/validator and a 300+ entry golden set exist; single-evaluation flow, real pipeline answer/contexts, failure accounting and report provenance implemented; library `evaluate()` keeps a non-quality unavailable fallback, while `--require-ragas` fails fast with a non-zero exit and no quality report; no real quality score produced | `tests/evaluation/ragas_eval.py`, `ragas_report.py`, `validate_golden_set.py` | `tests/evaluation/test_ragas_eval.py`, `test_ragas_report.py` | Optional package omitted from default requirements; isolated `requirements-ragas.txt` (pinned, carries recorded advisories); `config.json` → `ragas` | CI deterministic evaluation guard passes without real RAGAS; real evaluator smoke and pipeline evaluation are BLOCKED (no `OPENAI_API_KEY`; latest `ragas` import-broken, importable `ragas 0.2.15` has advisories) | `REPO_VERIFIED` (harness) / `PENDING` (real quality score) | `--require-ragas` missing dependency/credential must fail fast (exit 2/3) with no report; real eval pending an approved provider |
| OpenTelemetry tracing | Tracing hook is on the online pipeline path and runs through the OTel SDK `TracerProvider`. Span attributes are reduced to an allow-list before touching the SDK. Falls back to an in-memory span buffer only when the OTel SDK is absent or provider initialization fails | `core/pipeline.py`, `monitoring/otel_tracer.py` | `tests/test_monitoring_otel.py`, `tests/monitoring/test_observability.py` | the legacy Jaeger thrift-agent block that used to sit in `config.json` has been removed; it never had a canonical reader and the OTel SDK no longer ships a Jaeger exporter; `opentelemetry-api`/`-sdk` in `requirements.txt` | CI exercises the tracer with export disabled; no committed runtime artifact demonstrates that an application span was queried from a tracing backend | `REPO_VERIFIED` (hook) | Say "tracing hook wired through the OTel SDK, export off by default". This row is about the **hook**; the exporter and its default-off state are the `OTLP export` row below |
| OTLP export | Three separate states, never merged: the exporter is **implemented**; it is **disabled by default** (`OTEL_EXPORT_ENABLED=false`, exporter package isolated in `requirements-otel.txt`, state exposed as `rag_otel_exporter_enabled`); the collector/backend closed loop is **PENDING**. Span attributes are allow-listed and every export failure is non-fatal | `monitoring/otel_exporter.py`, `monitoring/otel_tracer.py` | `tests/monitoring/test_observability.py`, `tests/test_monitoring_otel.py` | `OTEL_EXPORT_ENABLED=false` by default; `requirements-otel.txt`; `.env.example` documents the switch | Implementation is REPO_VERIFIED and test-covered. The application -> exporter -> collector -> backend -> queried-span loop is PENDING; no committed closed-loop runtime evidence artifact exists in this repository, and no committed artifact demonstrates that an application span was queried from a Jaeger/OTLP backend | `REPO_VERIFIED` (exporter) / `PENDING` (closed loop) | Say "exporter implemented and test-covered, disabled by default, closed loop pending" — never "OTLP tracing validated", "Jaeger operational" or "production tracing complete" |
| RBAC | Auth and bitmask authorization code exists under a uint32 mask contract; missing/malformed metadata fails closed, and text/image retrieval plus `/api/media/{doc_id}` are epoch/RBAC aware | `auth/`, `common/auth.py`, `retrieval/parallel_recall.py`, `api/routes.py` | `tests/test_bitmask_rbac.py`, `tests/test_retrieval_authorization_contract.py`, `tests/test_media_route.py`, `tests/offline/test_image_processing.py` | `config.json` RBAC section and environment settings | CI unit/API tests; no production policy audit | `REPO_VERIFIED` | Describe implemented paths without deployment claims |
| Cache | Cache implementations and metrics exist; full invalidation model not certified | `cache/`, cache service, metrics code | `tests/test_cache.py`, `tests/test_metrics_endpoint.py` | Cache settings in `config.json` | CI unit tests; no workload benchmark | `REPO_VERIFIED` (implementation) / `PENDING` (full invalidation model) | Keep full design behavior unverified |
| Frontend contract | Four claims that must never be merged into one: **(A)** the React client reads auth/RBAC metadata and routes single-query, chat, session and stats panels to the monolith API; **(B)** GitHub Actions installs from the lockfile and builds the frontend on every push/PR; **(C)** frontend + real backend end-to-end runtime integration is not established; **(D)** production deployment is deployment-specific and not a repository claim | (A) `frontend/src/App.jsx`, `api/routes_auth.py` metadata endpoint. (B) `.github/workflows/ci.yml` job `frontend-build` (`npm ci` then `npm run build`). (C) `docs/demo/capture_demo.py` drives the real UI against the synthetic `docs/demo/mock_api.py`, never the monolith — a demo capture, not E2E | (A) `tests/test_auth_metadata.py`. (B) the build *is* the gate: `frontend-build` is a required check in `docs/main-branch-governance.md`, so there is no separate pytest target and none is implied. (C) no committed artifact shows a browser-rendered frontend against the real backend | `config.json` UI metadata; `frontend/package-lock.json` (lockfileVersion 3, so `npm ci` resolves nothing floating); `frontend/.gitignore` keeps `dist/` untracked | (B) `frontend-build` runs `npm ci` and `npm run build` and fails the merge gate if either fails; a separate `continue-on-error` `npm audit` step reports 4 transitive build-toolchain advisories non-blockingly (see [Known frontend dependency advisories](#known-frontend-dependency-advisories-disclosed-not-gating)). (C) PENDING — no browser run against the real monolith + real ES/Qdrant + real models has ever been recorded as an artifact here. (D) PENDING — deployment state lives outside this repository | `REPO_VERIFIED` (A client + metadata contract) / `REPO_VERIFIED` (B CI build gate) / `PENDING` (C end-to-end runtime integration) / `PENDING` (D production deployment) | Say "the frontend builds in CI". Never say the frontend is validated end-to-end, browser-verified against the real backend, or deployed. A green `frontend-build` proves the bundle compiles and nothing more |
| Performance | PRD P95/P99/QPS values are design targets | Benchmark utilities exist under `tests/load/`; no reproducible artifact checked in | Load-test code is not a benchmark result | Target values in `config.json` / PRD | CI does not establish production latency/throughput/accuracy | `DESIGN_TARGET` (PRD objectives) / `PENDING` (measured result) | Label numbers as design targets |
| Retrieval benchmark | Deterministic retrieval-quality harness exists (Recall@1/3/5/10, HitRate@1/3/5/10, MRR@10, binary NDCG@10) with provenance and artifact contract | `benchmarks/`, `artifacts/benchmarks/` | `tests/benchmark/` (deterministic tests, fixture retriever only) | Bucket support: overall / business_type / difficulty; `visual_required` and complexity labels absent (see `docs/benchmark-data-quality.md`) | Framework = REPO_VERIFIED; no real benchmark artifact exists, so the result is PENDING. Configurations currently report BLOCKED (no live Elasticsearch/Qdrant, no BGE weights, no corpus containing the ground truth) | `REPO_VERIFIED` (framework) / `PENDING` (result) | Never publish numbers from a blocked or fixture-retriever run |
| Performance evidence | Deterministic seven-file artifact contract under `artifacts/performance/`; status `EXECUTED`/`PARTIAL`/`BLOCKED` derived from observation; unmeasured values are `null`, never `0`; unrun work is blocked with a reason | `benchmarks/performance.py`, `benchmarks/performance_cli.py`, `tests/load/locustfile.py` | `tests/performance/` (null-vs-zero, status derivation, provenance, redaction) | `artifacts/performance/README.md` documents the contract; generated runs are git/docker-ignored | Framework = REPO_VERIFIED. No artifact is committed, so no QPS/P95/P99 is measured: result = PENDING. A run against an unreachable target was executed and correctly recorded BLOCKED | `REPO_VERIFIED` (framework) / `PENDING` (result) | Never publish a number from a run that did not execute |
| Structured audit trail | Business-action events with a stable 9-field schema; redaction enforced on every emit; request-id correlation via contextvar; Redis Stream + daily JSONL persistence; no activate event because no activate endpoint exists | `common/audit.py`, `api/routes_auth.py`, `api/routes.py`, `run_offline.py` | `tests/test_audit_log.py` (schema, three outcomes, key/value redaction, correlation, real login path) | `logs/audit/<date>.jsonl`; Redis Stream `audit:events` capped at 10000 | Implementation is REPO_VERIFIED. No SIEM forwarding and no production audit review; that is a deployment concern, not a repository claim | `REPO_VERIFIED` (implementation) | Keep audit stdout structured for external forwarding |
| SLO + incident runbook | Five objectives and eight incident procedures written against the degradation paths that exist in code | `docs/slo-runbook.md` | Docs-consistency guards assert the objectives stay `DESIGN_TARGET` | Alert names map 1:1 to `monitoring/prometheus/alerts.yml` | Document is REPO_VERIFIED. Every objective is a `DESIGN_TARGET`; no SLO has been met and none has been measured | `REPO_VERIFIED` (document) / `DESIGN_TARGET` (objectives) | Do not restate a target as an achievement |
| Prometheus alerting | Six rules over metrics the canonical collector actually emits; malformed summary exposition fixed; two guards against traffic-less firing | `monitoring/prometheus/alerts.yml`, `monitoring/otel_tracer.py`, `api/middleware.py`, `core/pipeline_context.py` | `tests/monitoring/` asserts every referenced metric is emitted, exposition is well-formed, and no placeholder gauge backs an alert | `deploy/prometheus.yml` scrapes the monolith with `bearer_token_file` | Configuration is REPO_VERIFIED. No production Prometheus evaluates these rules, and no alert has fired in production | `REPO_VERIFIED` (configuration) / `PENDING` (fired in production) | Qdrant/Elasticsearch outages are deliberately unalerted; the metric is missing and that gap is documented |
| In-process AlertingManager | A second, older threshold engine exists in `monitoring/otel_tracer.py` but is **not wired into the canonical request path** | `monitoring/otel_tracer.py` (`AlertingManager`); referenced by no module under `app.py`, `api/`, `core/pipeline.py` or `core/pipeline_context.py` | `tests/test_monitoring_subsystem.py` exercises it directly; `tests/monitoring/test_alerting_reachability.py` asserts it stays unwired | The `config.json` → `alerting.rules` block that fed it was removed — it had no canonical consumer and read as a second production alert contract | Never runs in the deployed application, so there is no runtime evidence of any kind, not even local | `HISTORICAL` | Deprecated/legacy. Never describe these rules as "the Prometheus rules"; the canonical contract is `monitoring/prometheus/alerts.yml`. Two of its default rules (`kv_utilization`, `rerank_batch_queue_delay_p99`) have no canonical producer, so they could not fire regardless |
| Grafana dashboard | Ten panels over emitted metrics only | `monitoring/grafana/dashboards/rag-overview.json` | `tests/monitoring/` asserts panel metrics exist and no hallucination/RAGAS/GPU panel exists | Provisioned via `monitoring/grafana/provisioning/` | JSON is REPO_VERIFIED. Never imported into a running Grafana, so panel population is PENDING | `REPO_VERIFIED` (JSON) / `PENDING` (live panel population) | Optional overlay only; the canonical deployment does not start Grafana |
| Runtime version | Runtime version agrees with newest dated changelog release | `common/config.py` reads `config.json` | Consistency script checks the invariant | `config.json` `system.version` = `2.3.0` | `scripts/check_repo_consistency.py` runs in CI | `REPO_VERIFIED` | Keep the check enabled; Unreleased does not bump version |
| CI | Gates include a frontend build from the lockfile (`npm ci` + `npm run build`) alongside Python tests, compile, collection and repository consistency | `.github/workflows/ci.yml` (jobs `frontend-build`, `build-sanity`, `enterprise-readiness`, `test`, `evaluation-guard`) | Workflow runs pytest and collection checks; `frontend-build` compiles the bundle on every push/PR | Workflow configuration | Verify the current `HEAD` GitHub Actions run for CI | `REPO_VERIFIED` (gate configuration) / `PENDING` (any runtime or deployment state derived from a green gate) | Require green checks on the reconciliation PR; a green `frontend-build` is compilation evidence only — see the `Frontend contract` row for what it does not prove |
| Ruff | Lint and formatting are configured as CI checks | `.github/workflows/lint.yml` | Ruff check and format check | Workflow configuration | Verify the current `HEAD` GitHub Actions run for Ruff | `REPO_VERIFIED` | Require green checks on the reconciliation PR |
| Security | Python dependency audit and secret scanning configured in CI; RAGAS excluded from default install | `.github/workflows/security.yml`, `requirements.txt` | CI runs pip-audit and secret scan | RAGAS is opt-in | A green security workflow does not imply optional dependency safety; see the frontend advisory note below | `REPO_VERIFIED` | Do not infer optional dependency safety |
| Runtime artifacts | Tracked PID/stopped markers were tool state, not product files | Exact markers removed | Consistency check rejects tracked PID/state markers | `.gitignore` excludes local state paths | Verify using `git ls-files` and CI invariant | `REPO_VERIFIED` | Keep runtime state untracked |
| Documentation governance | Current guides and historical documents have separate roles, and the two roles have separate locations | `docs/README.md` and the current guides; superseded plans formerly stored under `docs/superpowers/` were removed from this branch and live only in Git history | Consistency checks link/path, doc-placement and stale-claim invariants | No runtime config claim | CI validates stable docs invariants | `REPO_VERIFIED` | Keep plans/specs out of current implementation evidence, and never present a removed path as one a reader can still open |

## External validation tracker map

Repository-side GitHub state is governance audit, not an offline observation, so it is recorded
here for human readers and is deliberately **not** something
`scripts/check_repo_consistency.py` can verify on its own. The states below are a **snapshot**
taken by re-querying the GitHub API at the verification date recorded in
[Audit lineage](#audit-lineage); re-query GitHub before relying on any row. The guard reads only
the working tree, so it never claims to know whether an issue is open right now — what it
enforces is that a transition nobody recorded cannot pass silently.

- PR #15 — repository truth and validation-evidence reconciliation: merged.
- PR #17 — deterministic retrieval benchmark **framework**: merged.
- [#16](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/16) — benchmark framework
  implementation scope: closed (`completed`), delivered by PR #17.
- [#18](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/18) — **real** retrieval
  benchmark execution: open. Two structural blockers keep it open: no real retrieval executor is
  wired for CLI runs, and no independent corpus covers the golden-set relevance truth.
- [#20](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/20) — enterprise-readiness
  evidence loop: completed, delivered by PR #21.
- [#22](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/22) — post-merge truth
  reconciliation: completed, delivered by PR #23.
- [#24](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/24) — final
  repository-truth reconciliation: completed (closed 2026-10-03), delivered by
  PR #25 (merged 2026-10-03). It closed the enterprise RAG security gap and reconciled the
  OTLP/Jaeger config surface and the Qdrant evidence wording; see
  [Reconciliation lineage invariants](#reconciliation-lineage-invariants) for why the lineage
  anchor is the issue and never the PR.
- Current reconciliation scope: **none**. Every reconciliation issue listed above is closed
  and no new reconciliation issue has been opened, so there is no open reconciliation scope
  at this audit point. No issue number is invented to fill this slot — a repository between
  reconciliations legitimately has none.
- [#47](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/47) — final
  canonical-runtime consistency audit before applications: completed (closed by this audit), delivered
  by the audit recorded in [docs/final-canonical-runtime-audit.md](final-canonical-runtime-audit.md).
  It re-verified the architecture, topology, taxonomy, scale and evidence-boundary claims against the
  code and config on the post-#55/#56 tree, and produced no evidence promotion.
- [#54](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/54) — real Kubernetes
  deployment and readiness smoke (`deploy/k8s/` manifests applied to a real cluster, `/api/ready`
  observed flipping under a real dependency outage): open. The manifests and their 31 static checks
  (`tests/deploy/test_k8s_manifests.py`, all offline) are `REPO_VERIFIED`; no cluster has ever
  applied them, so the Kubernetes deployment row stays `PENDING`.
- [#8](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/8) — umbrella external
  validation (real BGE / CLIP / PaddleOCR / Airflow / benchmark artifact): open.
- [#12](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/12) — runtime / security
  external validation (real RAGAS, 4B/14B vLLM GPU topology): open.
- [#32](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/32) — deferred real
  performance artifact and observability closed-loop evidence (live performance artifact, OTLP
  exporter → collector → queried span, live Grafana panels, a controlled Prometheus alert
  firing/resolution path): open. It is validation-only and explicitly does not block
  repository or document governance work.

Issue #20 being completed records that the *implementation scope* shipped. It does **not**
record that any of its results were observed: no performance artifact, no production alert
firing, no traced span. Those stay `PENDING` and keep #8, #12, #18 and #32 open.

Closing #16 records that the framework scope is delivered. It does **not** record a benchmark result:
no artifact exists, so `benchmark result` stays `PENDING` and both execution blockers (no real
retrieval executor, no independent corpus) stay open in #18.

Closing #22 likewise records only that the post-merge truth reconciliation was delivered. It does
**not** record external validation evidence, so #8, #12, #18 and #32 stay open.

Closing #24 is the same kind of record: documentation, guard and bounded security-test work.
Completing it did not close #8, #12, #18 or #32, and it did not produce a benchmark result, a
performance artifact, a traced span or a fired production alert. Every one of those rows stays
`PENDING`. #32 was opened afterwards to consolidate the deferred performance and observability
runtime evidence; it is a validation tracker, not a reconciliation scope, so opening it did not
create a current reconciliation issue.

## Reconciliation lineage invariants

Reconciliation lineage is anchored on the **tracking issue**, never on the PR number.

- The audit must record each completed reconciliation issue as completed, and — only while one
  is actually open — name that one as the current scope. `COMPLETED_RECONCILIATION_ISSUES` and
  `CURRENT_RECONCILIATION_ISSUE` in `scripts/check_repo_consistency.py` hold those numbers.
- **No open reconciliation issue is a legal state.** `CURRENT_RECONCILIATION_ISSUE` is then
  `None` and the tracker map says so explicitly (see
  [External validation tracker map](#external-validation-tracker-map)). The guard refuses an
  audit that omits the declaration, and it refuses a model that points
  `CURRENT_RECONCILIATION_ISSUE` at an issue also listed in `COMPLETED_RECONCILIATION_ISSUES`.
  Reviving a closed issue to satisfy an "exactly one current issue" invariant would reintroduce
  the very drift below, so the invariant is not maintained that way.
- Those numbers move in the same commit that closes the issue and updates this document, exactly
  like `OPEN_EXTERNAL_VALIDATION_TRACKERS` above. A new reconciliation issue supersedes the
  previous one; it does not extend it.
- PR numbers stay historical narration in this document. No invariant may claim that a named PR is
  the latest or the current one: that is false as soon as the next PR exists, and the next PR is
  not a repository-truth event. Adding PR #26 does not invalidate anything recorded here; closing
  #24 did, and the model was updated in the same commit that recorded the closure rather than
  left to go stale.
- Completed-versus-current is the distinction that carries truth. A reconciliation can be merged
  while every external-validation row stays `PENDING`, so the two are never derived from each
  other.
- These recorded states are a **governance snapshot**, taken by re-querying the GitHub API at the
  verification date in [Audit lineage](#audit-lineage). `scripts/check_repo_consistency.py` runs
  offline against the working tree: it verifies that this document and the guard's constants agree,
  and it cannot observe whether an issue is open on GitHub right now. Re-query GitHub before
  relying on any row above.

## External validation pending

These are implemented in code but not validated against real external assets/runtimes here:

- Real configured BGE model smoke — `EXTERNAL_MODEL_ASSET_REQUIRED`.
- Real configured CLIP model smoke — `EXTERNAL_MODEL_ASSET_REQUIRED`.
- Real PaddleOCR smoke — external runtime not installed.
- Real Airflow DAG execution — Airflow not installed.
- Real Qdrant service/container validation as a reproducible artifact — **committed** at
  `artifacts/qdrant/2026-10-09-qdrant-v1.12.0/metadata.json`, from a single-host run against
  `qdrant/qdrant:v1.12.0` with deterministic vectors (epoch point ids, payload filters, RBAC
  re-filter, text+image fusion, Qdrant-down degradation). This is `LOCAL_REAL_VALIDATION`; it is
  not a cluster/HA/throughput result and does not measure model quality. The PR #6/#7 development
  round remains separate lineage; see
  [Qdrant evidence: current coverage and historical execution](#qdrant-evidence-current-coverage-and-historical-execution).
- Production evaluation / benchmark — no reproducible artifact checked in. The benchmark framework
  exists and is `REPO_VERIFIED`; two structural blockers keep execution open: **no real retrieval
  executor is wired for CLI runs**, and **no independent corpus** covers the golden-set relevance
  truth (building one from `golden_set.contexts` would be label leakage). See
  [benchmark data quality](benchmark-data-quality.md) and
  [artifacts/benchmarks/README.md](../artifacts/benchmarks/README.md).
- Real RAGAS evaluation with a permitted dependency and evaluator API key — not run: no
  `OPENAI_API_KEY`, `ragas 0.4.x` is import-broken, and the importable `ragas 0.2.15` pulls
  `langchain 0.3.x` with advisories (see [real RAGAS validation](validation/real-ragas-evaluation.md)).
- Single shared 4B vLLM GPU deployment (and 14B routing) — model weights absent, `vllm` not installed.
- Real vLLM runtime behaviour of the bounded generation resilience contract — no real vLLM server has been
  driven through `route_chat` in this repository. The failure classification, attempt cap, deadline,
  backoff determinism, sanitised errors and metric counts are `REPO_VERIFIED` by deterministic test; whether
  the taxonomy and the default bounds match a real vLLM's actual failure modes, and what the retry ratio
  looks like under real load, are unobserved. The new `rag_vllm_generation_*` counters are also not
  referenced by any alert rule, so no alert has been derived from them. See the
  `vLLM generation resilience` row in the audit table above
  ([Status vocabulary](repository-truth-audit.md#status-vocabulary)).
- Frontend + real backend end-to-end runtime integration — the CI `frontend-build` gate compiles the
  bundle from the lockfile and nothing more. No committed artifact shows a browser-rendered
  `frontend/` against the real monolith with real Elasticsearch/Qdrant and real models. The Playwright
  demo capture runs the real UI against the synthetic `docs/demo/mock_api.py`, so it is a demo
  capture and explicitly **not** end-to-end evidence.
- Frontend production deployment — deployment-specific state that lives outside this repository, so
  it is never asserted here. A green `frontend-build` is neither a deployment nor a runtime check.
- Production Redis topology (cluster/Sentinel) and HTTP multi-worker behind a load balancer.
- Non-nginx reverse proxies (Cloudflare / ALB / Traefik) — require deployment-specific configuration.

Validated locally on 2026-10-02 as `LOCAL_REAL_VALIDATION` (see
[v2.5 working-milestone runtime/security validation](validation/v2.5-runtime-security-validation.md)):
real Redis multi-process session persistence and login rate limiting, real nginx proxy-trust resolution,
authenticated Elasticsearch online + offline paths, and an authenticated Prometheus scrape. Local
`LOCAL_REAL_VALIDATION` is not a production benchmark, does not imply production HA/SLO, and must not be
rewritten as "never validated".

Qdrant is deliberately **not** in that list: that round covered Redis, nginx, Elasticsearch and
Prometheus only. Qdrant's own two evidence states are recorded separately below, because "not in the
2026-10-02 round" must not collapse into "never run against a real service".

## Qdrant evidence: current coverage and historical execution

Qdrant has two independent evidence states. Both are true. Collapsing them in either direction is
the drift this section exists to prevent.

| State | What it is | What it is not |
|---|---|---|
| Current deterministic coverage | The deterministic tests use the in-process `QdrantClient` (`QdrantClient(":memory:")`) in `tests/test_offline_text_ingestion.py`, `tests/offline/test_offline_end_to_end.py`, `tests/offline/test_snapshot_builder.py`, `tests/offline/test_image_processing.py`. Re-running the suite reproduces exactly this. | It is not a real-service test, and no collected test connects to a Qdrant service by default. |
| Current real-service validation | A single-host run against `qdrant/qdrant:v1.12.0` is committed at `artifacts/qdrant/2026-10-09-qdrant-v1.12.0/metadata.json` and reproducible via `tests/integration/test_qdrant_store_runtime.py` / `scripts/validation/validate_qdrant_store.py`. It validates epoch point ids, payload (status/epoch) filters, RBAC re-filter before fusion, text+image `rrf_fusion` merge and Qdrant-down degradation, with deterministic vectors. Evidence level `LOCAL_REAL_VALIDATION`. | It is not a production cluster / HA / throughput result, and it does not measure BGE/CLIP model quality. |
| Historical development execution | The PR #6/#7 development record states the writers ran against a real local Qdrant service/container, in the same round as the real local Elasticsearch run that produced PR #7 (`search_after` sorted on `_id`, which real Elasticsearch 8 rejects). | It is not itself a committed, reproducible artifact: no artifact of that specific run is committed, and nothing re-runs it. |

Consequences, each of which is a boundary and not a goal:

1. The historical execution is **development lineage**, and the current committed single-host run is a
   local real-service validation. Neither may be described as production Qdrant validation.
2. Neither establishes **anything** about production Qdrant HA, cluster topology or replication,
   cluster or index performance, retrieval or model quality, QPS or latency. A single-host run with
   deterministic vectors is not a capacity or quality measurement.
3. The `LOCAL_REAL_VALIDATION` recorded for Elasticsearch comes from the separate 2026-10-02 round in
   [v2.5 working-milestone runtime/security validation](validation/v2.5-runtime-security-validation.md),
   not from the PR #6/#7 round. The two are separate records and must not be merged.
4. Production Qdrant cluster, HA, throughput and TLS remain `PENDING`; the Qdrant rows are
   `LOCAL_REAL_VALIDATION` (single-host real service) / `REPO_VERIFIED` (deterministic coverage).
5. Claiming "Qdrant has only ever been tested in memory" is false by the historical record and the
   committed run; claiming "Qdrant is production-validated" is unsupported because the committed run
   is a single local host. `scripts/check_repo_consistency.py` rejects the erasure and the
   repository-level contradiction.

## Known frontend dependency advisories (disclosed, not gating)

`npm ci` in `frontend/` currently reports 4 advisories: 1 moderate
(`baseline-browser-mapping`) and 3 high (`browserslist`, `nanoid`, `postcss`).

Scope, stated precisely:

- All four are **transitive** build-toolchain dependencies reached through
  `vite`. None is declared in `frontend/package.json`.
- They are build-time tooling, not code the browser executes as application
  logic. `postcss` and `browserslist` run at build time; `nanoid` and
  `baseline-browser-mapping` are transitive build-tool dependencies.
- This is therefore **not** a claim of a production runtime vulnerability, and
  not a claim of safety either.

Why it is not a merge gate: the registry's quick-audit endpoint is deprecated
and currently returns HTTP 400, so gating on it would produce a flaky red build
rather than a real signal. CI reports the advisories as a non-blocking step so
they stay visible. Remediation (a lockfile refresh plus a verified frontend
build) is open work, not something this reconciliation silently performed.

## Enterprise readiness: four layers kept separate

Each capability above has four independent states. Collapsing any two of them is
the drift this audit exists to prevent. Each state has exactly one canonical level:

- **Implemented** — code/config exists in this repository (`REPO_VERIFIED`, existence).
- **Tested** — covered by collected deterministic tests (`REPO_VERIFIED`, coverage).
- **Runtime validated** — exercised here against a real dependency on a single host
  (`LOCAL_REAL_VALIDATION`).
- **Pending external** — needs an asset, credential or environment this repository
  lacks (`PENDING`).

Concrete non-equivalences, each enforced by a guard:

- Alert rules existing does not mean any alert has fired in production.
- An OTLP exporter existing does not mean a tracing closed loop was verified.
- A performance artifact framework existing does not mean QPS or P95 were measured.
- An SLO being defined does not mean the SLO was met.
- Audit events being emitted does not mean they were reviewed or forwarded to a SIEM.
- `monitoring/prometheus/alerts.yml` existing does not mean the in-process
  `AlertingManager` is part of it. That class is legacy and unwired; it is a
  *second* mechanism, not a second half of the canonical one.

## Two alerting mechanisms, and which one is canonical

The repository contains two threshold engines. They must never be described
interchangeably.

| Mechanism | What evaluates it | Canonical? |
|---|---|---|
| `monitoring/prometheus/alerts.yml` | An external Prometheus server loading the file via `deploy/prometheus.yml` | **Yes.** This is the alert contract. Its 6 rules only reference `/api/metrics` series the collector really emits, and `tests/monitoring/test_prometheus_alerts.py` enforces that correspondence. |
| `monitoring/otel_tracer.py::AlertingManager` | Nothing. It is an in-process engine and no canonical module constructs it | No. Legacy / not wired. |

Reachability evidence for the second row: `app.py`, `api/*`, `core/pipeline.py` and
`core/pipeline_context.py` contain no reference to `AlertingManager` or
`check_alerts()`. `tests/monitoring/test_alerting_reachability.py` asserts this, so
wiring it up later fails loudly instead of silently creating a second contract.

Consequences recorded honestly:

- The `config.json` → `alerting.rules` block that used to feed it was removed. It had
  no canonical consumer and its presence in `config.json` read like a second
  production alert contract. JSON cannot carry a comment, so the honest options were
  "delete it" or "keep a misleading second contract"; it was deleted.
- Two of its default rules depend on `kv_utilization` and
  `rerank_batch_queue_delay_p99`, neither of which has a producer on the canonical
  path. They could never fire even if the class were wired.
- Its metric names are internal dotted collector names (`cache.total`,
  `prefix_cache.hit`), not `rag_*` Prometheus series. Do not translate between them
  by eye.

`scripts/check_repo_consistency.py` derives each result's evidence state from the
working tree (`artifacts/performance/*/metadata.json`,
`monitoring/evidence/*.json`, the alert file, the dashboard directory). For the
OTLP runtime-evidence candidate, path presence alone is insufficient: the JSON
must satisfy the repository's minimum structural contract — `schema_version` 1,
`evidence_type` `otel_closed_loop`, `status` `EXECUTED`, a non-empty `backend`, a
32-hex `trace_id`, and `queried_span_count` > 0. That is structural validation
only: it cannot show the artifact was not hand-written, that the backend was
really reached, or that the trace corresponds to any real request, so it is not
provenance or authenticity verification. The required wording therefore follows
the repository's observed evidence state instead of a hardcoded expectation.

## Architecture debt — free-text semantic governance

The drift guards in `scripts/check_repo_consistency.py` enforce repository-truth
invariants by parsing natural-language documentation with scoped regexes. This
works today, but free-text semantic matching has an unbounded edge space: every
new phrasing requires another pattern, which is ongoing maintenance cost rather
than a one-time fix.

Future improvement (deliberately out of scope for this reconciliation):
replace the free-text semantic regex governance with structured repository
facts / a machine-readable truth schema, and validate documents against that
schema instead of against prose.

## Offline history check

Before the Phase 1 text-ingestion slice, `offline/` contained only the QLoRA files. Phase 1 added
the TXT processor, text embedding adapters, and Qdrant text writer. This reconciliation adds the
multi-format processor, OCR/image pipeline, CLIP adapter, Qdrant image writer, Elasticsearch writer,
incremental state, carry-forward, full rebuild, validator, lifecycle CLI, scheduler, and feedback
pipeline.

The historical 2.3.0 changelog claim that named broader ingestion modules is retained unchanged as
release history. It is not evidence that those capabilities existed at the 2.3.0 point in time: at
reconciliation the corresponding implementation and Git history were absent, and the later
re-implementation does not retroactively make the original 2.3.0 claim true. The `[Unreleased]`
correction in `CHANGELOG.md` explains this without rewriting the historical entry.

`tests/contracts/offline_pipeline_contract.py` and `tests/contracts/archive_expired_contract.py` are
deliberately not named `test_*.py`, so pytest does not collect them. They preserve earlier proposed
acceptance behavior and are not regression tests or implementation evidence.

## Version policy

`config.json` `system.version` is the canonical runtime version. The newest dated version in
`CHANGELOG.md` must match it. `Unreleased` records changes after that release without assigning a new
runtime version. Git tags/releases are separate publication decisions; no GitHub Release exists at
this audit point.

### `v2.5` is a working-milestone label, not a release

Several current docs and one filename carry a `v2.5` label (for example
`docs/validation/v2.5-runtime-security-validation.md`). That label names a **historical working
milestone / development phase** of the runtime-and-security reconciliation work. It is deliberately
**not**:

- a repository release — the newest dated release heading in `CHANGELOG.md` is `[2.4.0]`;
- the canonical runtime version — `config.json` → `system.version` is `2.4.0`;
- a Git tag or GitHub Release — neither exists.

Changes after `2.4.0` are recorded under `[Unreleased]` and do not bump the runtime version. The
filename and the `v2.5` label are retained rather than renamed so existing cross-document links keep
resolving; a large rename would create link breakage for no truth gain. `scripts/check_repo_consistency.py`
enforces that `v2.5` is never described as a formal runtime release.

> **Scope of the version figures in this document.** The rows above and in
> [Version labelling](#version-labelling) are current and track `config.json`. The
> remaining `2.3.0` figures elsewhere in this file — the audit lineage block dated
> 2026-10-06, the evidence-table row for the runtime-version invariant, and the
> offline-capability correction — are **point-in-time records of the 2026-10-06
> verification**, when `2.3.0` was the canonical runtime version and everything after
> it sat under `[Unreleased]`. They are deliberately not rewritten by the 2.4.0
> release; the same applies to `docs/repository-drift-report.md` and
> `docs/final-canonical-runtime-audit.md`. Read them as history, not as the current
> version.
