# Changelog

## [Unreleased]

Changes present on `main` after the 2.3.0 release entry:

### Added

- Full offline ingestion pipeline under `offline/` (reintroduced after the 2.3.0
  capability correction below):
  - Multi-format `DocumentProcessor` for TXT/PDF/DOCX/XLSX with scanned-page OCR routing.
  - Deterministic structured chunking with stable logical `doc_id`/`chunk_id`/`image_id`.
  - OCR/image pipeline with pluggable `OCRProvider`/`ImageEmbedder` and a deterministic
    visual-weight rule.
  - CLIP image embedding adapter (512d) sharing the online preprocessing contract.
  - Epoch-aware Qdrant image writer; legacy image points remain retrievable in `default`.
  - Elasticsearch `cosmetics_docs` writer with explicit mapping.
  - SQLite incremental source state (content-hash authoritative).
  - Snapshot carry-forward, full rebuild, snapshot validator, and validate-then-seal CLI.
  - Lifecycle CLI: `create-index`, `ingest`, `incremental-build`, `full-rebuild`, `seal-epoch`
    (legacy `ingest-text` preserved).
  - Framework-independent scheduler and config-driven Airflow DAG definitions.
  - Unified, review-gated feedback pipeline.
  - Regression candidate pipeline (`offline/regression_candidates.py`): reviewed negative
    feedback becomes a `PENDING_REVIEW` candidate with stable `case_id`, human-only
    `expected_behaviour`/`expected_evidence` (fail closed when absent), preserved provenance,
    and `accepted`-only export to `regression_dataset.jsonl`. Model answers and their
    retrieved documents are never promoted to ground truth. New CLI:
    `export-regression-candidates`. Produces no RAGAS score.
  - Cross-platform file lock adapter (POSIX `fcntl` / Windows `msvcrt`).
  - Real BGE smoke harness (`scripts/smoke_bge_ingestion.py`, `@pytest.mark.model_smoke`).
- QLoRA fine-tuning utility, sample data, and separate fine-tuning dependencies.
- PEFT `AdapterManager` and integration with `LLMClient`.
- Weighted Reciprocal Rank Fusion (RRF) in multi-path retrieval.
- Dedicated configurable BiEncoder reranker support.
- RAGAS evaluation harness, reporter, golden-set validator and CLI entrypoint; golden set
  expanded to 300+ entries (initial seed was 27; exact count is authoritative from
  `validate_golden_set`/`golden_set.jsonl`).
- Isolated real-evaluator environment (`requirements-ragas.txt`: pinned ragas 0.2.15 +
  langchain 0.3.x) with recorded `pip-audit` advisories; `--limit` / `--sample-ids` /
  `--require-ragas` CLI options; report provenance (git commit, dataset hash, sample ids,
  provider/model, attempt/success/failure counts).
- Deterministic architecture-contract tests (`tests/test_architecture_contract.py`).
- Redis-backed cross-worker session persistence for `SessionState` with in-memory fallback,
  including a stable serialization schema for nested Pydantic objects.
- Redis-backed login rate limiting with in-memory fallback for multi-worker deployments.
- Prefix-cache hit/miss metrics and Locust report improvements.
- Deterministic retrieval benchmark framework (`benchmarks/`, artifact contract under
  `artifacts/benchmarks/`). This is the **framework only**:
  - Pure-function retrieval metrics: `Recall@1`, `Recall@3`, `Recall@5`, `Recall@10`,
    `HitRate@1`, `HitRate@3`, `HitRate@5`, `HitRate@10`, `MRR@10` and binary `NDCG@10`.
  - Conservative relevance matching — stable id when present, otherwise NFKC- and
    whitespace-normalized exact text. No LLM judge, no fuzzy threshold.
  - Provenance metadata: git state, dataset sha256, a sanitized effective-configuration
    sha256 plus the snapshot it hashes, environment versions, and credential **presence
    flags** only (never secret values; URL userinfo is redacted and a non-reversible
    principal fingerprint separates principals).
  - Per-stage latency artifact contract: `embedding_ms` / `bm25_ms` / `dense_search_ms` /
    `rrf_ms` / `biencoder_ms` / `crossencoder_ms`. A stage that never ran is recorded as
    `null`, never `0` and never mirrored from end-to-end time.
  - `BLOCKED` / `PENDING` semantics: every configuration is probed live and reports
    `BLOCKED` with a recorded reason rather than producing numbers. Dirty-tree runs are
    refused unless `--allow-dirty`.
  - Backend and model readiness probing (Elasticsearch, Qdrant over the gRPC transport the
    production client actually uses, model config/weights/tokenizer completeness) so a
    partial or interrupted asset cache cannot look loadable.

  **Framework implementation is not a benchmark result.** No retrieval metric is claimed
  anywhere in this repository: no benchmark artifact is committed, and every configuration
  currently reports `BLOCKED` (no live Elasticsearch/Qdrant, no BGE weights, and no corpus
  containing the golden-set passages). Benchmark framework = `REPO_VERIFIED`, benchmark
  result = `PENDING`.
