# Repository Truth Audit

## Audit lineage

- Original audit base: `7b03267ccd751178e5e1d69ec6a6ec97281b57cb` (`origin/main`, before the offline merges).
- Historical merged PRs: #3, #4, #5, #6, #7.
- Post-merge reconciliation: PR #9 (squash merge `b1479d8`).
- v2.5 runtime/security validation merged via PR #13; RAGAS correctness and dependency isolation merged via PR #14 (both are part of `main` at this audit point).
- Runtime validation: see [v2.5 runtime/security validation](validation/v2.5-runtime-security-validation.md)
  (local real Redis + multi-process, real nginx, authenticated Elasticsearch, real Prometheus scrape).
Reconciled candidate: `HEAD` (resolved by `scripts/check_repo_consistency.py` at verification time;
a commit cannot embed its own SHA without making the value stale).
Post-reconciliation verification date: 2026-10-02.
- Runtime version: `config.json` → `system.version` (`2.3.0`); release history is recorded in
  `CHANGELOG.md`. The repository has no GitHub Release at audit time.
- External validation boundary: see [External validation pending](#external-validation-pending).
  Code + deterministic tests are never evidence of real-model quality, production latency/QPS, or
  large-corpus throughput.

Status values: `VERIFIED`, `PARTIAL`, `PLANNED`, `BROKEN`, `STALE`, `HISTORICAL`.

A `VERIFIED` status means code exists and is covered by collected tests (and, where noted, by a real
local service integration). It never means real-model quality, production latency/QPS, or
large-corpus throughput has been established.

| Area | Claim | Code evidence | Test evidence | Config evidence | Runtime / CI evidence | Status | Current action |
|---|---|---|---|---|---|---|---|
| Application | FastAPI monolith is the canonical application path | `app.py`, `api/routes.py`, `api/routes_auth.py` | `tests/test_api.py`, `tests/test_pipeline.py` | `common/config.py`, `.env.example` | CI exercises Python tests; no production deployment is claimed | VERIFIED | Keep README quick start on `app.py` |
| FastAPI lifecycle | Startup/shutdown use a `lifespan` context manager (no deprecated `on_event`) | `app.py` | `tests/test_api.py` | n/a | CI imports the app; no deprecation warnings asserted | VERIFIED | Keep `on_event` out of the canonical app |
| Generation topology | One shared 4B vLLM endpoint serves rewrite + simple generation; complex generation routes to 14B | `router/stateless_router.py`, `models/llm_client.py`, `config.json` | `tests/test_architecture_contract.py`, `tests/test_pipeline_ordering.py` | `gpu1.models.vllm_4b`, `gpu0.models.gen_14b` | Unit tests only; no GPU deployment executed here | VERIFIED | Single-4B GPU deployment stays external validation |
| Authentication | RS256 browser tokens verify independently of the legacy HS256 secret; legacy HS256 remains an optional fallback | `auth/jwt_auth.py`, `common/auth.py`, `api/routes_auth.py` | `tests/test_jwt_auth.py`, `tests/test_auth_routes.py`, `tests/test_auth_identity_resolution.py` | `JWT_PRIVATE_KEY_PATH`/`JWT_PUBLIC_KEY_PATH`/`JWT_ALGORITHM`; `JWT_SECRET` optional | CI unit/API tests; no production identity-policy audit | VERIFIED | Keep RS256 primary and the HS256 compatibility path explicitly bounded |
| Login rate limiting | 5 attempts/min; Redis-backed across workers with in-memory fallback; `X-Forwarded-For` honored only behind `TRUSTED_PROXIES` | `api/routes_auth.py` | `tests/test_auth_routes.py`, `tests/integration/test_login_rate_limit_runtime.py` | `REDIS_PASSWORD`/`REDIS_CACHE_*`, `TRUSTED_PROXIES` | Local real Redis (7.4.9): 6 attempts from one IP alternating across two processes -> 6th 429; window expiry; Redis-down -> per-process memory fallback. HTTP multi-worker behind a load balancer still pending | VERIFIED | Production proxy/load-balancer topology stays deployment-specific |
| Session persistence | `SessionState` persists to Redis across workers with in-memory fallback and a stable Pydantic-safe schema | `core/pipeline_context.py` | `tests/test_pipeline_context.py`, `tests/integration/test_redis_session_runtime.py` | `REDIS_PASSWORD`/`REDIS_CACHE_*` | Local real Redis (7.4.9): write in process A, typed restore in process B, update seen in process C, TTL refresh, malformed/incompatible fallback. Cluster/Sentinel not validated | VERIFIED | Production Redis topology stays deployment-specific |
| Trusted proxy | Client IP comes from the TCP peer unless the peer is a trusted proxy; forwarded chain is walked right-to-left | `api/routes_auth.py`, `app.py` | `tests/test_auth_routes.py`, `tests/integration/test_trusted_proxy_runtime.py` | `TRUSTED_PROXIES` | Real nginx (single + multi-hop) and direct access validated; uvicorn `proxy_headers=False` required so the app policy is authoritative. Only nginx validated | VERIFIED | Other LB/proxy products need deployment-specific config |
| Observability endpoints | `/api/stats` and `/api/metrics` require an authenticated identity; `/api/health` is public | `api/routes.py` | `tests/test_metrics_endpoint.py`, `tests/test_api.py`, `tests/integration/test_metrics_auth_runtime.py` | n/a | CI API tests + local authenticated Prometheus scrape (no token 401, bearer 200, target UP, `up == 1`) | VERIFIED | Keep bearer-token scraping documented |
| Microservices | Service components exist; integrated production deployment is not established | `api-gateway/`, `retrieval-service/`, `generation-service/`, `monitoring-service/` | Component tests exist; no complete frontend-to-service e2e evidence | Compose files and per-service settings | CI is not a production deployment or end-to-end verification | PARTIAL | Treat as secondary components pending independent deployment validation |
| Document parsing | TXT/PDF/DOCX/XLSX parsing with scanned-PDF OCR routing | `offline/document_processor.py`, `offline/chunking.py` | `tests/offline/test_document_processor.py`, `tests/offline/test_chunking.py` | `knowledge_base.chunk_size`, `max_document_bytes`, `max_binary_document_bytes`, `xlsx_rows_per_block` | Deterministic fixtures; no real-corpus parsing benchmark | VERIFIED | Claim parsing implementation only |
| OCR | OCR adapter and image pipeline exist; real PaddleOCR runtime is external | `offline/image_processor.py` | `tests/offline/test_image_processing.py` (deterministic provider) | `knowledge_base.ocr.*`; optional `offline/requirements-ocr.txt` | PaddleOCR not installed in default CI; real OCR smoke not run | PARTIAL | Real PaddleOCR smoke pending external runtime |
| BGE | BGE text embedding adapter shares the online pooling contract | `offline/embeddings.py`, `offline/text_ingestion.py` | `tests/offline/*`, `tests/test_offline_text_ingestion.py`; `scripts/smoke_bge_ingestion.py` | `embedding.text.model_path`/`model_revision`/`dimension` | CI uses deterministic embedder; real model smoke `EXTERNAL_MODEL_ASSET_REQUIRED` | PARTIAL | Real configured BGE smoke pending external asset |
| CLIP | CLIP image embedding adapter (512d) shares the online preprocessing contract | `offline/embeddings.py` | `tests/offline/test_image_processing.py` | `embedding.image_clip.*` | Deterministic embedder integration; real CLIP smoke not run | PARTIAL | Real configured CLIP smoke pending external asset |
| Qdrant text | Text writer with epoch/seal/staging lifecycle | `offline/text_ingestion.py` | `tests/test_offline_text_ingestion.py`, `tests/offline/test_offline_end_to_end.py` | `embedding.text.collection`, `qdrant.*` | In-memory `QdrantClient` integration only; no real Qdrant service runtime test | VERIFIED | Keep lifecycle contract covered |
| Qdrant image | Epoch-aware image writer; legacy points retrievable in `default` | `offline/qdrant_writer.py` | `tests/offline/test_image_processing.py` | `embedding.image_clip.collection` | In-memory `QdrantClient` integration only; no real Qdrant service runtime test | VERIFIED | Keep epoch-isolation regression coverage |
| Elasticsearch | `cosmetics_docs` writer with explicit mapping and `search_after` epoch pagination | `offline/elasticsearch_writer.py` | `tests/offline/test_elasticsearch_writer.py`, `tests/offline/test_offline_end_to_end.py` | `elasticsearch.*`; `requirements.txt` pins client `<9` | Fake client unit tests (the fake rejects `_id` sorting) + real Elasticsearch integration; epoch reads sort on `chunk_id` | VERIFIED | Keep mapping and pagination-sort regression coverage |
| Elasticsearch security | Compose enables `xpack.security.enabled=true`; online/offline clients prefer env credentials over `config.json` | `docker-compose.yml`, `retrieval/bm25_retriever.py`, `common/config.py` | `tests/test_docker_compose.py`, `scripts/validation/validate_es_auth.py` | `ELASTICSEARCH_USERNAME`/`ELASTICSEARCH_PASSWORD` | Compose config parsing + local authenticated ES 8.11 (anon/wrong 401; writer mapping + `search_after`; BM25 online search). Cluster/TLS/multi-node not validated | VERIFIED | Production ES topology stays deployment-specific |
| Source state | SQLite incremental state; content-hash and permission-mask authoritative, committed only after a successful snapshot | `offline/state_store.py`, `offline/snapshot_builder.py` | `tests/offline/test_state_store.py`, `tests/offline/test_reconciliation_fixes.py` | `knowledge_base.state_db_path` | Temp DB tests; runtime DB untracked | VERIFIED | Do not claim an mtime/size short-circuit |
| Incremental snapshot | Incremental build produces a complete target-epoch snapshot | `offline/snapshot_builder.py` | `tests/offline/test_snapshot_builder.py`, `tests/offline/test_offline_end_to_end.py` | `knowledge_base.*` | Deterministic in-memory integration | VERIFIED | Keep carry-forward coverage |
| Carry-forward | Unchanged documents are copied across epochs; incompatible versions require full rebuild | `offline/carry_forward.py` | `tests/offline/test_snapshot_builder.py` | `embedding.*.model_revision` | Deterministic integration | VERIFIED | Keep version-compatibility guard |
| Full rebuild | Reprocesses all sources into a new epoch without auto-activation | `offline/rebuild.py`, `offline/snapshot_builder.py` | `tests/offline/test_snapshot_builder.py` | `knowledge_base.data_dir` | Deterministic integration | VERIFIED | Keep manual activation boundary |
| Validator | Pre-seal snapshot validation across stores | `offline/validator.py` | `tests/offline/test_snapshot_builder.py`, `tests/offline/test_offline_end_to_end.py` | `offline.scheduler.auto_seal` | Deterministic integration | VERIFIED | Validation failure must block sealing |
| Epoch seal | Validate-then-seal CLI; sealed epochs immutable; activation is manual | `run_offline.py`, `offline/snapshot_builder.py`, `offline/text_ingestion.py` | `tests/offline/test_cli.py`, `tests/test_offline_text_ingestion.py` | `knowledge_version_epoch` | Deterministic integration | VERIFIED | Keep `--skip-validation` as explicit danger only |
| CLI | Lifecycle subcommands with legacy TXT alias | `run_offline.py` | `tests/offline/test_cli.py`, `tests/test_offline_text_ingestion.py` | n/a | Deterministic tests | VERIFIED | Document only implemented commands |
| Scheduler | Framework-independent cycles; config-driven cadence | `offline/scheduler.py` | `tests/offline/test_scheduler.py` | `offline.scheduler.*` | Deterministic tests | VERIFIED | Scheduler never activates an epoch |
| Airflow integration | DAGs registered only when Airflow and modules exist | `dags/knowledge_base_dags.py` | `tests/test_knowledge_base_dags.py` | `offline.scheduler.*` cadence | Airflow not installed; no real DAG execution | PARTIAL | Real Airflow execution pending |
| Feedback | Unified review-gated feedback pipeline | `offline/feedback_loop.py` | `tests/offline/test_feedback_loop.py` | `offline.feedback.*` | Deterministic tests | VERIFIED | Only `accepted` records enter training exports |
| QLoRA | Training utility exists; trained adapter is not included | `offline/finetune_qlora.py`, sample data and dedicated requirements | `tests/test_finetune_pipeline.py` (mocked); no reproducible training run | Separate optional dependency set | No training artifact or runtime validation | PARTIAL | Describe the utility only |
| AdapterManager | PEFT lifecycle code integrates with `LLMClient` | `models/adapter_manager.py`, `models/llm_client.py` | `tests/test_adapter_manager.py`, `tests/test_llm_client.py` | `config.json` adapter path and `peft_config.auto_discover` | CI covers mocked/unit paths, not external weights | PARTIAL | Keep asset boundary explicit |
| RRF | Weighted reciprocal rank fusion implemented in retrieval | `retrieval/parallel_recall.py`, `retrieval-service/rerank/rrf_fusion.py` | `tests/test_rrf_fusion.py`, `tests/test_parallel_recall.py` | Fusion weights in `config.json` | CI unit coverage; no relevance benchmark | VERIFIED | Claim implementation only |
| BiEncoder | BiEncoder reranking implemented in the online pipeline | `retrieval/bi_encoder.py`, `core/pipeline.py` | `tests/test_bi_encoder_rerank.py` | BGE model paths in `config.json`; weights external | CI uses mocks; no production model run | PARTIAL | Model assets and evaluation are separate |
| RAGAS | Harness/reporter/validator and a 300+ entry golden set exist; single-evaluation flow, real pipeline answer/contexts, failure accounting and report provenance implemented; library `evaluate()` keeps a non-quality unavailable fallback, while `--require-ragas` fails fast with a non-zero exit and no quality report; no real quality score produced | `tests/evaluation/ragas_eval.py`, `ragas_report.py`, `validate_golden_set.py` | `tests/evaluation/test_ragas_eval.py`, `test_ragas_report.py` | Optional package omitted from default requirements; isolated `requirements-ragas.txt` (pinned, carries recorded advisories); `config.json` → `ragas` | CI deterministic evaluation guard passes without real RAGAS; real evaluator smoke and pipeline evaluation are BLOCKED (no `OPENAI_API_KEY`; latest `ragas` import-broken, importable `ragas 0.2.15` has advisories) | PARTIAL | `--require-ragas` missing dependency/credential must fail fast (exit 2/3) with no report; real eval pending an approved provider |
| OpenTelemetry tracing | Tracing hook is on the online pipeline path; default install routes through the OTel SDK provider with no exporter configured (spans neither exported nor retained), falling back to an in-memory span buffer only when the OTel SDK is absent or initialization fails | `core/pipeline.py`, `monitoring/otel_tracer.py` | `tests/test_monitoring_otel.py` | `config.json` → `monitoring.jaeger.enabled=false`; `opentelemetry-api`/`-sdk` installed, no exporter package | CI exercises the tracer without an exporter; no Jaeger/OTLP backend verified | PARTIAL | Say "tracing hook wired, no exporter configured", not "OTel/Jaeger export closed loop" |
| RBAC | Auth and bitmask authorization code exists under a uint32 mask contract; missing/malformed metadata fails closed, and text/image retrieval plus `/api/media/{doc_id}` are epoch/RBAC aware | `auth/`, `common/auth.py`, `retrieval/parallel_recall.py`, `api/routes.py` | `tests/test_bitmask_rbac.py`, `tests/test_retrieval_authorization_contract.py`, `tests/test_media_route.py`, `tests/offline/test_image_processing.py` | `config.json` RBAC section and environment settings | CI unit/API tests; no production policy audit | PARTIAL | Describe implemented paths without deployment claims |
| Cache | Cache implementations and metrics exist; full invalidation model not certified | `cache/`, cache service, metrics code | `tests/test_cache.py`, `tests/test_metrics_endpoint.py` | Cache settings in `config.json` | CI unit tests; no workload benchmark | PARTIAL | Keep full design behavior unverified |
| Frontend contract | React client reads auth/RBAC metadata and routes single-query, chat, session and stats panels to the monolith API | `frontend/src/App.jsx`, `api/routes_auth.py` metadata endpoint | `tests/test_auth_metadata.py`; frontend build is separate | `config.json` UI metadata | CI does not build the frontend here | PARTIAL | Frontend integration remains build/validation-scoped |
| Performance | PRD P95/P99/QPS values are design targets | Benchmark utilities exist under `tests/load/`; no reproducible artifact checked in | Load-test code is not a benchmark result | Target values in `config.json` / PRD | CI does not establish production latency/throughput/accuracy | PARTIAL | Label numbers as design targets |
| Retrieval benchmark | Deterministic retrieval-quality harness exists (Recall@1/3/5/10, HitRate@1/3/5/10, MRR@10, binary NDCG@10) with provenance and artifact contract | `benchmarks/`, `artifacts/benchmarks/` | `tests/benchmark/` (48 deterministic tests, fixture retriever only) | Bucket support: overall / business_type / difficulty; `visual_required` and complexity labels absent (see `docs/benchmark-data-quality.md`) | Framework = REPO_VERIFIED; no real benchmark artifact exists, so the result is PENDING. Configurations currently report BLOCKED (no live Elasticsearch/Qdrant, no BGE weights, no corpus containing the ground truth) | PARTIAL | Never publish numbers from a blocked or fixture-retriever run |
| Runtime version | Runtime version agrees with newest dated changelog release | `common/config.py` reads `config.json` | Consistency script checks the invariant | `config.json` `system.version` = `2.3.0` | `scripts/check_repo_consistency.py` runs in CI | VERIFIED | Keep the check enabled; Unreleased does not bump version |
| CI | Python checks include tests, compile, collection and repository consistency | `.github/workflows/ci.yml` | Workflow runs pytest and collection checks | Workflow configuration | Verify the current `HEAD` GitHub Actions run for CI | VERIFIED | Require green checks on the reconciliation PR |
| Ruff | Lint and formatting are configured as CI checks | `.github/workflows/lint.yml` | Ruff check and format check | Workflow configuration | Verify the current `HEAD` GitHub Actions run for Ruff | VERIFIED | Require green checks on the reconciliation PR |
| Security | Dependency audit and secret scanning configured in CI; RAGAS excluded from default install | `.github/workflows/security.yml`, `requirements.txt` | CI runs pip-audit and secret scan | RAGAS is opt-in | A green security workflow does not imply optional dependency safety | VERIFIED | Do not infer optional dependency safety |
| Runtime artifacts | Tracked PID/stopped markers were tool state, not product files | Exact markers removed | Consistency check rejects tracked PID/state markers | `.gitignore` excludes local state paths | Verify using `git ls-files` and CI invariant | VERIFIED | Keep runtime state untracked |
| Documentation governance | Current guides and historical plans have separate roles | `docs/README.md`, current guides and `docs/superpowers/` | Consistency checks link/path and stale-claim invariants | No runtime config claim | CI validates stable docs invariants | VERIFIED | Keep plans/specs out of current implementation evidence |

## External validation pending

These are implemented in code but not validated against real external assets/runtimes here:

- Real configured BGE model smoke — `EXTERNAL_MODEL_ASSET_REQUIRED`.
- Real configured CLIP model smoke — `EXTERNAL_MODEL_ASSET_REQUIRED`.
- Real PaddleOCR smoke — external runtime not installed.
- Real Airflow DAG execution — Airflow not installed.
- Production evaluation / benchmark — no reproducible artifact checked in.
- Real RAGAS evaluation with a permitted dependency and evaluator API key — not run: no
  `OPENAI_API_KEY`, `ragas 0.4.x` is import-broken, and the importable `ragas 0.2.15` pulls
  `langchain 0.3.x` with advisories (see [real RAGAS validation](validation/real-ragas-evaluation.md)).
- Single shared 4B vLLM GPU deployment (and 14B routing) — model weights absent, `vllm` not installed.
- Production Redis topology (cluster/Sentinel) and HTTP multi-worker behind a load balancer.
- Non-nginx reverse proxies (Cloudflare / ALB / Traefik) — require deployment-specific configuration.

Validated locally on 2026-10-02 as `LOCAL_REAL_VALIDATION` (see
[v2.5 runtime/security validation](validation/v2.5-runtime-security-validation.md)):
real Redis multi-process session persistence and login rate limiting, real nginx proxy-trust resolution,
authenticated Elasticsearch online + offline paths, and an authenticated Prometheus scrape. Local
`LOCAL_REAL_VALIDATION` is not a production benchmark, does not imply production HA/SLO, and must not be
rewritten as "never validated".

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
