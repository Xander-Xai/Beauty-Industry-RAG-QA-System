# Final canonical-runtime consistency audit

Audit of the canonical/current surfaces against the code and configuration on the post-#55/#56
tree (`main` at `0c99724`). This is a **fact-consistency audit, not a feature change**: no
architecture, evidence level, benchmark, result, runtime behaviour or release version was altered,
and no contradiction below was resolved by weakening a boundary.

Its authority is bounded on purpose. Every row is a claim compared against code, config or a
committed artifact, so the reader can re-run the same check. It does **not** and cannot establish
anything about real vLLM servers, real GPU topology, real Kubernetes clusters, real retrieval
quality or real QPS/latency — those remain `PENDING` and are tracked in
[the truth audit](repository-truth-audit.md#external-validation-pending).

Surfaces audited: `README.md`, `PRD.md`, `docs/architecture-baseline.md`,
`docs/evidence-map.md`, `docs/repository-truth-audit.md`, `docs/README.md`,
`docs/operations-guide.md`, `.env.example`, `docker-compose*.yml`, `deploy/k8s/`, `CHANGELOG.md`,
`config.json`.

Every path cited below is written in full and resolves in the current tree, so each row can be
re-checked without guessing where a file lives. Citations name a file and, where useful, a
section heading rather than a line number: a `file:line` citation goes stale the next time an
unrelated paragraph is inserted above it, and a stale citation in an audit document is worse
than no citation, because it invites re-verifying the wrong line.

## Contradiction audit table

| # | Claim | Source | Code / config evidence | Classification | Action |
|---|---|---|---|---|---|
| 1 | Canonical online architecture is a FastAPI monolith with a React frontend | `docs/architecture-baseline.md` §一句话结论 and §当前主链路, `README.md` §Architecture | `app.py` is the single entrypoint; `frontend/package.json` declares `react ^18.3.1`; `api/routes.py` serves the online path in-process | `REPO_VERIFIED` | Consistent — no change |
| 2 | One shared 4B endpoint serves rewrite **and** simple generation; 14B serves complex generation | `docs/repository-truth-audit.md` §Status vocabulary ("Generation topology" row), `PRD.md` §4.2, `config.json` `model_routing.tiers` | `config.json` maps `simple` → `gen_4b` and `rewrite` → `gen_4b`, `complex` → `gen_14b`; `router/stateless_router.py` `StatelessRouter` endpoint resolution resolves both keys from `gpu1.models.vllm_4b` (port 8101) and `gpu0.models.gen_14b` (port 8100) | `REPO_VERIFIED` (topology in config/code) / `PENDING` (real GPU run) | Consistent — no change |
| 3 | The dual-GPU topology is **not** a validated production deployment | `docs/repository-truth-audit.md` §Audit lineage (runtime-exposed metadata audit), `CHANGELOG.md` §Unreleased | `config.json` carries `system.dual_gpu: true`, but `deployment_mode` is `development` and `is_production_mode()` reads only that field; `tests/test_runtime_api_metadata.py` fails if a topology claim returns to the OpenAPI `info` block | `PENDING` | Consistent — the flag is a declared target, and no doc presents it as executed |
| 4 | Microservice directories are components; integrated deployment is `PENDING` | `README.md` §Architecture and §Repository Layout, `docs/repository-truth-audit.md` §Status vocabulary ("Microservices" row), `docs/pre-launch-checklist.md` header | `api-gateway/`, `retrieval-service/`, `generation-service/`, `monitoring-service/`, `cache-service/`, `rewrite-service/` exist with code; no doc calls them the canonical online path, and `docker-compose.microservices.yml` is referenced by no canonical doc | `REPO_VERIFIED` (components) / `PENDING` (integrated deployment) | Consistent — no change |
| 5 | Historical production scale figures appear only as `HISTORICAL_PRODUCTION` | `docs/evidence-map.md` §Business scale — historical production context, `README.md` §Historical Production Context | The evidence map classifies all eleven figures (3000+ docs, 5000+ images, 1500+ products, 2000+ ingredients, 8 systems, 200+ users, 10–15 QPS, 1500+ daily requests, RTX A5000 ×2, Qwen2.5 → Qwen3 migration, annual award) in one table, every row `HISTORICAL_PRODUCTION` | `HISTORICAL_PRODUCTION` | Consistent — no change |
| 6 | The PRD's early 500+ document figure stays a design-stage baseline, distinct from production scale | `PRD.md` §3.1 | §3.1 is titled "数据范围（设计阶段 baseline）", states the figure is **not** current reproducible production scale, and points to the top-of-file `HISTORICAL_PRODUCTION` block for the real figures | `DESIGN_TARGET` | Consistent — no change |
| 7 | No unreproducible historical number is presented as a current repository result | whole tree | Repo-wide search: `92.3`, `84.7`, `250 QPS`, `0.8s` return no hits in any claim (the only `92.3` is a port number inside a test URL parameter; the only `84.7` is a digest substring in `deploy/minio/Dockerfile`). No accuracy/recall/QPS/latency figure appears without a `HISTORICAL_PRODUCTION`, `DESIGN_TARGET` or `SYNTHETIC DEMO` marker | absent | Consistent — no change |
| 8 | The bounded vLLM resilience contract claims only what its tests cover | `docs/repository-truth-audit.md` §Status vocabulary ("vLLM generation resilience" row) and §External validation pending, `CHANGELOG.md` §Unreleased | 124 deterministic tests in `tests/test_vllm_generation_resilience.py` drive an `httpx.MockTransport` endpoint and a manual clock. `TRANSIENT_HTTP_STATUSES == {408,429,502,503,504}` (500 absent); `HARD_MAX_ATTEMPTS=3`, `DEFAULT_MAX_ATTEMPTS=2`; the counters are not referenced by `monitoring/prometheus/alerts.yml` | `REPO_VERIFIED` (contract + deterministic tests) / `PENDING` (real vLLM runtime) | Consistent — no change |
| 9 | Runtime validation trackers stay open | `docs/repository-truth-audit.md` tracker map | #8, #12, #18, #32 were recorded `open`; **#54 was absent from both the tracker map and `OPEN_EXTERNAL_VALIDATION_TRACKERS`** although it is an open external-validation issue | `PENDING` | **Fixed** — #54 recorded as an open tracker; #47 recorded as a completed reconciliation |
| 10 | Metric series named in documents are actually emitted by `/api/metrics` | `docs/evidence-map.md` §Capability evidence, `docs/repository-truth-audit.md` §External validation pending | Both documents cite emitted series, but neither was in `OPERATIONAL_METRIC_DOCS`, so a phantom series written into either would have passed CI. Additionally the explicit family glob ``rag_http_*`` was reported as a non-emitted series because the token regex absorbs the trailing underscore | gap in the guard | **Fixed** — guard scope widened to the evidence map, truth audit and README; glob family references recognised |
| 11 | Documented local links, anchors and code paths resolve | all canonical docs | 186 local links and anchors across `README.md`, `PRD.md`, `docs/*.md` and `docs/validation/*.md`: 0 unresolved. 707 backticked path references: 17 unresolved, none introduced by this document — all are pre-existing (benchmark artifact filenames that must **not** exist, historical CHANGELOG entries, and short forms such as a workflow filename mentioned beside its directory) | consistent | Consistent — this document adds no unresolvable reference |
| 12 | Documented config keys, environment variables and constants exist | `.env.example`, `docs/repository-truth-audit.md`, `docs/operations-guide.md` | 28 dotted `config.json` references resolve except three `auth.login.*` audit **action names** (real constants in `common/audit.py` `KNOWN_ACTIONS`). `VLLM_4B_URL` / `VLLM_GEN_14B_URL` / `VLLM_MAX_ATTEMPTS` / `VLLM_TIMEOUT_SECONDS` / `VLLM_RETRY_*` / `VLLM_GENERATION_DEADLINE_SECONDS` all exist in `.env.example` and are read in code. The documented removal of `config.json` → `alerting.rules` is true: neither key exists in the file | `REPO_VERIFIED` | Consistent — no change |
| 13 | Deploy-time claims match the manifests | `deploy/k8s/`, `docker-compose*.yml` | `deploy/k8s/api-deployment.yaml` targets `/api/ready` for readiness and `/api/health` for startup, matching `api/readiness.py`; `deploy/k8s/configmap.yaml` sets the two `VLLM_*_URL` keys the Deployment reads. `docker-compose.yml` (canonical), `.cpu`, `.gpu`, `.observability` and `.microservices` all parse; canonical services are `app/redis/qdrant/minio/elasticsearch` | `REPO_VERIFIED` (manifests/parsing) / `PENDING` (real cluster) | Consistent — no change |
| 14 | Version labelling is honest | `README.md` badge paragraph, `config.json`, `CHANGELOG.md` §Unreleased | `config.json` → `system.version` is `2.3.0` and `CHANGELOG.md`'s latest dated release entry is `[2.3.0]`, with the vLLM resilience work under `[Unreleased]`. No release bump was made | `REPO_VERIFIED` | Consistent — no change |

## Findings and what changed

Two real findings, both closed. Everything else verified consistent.

**#9 — an open validation tracker was missing from the recorded lineage.** The truth audit's
tracker map declared `Current reconciliation scope: **none**` while #47 was an open reconciliation
issue, and #54 — an open external-validation issue for real Kubernetes deployment — appeared in
neither the map nor `OPEN_EXTERNAL_VALIDATION_TRACKERS`. The audit documents that this map is a
GitHub-state snapshot, but the guard's own rule requires the numbers to move in the same commit
that changes the state. Fixed by recording #47 as a completed reconciliation, #54 as an open
tracker, and updating both constants.

**#10 — three claim-heavy documents were exempt from metric-series verification.** The guard only
scanned the four operational guides, on the reasoning that only those carry references an operator
pastes into a query. But the evidence map and the truth audit both enumerate emitted series while
classifying evidence, and the README names `/api/metrics` as the endpoint carrying them. Writing a
phantom series into any of the three would have passed CI. Fixing the scope alone would have
produced a false failure, because ``rag_http_*`` is a legitimate family reference that the token
regex misread: it absorbs the trailing underscore, so the prefix test compared `rag_http__`
against the inventory. Both halves are fixed — the glob form is recognised as a family reference,
and the three documents are in scope. Verified: a glob over a family that is never emitted, and a
bare invented series, are both still flagged.

## What this audit does not establish

- No real vLLM server, GPU topology, model weight or retry-amplification measurement.
- No Kubernetes cluster has applied `deploy/k8s/`; no `/api/ready` transition has been observed.
- No retrieval benchmark artifact, recall/accuracy figure or RAGAS quality score.
- No reproducible QPS or latency result. The only such figures remain `HISTORICAL_PRODUCTION`
  (former-employer observations) or `DESIGN_TARGET` (SLO and alert thresholds).

Trackers #8, #12, #18, #32 and #54 stay open for exactly these reasons.