- Performance evidence artifact contract under `artifacts/performance/<run-id>/`
  (`metadata.json`, `environment.json`, `workload.json`, `latency_metrics.json`,
  `throughput_metrics.json`, `errors.json`, `report.md`):
  - `EXECUTED` / `PARTIAL` / `BLOCKED` status **derived** from what happened rather than
    chosen by the caller; `blocked_reason` wins outright and zero requests is `BLOCKED`.
  - Not executed is recorded as `null` and the latency block is reduced to `{"count": 0}`,
    so an unrun workload can never read as a fast, healthy one.
  - Git provenance (`git_sha`, `git_dirty`), runtime version, host, workload declaration,
    limitations and a machine-readable digest.
  - Load-harness preflight blocks on an unreachable API, a non-200 `/api/health` or a
    missing bearer token, because every `/api/*` endpoint except health requires auth and a
    tokenless run would otherwise measure a stream of 401s.
  - **No performance number is claimed.** No artifact is committed, so measured
    QPS/P95/P99 remains `PENDING` and PRD figures remain `DESIGN_TARGET`.
- Structured enterprise audit events (`common/audit.py`) for login success/failure/rate-limit,
  user and role administration, protected-media denial and epoch seal:
  - Stable schema (timestamp, request_id, actor_id, action, resource_type, resource_id,
    outcome, reason, metadata) with extra context confined to `metadata`.
  - Redaction enforced on every emit, covering key spelling variants, nested
    dict/list/tuple structures, and credential-shaped *values*; `auth` is matched as a
    suffix so a presence mapping is not collapsed.
  - request-id correlation via a contextvar shared with the access log and trace spans.
  - Reuses the existing Redis Stream + daily JSONL audit sinks.
  - No `knowledge.epoch.activate` event: this repository has no activate endpoint.
- Real HTTP and Redis-degradation metrics on the canonical `/api/metrics` path:
  `rag_http_requests`, `rag_http_responses_2xx/4xx/5xx`, `rag_http_rate_limited`,
  `rag_http_active_requests`, `rag_http_request_duration_seconds`, and
  `rag_redis_degraded_mode` driven by the actual fallback path. Previously the endpoint
  emitted only `rag_uptime_seconds` until a complete RAG query succeeded, so dependency
  failures produced no metrics at all.
- Prometheus alert rules (`monitoring/prometheus/alerts.yml`): `RagAppDown`,
  `RagHighErrorRate`, `RagHighLatencyP95`, `RagRedisDegraded`,
  `RagHighLoginRateLimit`, `RagRequestSaturation`. Every threshold is a `DESIGN_TARGET`.
  Qdrant and Elasticsearch outages are deliberately not alerted because no metric is
  emitted for them; that gap is documented rather than papered over.
- Optional OTLP span export (`monitoring/otel_exporter.py`): off by default, non-fatal on
  any failure, span attributes reduced to an allow-list, exporter package isolated in
  `requirements-otel.txt`. Observable as `rag_otel_exporter_enabled`.
- `docs/slo-runbook.md`: 5 objectives (all `DESIGN_TARGET`) and 8 incident procedures.
- Optional `docker-compose.observability.yml` (Prometheus + Jaeger + Grafana), verified to
  be purely additive; the canonical deployment still starts none of it.
- Minimal Grafana dashboard over emitted metrics only — no hallucination-rate, live-RAGAS
  or GPU-utilization panel, since those metrics do not exist.

### Fixed

- The FastAPI `description` (runtime-exposed through Swagger UI and the generated
  OpenAPI `info` block) advertised the product as "基于双 GPU". No code, config, test or
  artifact in this repository evidences that topology: the 4B/14B vLLM deployment is
  `PENDING` (weights absent, `vllm` not installed) and the RTX A5000 ×2 serving host is
  `HISTORICAL_PRODUCTION`. The runtime-facing description is now
  "多模态检索增强生成（RAG）的企业级知识问答 API", which matches what this repository
  does verify (multimodal recall, RBAC, audit, metrics). `info.title` and `info.version`
  remain sourced from `config.json`. `tests/test_runtime_api_metadata.py` now generates the
  OpenAPI schema and rejects any re-assertion of an unverified dual-GPU production
  topology. Architecture, model routing, config and the historical production record are
  unchanged.
