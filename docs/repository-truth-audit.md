# Repository Truth Audit

Original audit base: `7b03267ccd751178e5e1d69ec6a6ec97281b57cb` (`origin/main`). Merged since then: PR #3, PR #4, PR #5, PR #6, PR #7.
Reconciled candidate: `HEAD` (resolved by `scripts/check_repo_consistency.py` at verification time; a commit cannot embed its own SHA without making the value stale).
Post-reconciliation verification date: 2026-10-02.
Canonical runtime version: `config.json` → `system.version` (`2.3.0`); release history is recorded in `CHANGELOG.md`. The repository has no GitHub Release at audit time.

Status values: `VERIFIED`, `PARTIAL`, `PLANNED`, `BROKEN`, `STALE`, `HISTORICAL`.

A `VERIFIED` status means code exists and is covered by collected tests (and, where noted, by a real local service integration). It never means real-model quality, production latency/QPS, or large-corpus throughput has been established.

| Area | Claim | Code evidence | Test evidence | Config evidence | Runtime / CI evidence | Status | Current action |
|---|---|---|---|---|---|---|---|
| Application | FastAPI monolith is the canonical application path | `app.py`, `api/routes.py`, `api/routes_auth.py` | `tests/test_api.py`, `tests/test_pipeline.py` | `common/config.py`, `.env.example` | CI exercises Python tests; no production deployment is claimed | VERIFIED | Keep README quick start on `app.py` |
| Authentication | RS256 browser tokens verify independently of the legacy HS256 secret; legacy HS256 remains an optional fallback | `auth/jwt_auth.py`, `common/auth.py`, `api/routes_auth.py` | `tests/test_jwt_auth.py`, `tests/test_auth_routes.py`, `tests/test_auth_identity_resolution.py` | `JWT_PRIVATE_KEY_PATH`/`JWT_PUBLIC_KEY_PATH`/`JWT_ALGORITHM`; `JWT_SECRET` optional | CI unit/API tests; no production identity-policy audit | VERIFIED | Keep RS256 primary and the HS256 compatibility path explicitly bounded |
| Microservices | Service components exist; integrated production deployment is not established | `api-gateway/`, `retrieval-service/`, `generation-service/`, `monitoring-service/` | Component tests exist; no complete frontend-to-service e2e evidence | Compose files and per-service settings | CI is not a production deployment or end-to-end verification | PARTIAL | Treat as secondary components pending independent deployment validation |
| Document parsing | TXT/PDF/DOCX/XLSX parsing with scanned-PDF OCR routing | `offline/document_processor.py`, `offline/chunking.py` | `tests/offline/test_document_processor.py`, `tests/offline/test_chunking.py` | `knowledge_base.chunk_size`, `max_document_bytes`, `max_binary_document_bytes`, `xlsx_rows_per_block` | Deterministic fixtures; no real-corpus parsing benchmark | VERIFIED | Claim parsing implementation only |
| OCR | OCR adapter and image pipeline exist; real PaddleOCR runtime is external | `offline/image_processor.py` | `tests/offline/test_image_processing.py` (deterministic provider) | `knowledge_base.ocr.*`; optional `offline/requirements-ocr.txt` | PaddleOCR not installed in default CI; real OCR smoke not run | PARTIAL | Real PaddleOCR smoke pending external runtime |
| BGE | BGE text embedding adapter shares the online pooling contract | `offline/embeddings.py`, `offline/text_ingestion.py` | `tests/offline/*`, `tests/test_offline_text_ingestion.py`; `scripts/smoke_bge_ingestion.py` | `embedding.text.model_path`/`model_revision`/`dimension` | CI uses deterministic embedder; real model smoke `EXTERNAL_MODEL_ASSET_REQUIRED` | PARTIAL | Real configured BGE smoke pending external asset |
| CLIP | CLIP image embedding adapter (512d) shares the online preprocessing contract | `offline/embeddings.py` | `tests/offline/test_image_processing.py` | `embedding.image_clip.*` | Deterministic embedder integration; real CLIP smoke not run | PARTIAL | Real configured CLIP smoke pending external asset |
| Qdrant text | Text writer with epoch/seal/staging lifecycle | `offline/text_ingestion.py` | `tests/test_offline_text_ingestion.py`, `tests/offline/test_offline_end_to_end.py` | `embedding.text.collection`, `qdrant.*` | In-memory Qdrant + real Qdrant integration | VERIFIED | Keep lifecycle contract covered |
| Qdrant image | Epoch-aware image writer; legacy points retrievable in `default` | `offline/qdrant_writer.py` | `tests/offline/test_image_processing.py` | `embedding.image_clip.collection` | In-memory Qdrant + real Qdrant integration | VERIFIED | Keep epoch-isolation regression coverage |
| Elasticsearch | `cosmetics_docs` writer with explicit mapping and `search_after` epoch pagination | `offline/elasticsearch_writer.py` | `tests/offline/test_elasticsearch_writer.py`, `tests/offline/test_offline_end_to_end.py` | `elasticsearch.*`; `requirements.txt` pins client `<9` | Fake client unit tests (the fake rejects `_id` sorting) + real Elasticsearch integration; epoch reads sort on the unique keyword `chunk_id`, not Elasticsearch 8 `_id`, which disables sorting/fielddata by default | VERIFIED | Keep mapping and pagination-sort regression coverage |
| Source state | SQLite incremental state; content-hash and permission-mask authoritative, committed only after a successful snapshot | `offline/state_store.py`, `offline/snapshot_builder.py` | `tests/offline/test_state_store.py`, `tests/offline/test_reconciliation_fixes.py` | `knowledge_base.state_db_path` | Temp DB tests; runtime DB untracked | VERIFIED | Do not claim an mtime/size short-circuit |
| Incremental snapshot | Incremental build produces a complete target-epoch snapshot | `offline/snapshot_builder.py` | `tests/offline/test_snapshot_builder.py`, `tests/offline/test_offline_end_to_end.py` | `knowledge_base.*` | Deterministic in-memory integration | VERIFIED | Keep carry-forward coverage |
| Carry-forward | Unchanged documents are copied across epochs; incompatible versions require full rebuild | `offline/carry_forward.py` | `tests/offline/test_snapshot_builder.py` | `embedding.*.model_revision` | Deterministic integration | VERIFIED | Keep version-compatibility guard |
| Full rebuild | Reprocesses all sources into a new epoch without auto-activation | `offline/rebuild.py`, `offline/snapshot_builder.py` | `tests/offline/test_snapshot_builder.py` | `knowledge_base.data_dir` | Deterministic integration | VERIFIED | Keep manual activation boundary |
| Validator | Pre-seal snapshot validation across stores | `offline/validator.py` | `tests/offline/test_snapshot_builder.py`, `tests/offline/test_offline_end_to_end.py` | `offline.scheduler.auto_seal` | Deterministic integration | VERIFIED | Validation failure must block sealing |
| Epoch seal | Validate-then-seal CLI; sealed epochs immutable | `run_offline.py`, `offline/snapshot_builder.py`, `offline/text_ingestion.py` | `tests/offline/test_cli.py`, `tests/test_offline_text_ingestion.py` | `knowledge_version_epoch` | Deterministic integration | VERIFIED | Keep `--skip-validation` as explicit danger only |
| CLI | Lifecycle subcommands with legacy TXT alias | `run_offline.py` | `tests/offline/test_cli.py`, `tests/test_offline_text_ingestion.py` | n/a | Deterministic tests | VERIFIED | Document only implemented commands |
| Scheduler | Framework-independent cycles; config-driven cadence | `offline/scheduler.py` | `tests/offline/test_scheduler.py` | `offline.scheduler.*` | Deterministic tests | VERIFIED | Scheduler never activates an epoch |
| Airflow integration | DAGs registered only when Airflow and modules exist | `dags/knowledge_base_dags.py` | `tests/test_knowledge_base_dags.py` | `offline.scheduler.*` cadence | Airflow not installed; no real DAG execution | PARTIAL | Real Airflow execution pending |
| Feedback | Unified review-gated feedback pipeline | `offline/feedback_loop.py` | `tests/offline/test_feedback_loop.py` | `offline.feedback.*` | Deterministic tests | VERIFIED | Only `accepted` records enter training exports |
| QLoRA | Training utility exists; trained adapter is not included | `offline/finetune_qlora.py`, sample data and dedicated requirements | No checked-in evidence of a reproducible training run | Separate optional dependency set | No training artifact or runtime validation | PARTIAL | Describe the utility only |
| AdapterManager | PEFT lifecycle code integrates with `LLMClient` | `models/adapter_manager.py`, `models/llm_client.py` | `tests/test_adapter_manager.py`, `tests/test_llm_client.py` | `config.json` adapter path and `peft_config.auto_discover` | CI covers mocked/unit paths, not external weights | PARTIAL | Keep asset boundary explicit |
| RRF | Weighted reciprocal rank fusion implemented in retrieval | `retrieval/parallel_recall.py`, `retrieval-service/rerank/rrf_fusion.py` | `tests/test_rrf_fusion.py`, `tests/test_parallel_recall.py` | Fusion weights in `config.json` | CI unit coverage; no relevance benchmark | VERIFIED | Claim implementation only |
| BiEncoder | BiEncoder reranking implemented in the online pipeline | `retrieval/bi_encoder.py`, `core/pipeline.py` | `tests/test_bi_encoder_rerank.py` | BGE model paths in `config.json`; weights external | CI uses mocks; no production model run | PARTIAL | Model assets and evaluation are separate |
| RAGAS | Evaluation harness and datasets exist; quality not certified | `tests/evaluation/ragas_eval.py` | `tests/evaluation/test_ragas_eval.py` | Optional package omitted from default requirements | CI validates harness fallback; no valid score | PARTIAL | Do not publish fallback output as quality |
| RBAC | Auth and bitmask authorization code exists under a uint32 mask contract; missing/malformed metadata fails closed, and text/image retrieval plus `/api/media/{doc_id}` are epoch/RBAC aware | `auth/`, `common/auth.py`, `retrieval/parallel_recall.py`, `api/routes.py` | `tests/test_bitmask_rbac.py`, `tests/test_retrieval_authorization_contract.py`, `tests/test_media_route.py`, `tests/offline/test_image_processing.py` | `config.json` RBAC section and environment settings | CI unit/API tests; no production policy audit | PARTIAL | Describe implemented paths without deployment claims |
| Cache | Cache implementations and metrics exist; full invalidation model not certified | `cache/`, cache service, metrics code | `tests/test_cache.py`, `tests/test_metrics_endpoint.py` | Cache settings in `config.json` | CI unit tests; no workload benchmark | PARTIAL | Keep full design behavior unverified |
| Performance | PRD P95/P99/QPS values are design targets | Benchmark utilities exist under `tests/load/`; no reproducible artifact checked in | Load-test code is not a benchmark result | Target values in `config.json` / PRD | CI does not establish production latency/throughput/accuracy | PARTIAL | Label numbers as design targets |
| Runtime version | Runtime version agrees with newest dated changelog release | `common/config.py` reads `config.json` | Consistency script checks the invariant | `config.json` `system.version` = `2.3.0` | `scripts/check_repo_consistency.py` runs in CI | VERIFIED | Keep the check enabled; Unreleased does not bump version |
| CI | Python checks include tests, compile, collection and repository consistency | `.github/workflows/ci.yml` | Workflow runs pytest and collection checks | Workflow configuration | GitHub Actions on the latest merged `main` (PR #7) are green; the reconciliation candidate must pass the same checks before merge | VERIFIED | Require green checks on final HEAD |
| Ruff | Lint and formatting are configured as CI checks | `.github/workflows/lint.yml` | Ruff check and format check | Workflow configuration | GitHub Actions on the latest merged `main` (PR #7) are green; the reconciliation candidate must pass the same checks before merge | VERIFIED | Require green checks on final HEAD |
| Security | Dependency audit and secret scanning configured in CI | `.github/workflows/security.yml` | CI runs pip-audit and secret scan | RAGAS excluded from default install pending upstream fix | GitHub Actions on the latest merged `main` (PR #7) are green; a green security workflow does not imply optional dependency safety | VERIFIED | Do not infer optional dependency safety |
| Runtime artifacts | Tracked PID/stopped markers were tool state, not product files | Exact markers removed | Consistency check rejects tracked PID/state markers | `.gitignore` excludes local state paths | Verify using `git ls-files` and CI invariant | VERIFIED | Keep runtime state untracked |
| Documentation governance | Current guides and historical plans have separate roles | `docs/README.md`, current guides and `docs/superpowers/` | Consistency checks link/path and stale-claim invariants | No runtime config claim | CI validates stable docs invariants | VERIFIED | Keep plans/specs out of current implementation evidence |

## External validation pending

These are implemented in code but not validated against real external assets/runtimes here:

- Real configured BGE model smoke — `EXTERNAL_MODEL_ASSET_REQUIRED`.
- Real configured CLIP model smoke — `EXTERNAL_MODEL_ASSET_REQUIRED`.
- Real PaddleOCR smoke — external runtime not installed.
- Real Airflow DAG execution — Airflow not installed.
- Production evaluation / benchmark — no reproducible artifact checked in.

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