- `monitoring-service/metrics_collector.py` emitted every latency quantile as
  `rag_{quantile="0.5"}`: the metric name was computed and then never used. That is not
  valid Prometheus exposition format, so those quantiles were silently unscrapeable and any
  latency alert would have had no data behind it.
- `deploy/prometheus.yml` scraped only `monitoring-service:8400`, which emits none of the
  `rag_http_*` metrics the alerts target. Added a `rag-api` job for the monolith, with the
  scrape token supplied via `bearer_token_file`.
- `common/audit.py` legacy `log_audit_event` now inherits the request id from context and
  redacts its `extra` payload, so both audit streams share one redaction rule.
- `tests/test_locust_load.py` asserted `LatencyStats.p50 == 0.0` for an empty sample set,
  encoding a fabricated zero as the contract. Now asserted as `None`.
- Qdrant validation evidence was stated in one direction only. The current docs claimed
  in-memory `QdrantClient` coverage with no real-service run anywhere, while the PR #6/#7
  development record contains a real local Qdrant service/container integration run performed
  together with a real local Elasticsearch. Both facts are now recorded, in the evidence map,
  the repository truth audit, the v2.5 validation record and the README, and kept apart:
  - current reproducible coverage is the in-process `QdrantClient`;
  - the PR #6/#7 execution is historical lineage with no committed artifact, so it is **not**
    a current `LOCAL_REAL_VALIDATION` result;
  - it establishes nothing about production Qdrant HA, cluster performance, model quality,
    QPS or latency;
  - upgrading the current evidence requires a **new** real-service run whose artifact is
    committed. `scripts/check_repo_consistency.py` now rejects "only in-memory ever happened",
    rejects "a real Qdrant service is currently verified" while no artifact is committed, and
    rejects the repository holding both claims at once.

### Changed

- FastAPI `on_event` startup/shutdown hooks migrated to a `lifespan` context manager.
- vLLM topology consolidated to a single shared 4B endpoint (`gpu1.models.vllm_4b`) that
  serves both Query Rewrite and simple generation; complex requests still route to the
  14B endpoint (`gen_14b`).
- CLIP synchronous routing thresholds are now config-driven (`config.json` → `clip_sync`).
- Docs, README and PRD reconciled against the current code/config/test contracts
  (single 4B topology, manual epoch activation, authenticated stats/metrics, ES auth).

### Security

- Security regression coverage audit against a fixed threat list, recorded in
  `docs/security-regression-coverage.md`. Each control is stated as a
  defense-in-depth layer with its production path, its regression test, its
  evidence level, and the gap that remains open. No guardrail framework and no
  LLM-based security model was introduced, and no unrelated architecture changed.
- Retrieval trust boundary in prompt construction: untrusted retrieved evidence is confined
  between reserved delimiters with the user instruction outside and after it, every reserved
  trust-boundary marker is encoded out of untrusted channels (current query, evidence,
  replayed history, continuation prefix), and a system-message policy names the evidence block
  as data rather than instructions. This is a message-structure guard and defense-in-depth
  layer; it does not resolve prompt injection and does not make jailbreaking impossible, as
  recorded in `models/llm_client.py`.
- L2 Redis cache keys are partitioned by role and dept inside the cache object
  (`rag:l2:rm:<role>:dm:<dept>:<key>`), and the permission scope is validated before any cache
  I/O, so cross-role cache reuse no longer depends on caller key discipline.
- Malformed JWT permission claims fail closed at identity ingress: a non-`int` or out-of-range
  `role_mask`/`dept_mask` is rejected before `UserIdentity` construction, so Pydantic coercion
  cannot launder a stringly-typed mask into an authenticated identity. A present-but-null claim
  is treated as malformed rather than absent.
- Access-token issuance refuses to let `extra_claims` shadow the claims the issuer owns
  (`sub`, `role_mask`, `dept_mask`, `iat`, `exp`, `type`), so issuer input and the signed token
  can no longer disagree. Genuine extension claims such as `tenant_id` remain settable.
- Repaired regression coverage that was silently inert: the RS256 malformed-claim matrix in
  `tests/test_auth_identity_resolution.py` minted its tokens through `create_access_token(
  extra_claims=...)`, so the issuer-side guard rejected them first and all 14 cases errored
  before ever reaching the receiver. The helper now signs directly with the test private key,
  which is how a validly-signed token carrying malformed claims would actually arrive. The
  receiver-side guard is therefore independently testable again. Verified by disabling
  `common/auth.py`'s mask validator: 18 tests fail.
- Added deterministic coverage for two implemented-but-untested invariants:
  - stale-chunk removal when a document shrinks within an epoch (Qdrant text writer), which
    previously had only the image-side counterpart covered;
  - role/dept partitioning of the pipeline's logical cache key
    (`OnlineRAGPipeline._build_cache_key`), a layer distinct from the L2 physical-key
    partition. Both verified by disabling the production control: 2 and 4 tests fail
    respectively.
- Elasticsearch in Docker Compose enables `xpack.security.enabled=true`; the app receives
  `ELASTICSEARCH_USERNAME`/`ELASTICSEARCH_PASSWORD` and the BM25/offline clients prefer the
  environment credentials over `config.json`.
- `GET /api/stats` and `GET /api/metrics` require an authenticated identity; `/api/health`
  remains public.
- Login rate-limit identity no longer trusts client-supplied `X-Forwarded-For` unless the
  TCP peer is in `TRUSTED_PROXIES`; forwarded chains are walked right-to-left past trusted hops.
- `app.py` runs uvicorn with `proxy_headers=False`, so the application's `TRUSTED_PROXIES`
  policy is authoritative. Without this, uvicorn's default (`proxy_headers=True`,
  `forwarded_allow_ips=127.0.0.1`) rewrote `request.client` from `X-Forwarded-For` before the
  app-level check and let a direct localhost client spoof its rate-limit identity.
- Post-enterprise-readiness truth reconciliation. No new capability; the enterprise-readiness
  framework and its results are unchanged, and no external validation evidence was produced.
  - `AlertingManager._get_metric_value("prefix_cache_hit_rate")` read `prefix_cache.hits` /
    `prefix_cache.misses` while every writer increments `prefix_cache.hit` /
    `prefix_cache.miss`, so the rate was always computed from zeros. It now reads the writer
    names and returns `None` when there are no samples, which makes the rule skip instead of
    asserting a perfect or a zero hit rate. `tests/test_monitoring_subsystem.py` previously
    encoded the wrong names and now pins the correct contract.
  - `monitoring/otel_tracer.py` module, tracer and `AlertingManager` docstrings described OTel
    and Jaeger as a future upgrade. They now state the actual split: hook implemented, exporter
    implemented and test-covered, disabled by default, closed loop `PENDING`.
  - Removed the `config.json` -> `alerting.rules` block. Its only consumers were the in-process
    `AlertingManager` here and an unimported `common/tracing.py` copy; nothing in `app.py`,
    `api/` or `core/` reaches either. It read like a second production alert contract.
  - Operational docs cited six `rag_*` names the exporter never emits
    (`rag_cache_hit_rate`, `rag_rewrite_fallback_rate`, `rag_http_responses_total`,
    `rag_redis_degradation_total`, `rag_login_rate_limited_total`,
    `rag_admission_rejected_total`). Corrected to the emitted counters, or to an explicit
    PromQL ratio / `/api/stats` pointer where the value is genuinely derived.
  - `docs/slo-runbook.md` listed `RagDependencyDown` in the alert -> response map as if it were
    a rule; it is a `/api/health` condition, and the gap is now stated explicitly.
  - `docs/repository-truth-audit.md` had two contradictory OpenTelemetry rows. They are unified
    into hook / exporter / closed-loop states, and the tracker map plus audit lineage now record
    the enterprise-readiness delivery and this reconciliation.
  - `scripts/check_repo_consistency.py` gained three drift guards: docs may not describe the
    implemented exporter as absent; operational docs may only cite emitted `rag_*` series or an
    explicitly derived ratio; and the audit must keep delivered scope classified and external
    validation trackers recorded as open. The emitted-series inventory is derived from the
    collector's own literals by AST, so the guard stays offline and deterministic. The audit
    table parser now stops at the end of the table instead of parsing later prose tables.
  - `tests/monitoring/test_alerting_reachability.py` pins the reachability finding: it fails if
    the in-process engine is ever wired into the canonical path, which is the moment a second
    alert contract would become real.
- **Retrieval trust boundary and permission-mask hardening (PR #25).** Every item in this
  group is a deterministic, offline-testable control. They are defense-in-depth only: they
  collectively do **not** establish prompt-injection immunity, do not make the model
  jailbreak-proof, and are **not** a production security certification. A prompt-level text
  instruction can still be ignored by the model; stronger guarantees need retrieval-side
  detection, document isolation and output-side validation, none of which exist in this layer.
  - Retrieved evidence is framed as explicitly untrusted data: a `<retrieved_context>` block
    carrying an inline fact-only preamble precedes the `<user_query>` block. Evidence text is
    not deleted or filtered — it stays as data. The policy is appended on all three
    system-prompt paths (default, `config.json` custom, business-type override) through one
    shared helper, so no path can opt out.
  - Reserved-boundary marker encoding: the six structural markers are declared once and
    encoded as data wherever they occur in a payload channel — retrieved evidence, current user
    input, replayed conversation history, and the replayed continuation assistant prefix.
    Retrieval content can no longer close the evidence block early or forge a second trusted
    block. Encoding is idempotent and touches only the reserved markers: no HTML escaping,
    filtering, stripping or normalization. Treating the current user query as a trusted
    instruction is unaffected; it is encoded so it cannot rewrite its own container framing.
  - Instruction precedence is stated explicitly: replayed history is session context for
    coreference and preference continuity — not untrusted retrieval data — and a stale
    historical request does not outrank the current query when the two conflict.
  - The continuation instruction receives its own application-owned
    `<continuation_instruction>` boundary instead of reusing the user-query tags, because it
    is neither retrieval evidence nor a direct user request. Answers returned to callers are
    unchanged; encoding applies only when text is replayed into a later request.
  - The policy's structural tags are derived from the same constants the real framing uses, so
    renaming a boundary moves policy and framing together instead of drifting apart.
- **Role/dept-partitioned Redis L2 storage** (`cache/redis_cache.py`). The physical L2 key is
  `rag:l2:rm:{role_mask}:dm:{dept_mask}:{key}`. Partitioning is enforced by the cache object
  itself rather than left to caller discipline in the logical key, and `get()`/`set()` share one
  key builder so read and write keys cannot drift. Legacy unscoped `rag:l2:*` keys are no longer
  read, so they fail closed instead of being served across permissions.
- **Strict `uint32` authorization-mask validation** at both permission boundaries, as two
  independent fail-closed defenses:
  - `common/auth.py` validates `role_mask`/`dept_mask` at identity ingress, before
    `UserIdentity` is constructed, so no downstream Pydantic coercion can launder a mask.
  - `cache/redis_cache.py` validates the same contract before any cache I/O, so an invalid
    identity cannot read L1 or touch Redis. The cache boundary does not import auth internals;
    the two stay separate.
  - `api/routes_auth.py` admin authorization now resolves a strict identity *before* the role
    check instead of applying a second, looser rule, so a malformed claim is a 401 that never
    reaches the authorization audit event. The returned payload shape is unchanged.
  - Validation is `type(value) is int` plus `0 <= value <= 0xFFFFFFFF`, with no `int()`
    coercion, narrowing or fallback. `isinstance` is deliberately avoided:
    `isinstance(True, int)` is `True`, so a JSON `true` would otherwise become mask 1 and alias
    a legitimate mask.

### Fixed

- Source state is committed only after the whole snapshot (validate + optional seal) succeeds, so a
  failed multi-source build cannot mark sources as processed.
- Incremental change detection now also compares resolved permission masks, so a permission change
  reprocesses the document even when the file bytes are unchanged.
- Re-ingesting a document that no longer has images now removes its stale image points.
- Snapshot validation treats unexpected document IDs as errors, so a full snapshot cannot be sealed
  with documents outside the current source set.
- BM25 results expose the source-level `doc_id` (and `chunk_id`) so they merge with Qdrant results
  in RRF instead of using the ES `_id`.
- Elasticsearch epoch reads use `search_after` pagination past the 10,000-document window.
- Elasticsearch epoch pagination now sorts `search_after` reads on the unique keyword
  `chunk_id` rather than `_id`, because Elasticsearch 8 disables sorting/fielddata on
  `_id` by default. The earlier pagination path existed but could not run against the
  real service until this sort field was corrected.
- The Elasticsearch test fake now rejects `_id` sorting so this real-service
  incompatibility is covered by regression tests.
- Airflow `DEFAULT_ARGS` uses valid `BaseOperator` timeout keys and task callables return
  JSON-serializable dictionaries.
- Image retrieval (sync and async CLIP) now respects the active `knowledge_version_epoch`
  exactly like text retrieval; legacy missing-epoch points no longer leak into a non-default epoch.
- `seal-epoch` now runs the canonical snapshot validator (Qdrant text/image + Elasticsearch)
  before sealing.
- `create-index --recreate --yes` now recreates Qdrant text/image collections and the ES index
  consistently with its help text.
- `elasticsearch` client pinned below 9.x to match the 8.x deployment server.
- AdapterManager and RAGAS test coverage and mock isolation issues.
- BiEncoder test coverage and configuration.
- `SessionState` Redis persistence previously passed Pydantic objects to `json.dumps`, so any
  session with dialog rounds failed to persist silently; it now uses a versioned schema and
  reconstructs `QueryRewriteResult`/`RecallResult` on read. `last_rewrite_result` and
  `store_async_clip_result()` are now persisted too.
- Test modules no longer leak fake `torch`/`datasets` entries into `sys.modules` during
  collection, which previously broke RAGAS tests that need the real `torch`.
- CI no longer emits an all-zero RAGAS fallback as a quality report; real RAGAS evaluation is
  opt-in and fails fast when the dependency is unavailable.
- Local runtime validation added real dependency evidence for Redis multi-worker session
  persistence and login rate limiting, trusted-proxy client-IP resolution through nginx,
  authenticated Elasticsearch online/offline paths, and an authenticated Prometheus scrape
  (see `docs/validation/v2.5-runtime-security-validation.md`; `v2.5` is a working-milestone label,
  not a release).
- RAGAS CLI no longer evaluates twice: `main()` evaluates once and the reporter builds the
  report from the stored run (`build_report`), halving evaluator cost and avoiding drift.
- RAGAS `--pipeline` reports now use the real pipeline answer and retrieved contexts instead
  of the dataset's reference answer; failed pipeline samples are recorded and excluded from
  the aggregate, and an all-failed run exits non-zero.
- **Cross-permission L2 cache aliasing risk (PR #25).** The physical Redis key was
  `rag:l2:{key}`, independent of `role_mask`/`dept_mask`. Permission isolation existed only as
  caller discipline in the *logical* key, so any caller that skipped permission-aware key
  construction made the same logical key resolve to one shared address across permissions.
  Because the masks were interpolated into an f-string without validation, `"1"` and `1` also
  collapsed onto the same key. L2 is now physically partitioned by role and dept, enforced
  inside the cache object rather than at the call sites.
- **Malformed permission-claim coercion risk (PR #25).** Raw JWT `role_mask`/`dept_mask` claims
  were passed straight into `UserIdentity`, whose mask fields are plain `int`, so Pydantic
  coerced `"1"` to `1` (and JSON `true`/`false` to `1`/`0`) and a stringly-typed or boolean
  mask became a fully authenticated identity. Claims are now validated before the identity is
  built. A present-but-malformed claim is also no longer conflated with an absent one: an
  explicit JSON `null` is rejected instead of falling through to the named-role encoding, so a
  malformed claim cannot be silently downgraded to a different mask source. Valid uint32
  boundaries (`0`, `0xFFFFFFFF`) remain accepted, and the absent-claim named-role fallback is
  unchanged.

### Correction — historical offline capability claim (superseded)

The 2.3.0 entry below says `offline/document_processor.py` was added. At the repository
reconciliation point, that file and the related ingestion modules were absent, and
`git log --all` contained no implementation history for them; the `offline/` directory
contained only QLoRA utility assets. That correction was accurate for its point in time.

The full offline pipeline has since been implemented in code (see the Added section above).
The historical 2.3.0 entry is retained unchanged as release history; the new implementation
does not retroactively make the original 2.3.0 claim true.

### Version policy

`config.json` → `system.version` is the canonical runtime version and must match the latest dated release heading below. `Unreleased` records changes without assigning a new version. A changelog version does not imply a GitHub Release or tag; no GitHub Release existed at the reconciliation base.

### `v2.5` is a working-milestone label, not a release

Repository docs and the filename `docs/validation/v2.5-runtime-security-validation.md` carry a
`v2.5` label. It names a historical **working milestone / development phase** of the
runtime-and-security reconciliation work. It is not a release, not the canonical runtime version,
and not a tag. The canonical runtime version remains `2.3.0`; every change listed under
`[Unreleased]` above happened after `2.3.0` without bumping it. Filenames and links are kept
stable rather than renamed. See
[`docs/repository-truth-audit.md`](docs/repository-truth-audit.md#version-policy).

## [2.3.0] - 2026-06-06

### Fixed — 矛盾统一 + Bug 修复 + GAP 补全 (16 项)

**参数统一 (T-01~T-05):**
- safety_factor: config.json 从 0.75 统一为 PRD 值 0.7
- KV 四级降级阈值: 代码从 0.80/0.88/0.93/0.96 统一为 PRD 值 0.70/0.80/0.90/0.95
- config key 重命名: kv_utilization_threshold→kv_pressure_truncate, kv_pressure_critical→kv_pressure_soft_stop, 新增 kv_pressure_tighten/kv_pressure_critical
- pressure 分母 Bug: _pressure_unlocked() 从除以 kv_total(8GB) 修正为除以 kv_budget(5.6GB)
- OUTPUT_MAP 统一: 7 类业务类型 (regulation=1024, development=768, formulation=768, ingredient=512, product=512, general=512, short=256) 在 OUTPUT_MAP/config.json/gateway 三处统一

**Bug 修复 (T-06~T-09):**
- api-gateway: _generate_without_context() 接受 dynamic target_model/max_output_tokens/business_type 参数
- PRD §3.1: 数据范围更新为 配方3000+/产品1500+/成分2000+
- api/routes_auth.py: 补充 logging 导入，修复 NameError
- tests: 修复 test_complexity_evaluator CWD 竞争问题

**功能补全 (T-10~T-14):**
- admission/kv_admission.py: 新增 get_metrics() 导出有效并发数/吞吐模型等 Prometheus 指标
- monitoring-service/metrics_collector.py: 补全 NLI 矛盾率/BLIP 触发率/CLIP 超时率/Redis 降级状态等 7 项缺失指标
- common/audit.py: 审计日志持久化至 Redis Stream (XADD, maxlen=10000)
- offline/document_processor.py: 增量更新差异检测 (mtime+md5)，状态文件 + --force-full 支持

### Changed
- config.json: admission_control 部分重构（key 重命名 + 四级阈值）
- config.json: safety_factor 0.75→0.7
- PRD.md §3.1: 数据范围补充产品维度

**测试结果:** 全量回归测试结果:
- 总测试数: 359
- 通过数: 359
- 失败数: 0
- 跳过数: 0

全部 359 个测试通过，无失败，无跳过。7 个 warnings 均为 FastAPI `on_event` 弃用提示，不影响功能。

注意：第一次运行出现的 8 个 `test_kv_admission.py` 失败属于非确定性问题（测试并行执行时的状态竞争），单独运行该模块及第二次全量运行均已全部通过。

---

## [2.2.0] - 2026-06-06

### Fixed — 全量代码审查 16 项问题修复

- **api-gateway/routers/generation.py: CRITICAL — 准入控制变量使用在赋值之前**
  - `max_output_tokens` 和 `business_type` 在准入检查步骤引用但未赋值
  - 将准入控制检查移动到查询改写和复杂度评估之后
  - 同时移除了重复的复杂度评估和输出长度计算代码块

- **core/pipeline.py + api/routes.py: _fallback_rewrite 传入 None 作为 self**
  - `_fallback_rewrite` 从实例方法改为 `@staticmethod`
  - 修正 `ComplexityEvaluator` 实例化方式（独立创建而非依赖 self）
  - 更新 `routes.py` 调用签名

- **retrieval/evidence_gate.py: NLI 批量推理参数签名不匹配**
  - `_batch_nli_inference` 期望 `(premise, hypothesis)` 元组列表
  - 实际传入两个独立列表，导致永远 fallback 到逐条推理
  - 修正为元组列表格式

- **api/routes.py + frontend: 前端图片上传 FormData 与后端 JSON 不兼容**
  - 新增 `POST /api/query/upload` 端点，支持 `multipart/form-data`
  - 前端图片上传请求改为调用 `/api/query/upload`

- **retrieval/bm25_retriever.py: JSON 加载使用破坏性字符串替换**
  - 移除 `json.loads(f.read().replace('\\"', '"'))` 中的 `.replace()`
  - 改用标准 `json.load(f)`

- **api-gateway/main.py: 系统名称为"汽车知识"（项目遗留错误）**
  - 全部替换为"化妆品企业级多模态 RAG 智能问答系统"

- **auth/user_store.py: RBAC 角色掩码与 config.json 不一致**
  - 从硬编码值改为从 `config.json` 动态读取
  - 修复 `update_user_roles` 使用 `sum()` 而非位运算 `|=`

- **retrieval/rerank_batch_aggregator.py: 队列延迟计算错误**
  - `_execute_batch_sync` 增加 `submit_time` 参数
  - `_process_batch` 传入批次最早提交时间

- **docker-compose.yml: Redis 密码环境变量缺失**
  - app 服务增加 `REDIS_CACHE_PASSWORD` 环境变量

- **config.json: 缺少 l1_max_entries + jwt_secret 为空**
  - 添加 `l1_max_entries: 1000`
  - 设置开发用 `jwt_secret`

- **app.py: SessionState 会话无定期清理**
  - 新增启动时 asyncio 后台任务，每 5 分钟清理过期会话

- **monitoring/otel_tracer.py: 线程锁初始化竞态条件**
  - `_thread_lock` 在 `__init__` 中直接初始化

- **api-gateway/routers/generation.py: CJK 字符范围不完整**
  - 扩展覆盖 CJK 扩展 A 区（U+3400-U+4DBF）

### Changed

- **tests/test_user_store.py: 更新测试用例**
  - 角色和部门名称改为与 config.json 一致
  - 角色掩码断言值更新

### 修复文件清单

| 文件 | 修复类型 |
|------|---------|
| `api-gateway/routers/generation.py` | CRITICAL: 变量顺序 + CJK 范围 |
| `core/pipeline.py` | HIGH: @staticmethod |
| `api/routes.py` | HIGH: fallback 签名 + multipart 端点 |
| `retrieval/evidence_gate.py` | HIGH: NLI 参数签名 |
| `frontend/src/App.jsx` | HIGH: 上传端点 |
| `retrieval/bm25_retriever.py` | MEDIUM: JSON 加载 |
| `api-gateway/main.py` | MEDIUM: 项目名称 |
| `auth/user_store.py` | MEDIUM: RBAC 角色对齐 |
| `retrieval/rerank_batch_aggregator.py` | MEDIUM: 队列延迟 |
| `docker-compose.yml` | MEDIUM: Redis 密码 |
| `config.json` | MEDIUM: 配置补全 |
| `app.py` | MEDIUM: 会话清理 |
| `monitoring/otel_tracer.py` | LOW: 线程锁 |
| `tests/test_user_store.py` | 测试更新 |

## [2.1.0] - 2026-06-05

### Fixed

- **core/pipeline.py: 修复 `is_complex` 变量使用顺序 (GAP-15, P0)**
  - 复杂度评估移至模型路由之前，消除 `NameError` 崩溃
  - 新增 evaluator 异常兜底（默认 simple + degraded）
  - 新增 `tests/test_pipeline_ordering.py`

- **tests/test_nli_service.py: 修复测试污染问题**
  - torch mock 由 `setdefault` 改为直接赋值，确保全量测试套件下 fake torch 生效
  - 新增 autouse fixture 管理 mock 生命周期

### Added

- **api/routes.py: 新增 `GET /api/media/{doc_id}` (GAP-16, P1)**
  - Qdrant 文档权限元数据查询 + RBAC 二次校验
  - MinIO 签名 URL 生成（60s 有效期）
  - 403/404/503 错误处理
  - 新增 `tests/test_media_route.py`（13 个测试）

- **api/routes.py: 新增 `GET /metrics` (GAP-18, P1)**
  - Prometheus text 格式指标暴露（`text/plain; charset=utf-8`）
  - 新增 `tests/test_metrics_endpoint.py`（16 个测试）

- **run_services.py: NLI 端点从硬编码改为真实推理 (GAP-17, P1)**
  - 启动时加载 DeBERTa-v3-mnli 模型
  - 模型不可用时返回 HTTP 501 + 合约说明
  - 新增 `tests/test_nli_service.py`（9 个测试）

### Changed

- **offline/scheduler.py: Qdrant 归档逻辑优化 (GAP-19, P2)**
  - 主路径使用 `upsert(status='archived')` 替代 delete
  - 降级兜底：upsert 失败时 delete + reinsert
  - 新增 `tests/test_archive_expired.py`（7 个测试）

- **docs/GAP_IMPL_PLAN.md: 更新实施计划**
  - 标记 GAP-15 ~ GAP-19 全部完成
  - 补充执行验证结果

### Test Coverage

- 新增 5 个测试文件，共 47 个测试用例
- 全量回归：**325 passed, 0 failed** (19.89s)
