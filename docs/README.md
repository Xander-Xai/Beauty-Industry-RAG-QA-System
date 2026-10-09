# Documentation index

## Version labelling

Canonical runtime version is `config.json` → `system.version` = `2.4.0`; the newest dated
release heading in `CHANGELOG.md` is `[2.4.0]` (2026-10-08) and everything after it is
recorded under `[Unreleased]`.

Where a doc or filename says **v2.5**, that is a **historical working milestone /
development-phase label**, not a release and not the current runtime version — see
[Version policy](repository-truth-audit.md#version-policy). Files are not renamed to avoid
breaking existing links.

## Evidence vocabulary

One canonical taxonomy classifies every evidence claim in the current documentation:
[Classification vocabulary](evidence-map.md#classification-vocabulary). That
table is the single source; this index does not extend it.

- `HISTORICAL_PRODUCTION` — work actually done in a former employer's production environment.
- `HISTORICAL` — a superseded in-repository implementation/config/design kept for lineage.
- `REPO_VERIFIED` — implemented here and covered by collected deterministic tests or CI.
- `LOCAL_REAL_VALIDATION` — exercised here against a real dependency on one local host.
- `DESIGN_TARGET` — a recorded PRD/plan target with no implementation or reproducible benchmark.
- `PENDING` — implemented but the real asset/runtime/credential needed to validate it is missing here.

`PARTIAL`, `EXECUTED`, `BLOCKED`, `PASS` and `NOT RUN` are **run outcomes**, not evidence
levels; see [Run outcomes that are not evidence levels](evidence-map.md#run-outcomes-that-are-not-evidence-levels).

## Canonical / Current

- [Project overview and quick start](../README.md)
- [Deployment guide](deployment-guide.md)
- [Kubernetes deployment contract](deployment-guide-k8s.md): the minimal second deployment form
  (`deploy/k8s/`, one workload — the FastAPI monolith, not the `api-gateway/` component).
  Manifests are `REPO_VERIFIED` by static check only; any
  real cluster deployment is `PENDING`. Docker Compose remains the canonical form.
- [User guide](user-guide.md)
- [Operations guide](operations-guide.md)
- [Data administration guide](data-admin-guide.md)
- [Repository truth audit](repository-truth-audit.md)
- [Final canonical-runtime consistency audit](final-canonical-runtime-audit.md): the post-merge
  re-verification of architecture, generation topology, evidence taxonomy, historical scale and
  evidence boundaries against the code and config, with a claim/source/code-evidence/classification
  table. It is an audit record, not a new capability source, and it promotes no evidence.
- [Repository metadata packet](repository-metadata.md): the intended GitHub About configuration
  (description, 20 topics, homepage policy, social preview) and the rules it must obey — no
  homepage that points at a deployment that does not exist, and nothing in the metadata that
  outruns the evidence.
- [Security regression coverage](security-regression-coverage.md): the fixed threat list
  (retrieved-chunk injection, poisoned documents, forged boundary markers, cross-role
  retrieval and L2 cache leakage, malformed JWT claims, deletion/stale chunks), the control
  and test behind each, and the bounded gaps that remain open.
- [Production readiness](production-readiness.md): the per-capability answer to "can this take
  real traffic" — code status separated from real-environment status, each with the named
  artifact that would promote it. Owns the condensed
  [Production Readiness](../README.md#production-readiness) table, the fail-closed degradation
  property under absent rerank weights, and the concrete boundary of the six retained
  microservice components.
- [Open-source configuration and hardcoding audit](open-source-hardcoding-audit.md)
- [Main-branch governance](main-branch-governance.md): the branch-protection and PR-only
  merge policy, how it is applied and re-verified.
- [Repository drift report](repository-drift-report.md): the point-in-time documentation
  truth audit that recorded and resolved the P0/P1 conflicts across the canonical documents.
- [Pre-launch checklist](pre-launch-checklist.md)

## Evaluation

- [RAGAS evaluation guide](ragas-evaluation-guide.md): offline quality-evaluation harness,
  golden set, reporter and CI boundary.
- [Retrieval benchmark artifact contract](../artifacts/benchmarks/README.md): the six-file
  run contract, what `BLOCKED` means, and why a real run still cannot produce a metric.
- [Benchmark data quality](benchmark-data-quality.md): measured golden-set field coverage and the
  buckets the retrieval benchmark deliberately does not produce.

Retrieval benchmark framework: `REPO_VERIFIED` (deterministic metrics, provenance,
artifact contract, covered by tests).
Retrieval benchmark result: `PENDING` — no reproducible artifact exists, so no
retrieval metric is claimed anywhere in the repository.

## Operations / Observability

- [SLO and incident runbook](slo-runbook.md): five objectives (all `DESIGN_TARGET`) plus
  alert → diagnosis → mitigation → rollback procedures for Redis, Elasticsearch, Qdrant,
  model endpoints, latency, error rate and knowledge-epoch release.
- [Prometheus alert rules](../monitoring/prometheus/alerts.yml): six alerts over metrics this
  application actually emits. Every threshold is a `DESIGN_TARGET`.
- [OTLP exporter](../monitoring/otel_exporter.py) and the
  [optional observability overlay](../docker-compose.observability.yml): opt-in span export plus a
  Prometheus/Jaeger/Grafana stack. The canonical deployment does not start any of it.
- [Performance evidence artifact contract](../artifacts/performance/README.md): seven-file run
  contract where *not executed* is never recorded as zero.

Performance artifact framework: `REPO_VERIFIED`.
Performance result, alerting validated in production, and OTLP runtime closed loop: `PENDING`.

## Validation

- [v2.5 working-milestone runtime/security validation](validation/v2.5-runtime-security-validation.md):
  Redis multi-worker, trusted proxy, authenticated Elasticsearch and Prometheus evidence.
  (`v2.5` is a working-milestone label, not a release.)
- [Real RAGAS evaluation](validation/real-ragas-evaluation.md): evaluator dependency
  isolation, correctness fixes, and the real-evaluation blockers.
- [Real Qdrant store validation](validation/qdrant-local-real-validation.md): a single-host
  run against a real Qdrant server (`VAL-STORE-001`) covering epoch point ids, payload
  filters, RBAC re-filter, text+image RRF merge and Qdrant-down degradation, with
  deterministic vectors (not BGE/CLIP).
- [RAG evaluation readiness](validation/rag-eval-readiness.md): the golden-set v2 contract
  coverage (0/301 valid), the answer-level harness, and the exact reason Hit@5/NDCG@10
  ablation A–E, answer-quality metrics and QPS are `BLOCKED` here — with the environment
  checklist that would promote them.
- [Deferred runtime validation index](deferred-runtime-validation.md): the single
  index of every runtime validation, each with its required environment, procedure,
  expected artifact and evidence-promotion rule. `VAL-DEGRADE-001` and `VAL-STORE-001`
  are executed; the rest are `NOT EXECUTED`.

## Architecture & Evidence Truth

- [Architecture baseline](architecture-baseline.md): the current
  end-to-end retrieval/generation contract the code can be held to.
- [Evidence map](evidence-map.md): the canonical evidence vocabulary
  and the classification of every claim. This map owns the taxonomy; the truth audit's
  `Status` column and the validation records' `Evidence level` columns use the same
  levels.

## Design

- [PRD](../PRD.md): product and architecture design. It records goals and target design as
  well as implemented capabilities; the runtime reconciliation table at the top separates
  current implementation from historical/target design.

## Historical / Implementation Plans

Historical documentation lives under [`docs/archive/`](archive/README.md) and nowhere else.
Every Markdown file under `docs/` is exactly one of two things:

- **current/canonical**, listed in this index above and held to the repository
  consistency guard, or
- **historical**, under `docs/archive/`, whose opening lines mark it as
  historical and state that it is not a source for current capability,
  architecture, metric or validation claims.

`docs/demo/` is current/canonical but holds a generated demo, not a prose guide, so it
is indexed as a single entry rather than file by file: see
[the demo README](demo/README.md).

The former `docs/superpowers/` directory was removed from the current branch and
nothing replaced it; its files remain retrievable from this repository's Git
history, which is their only archive.

- [Repository truth audit](repository-truth-audit.md) and the canonical guides above are the
  only sources for the current repository state.

Historical plan ≠ current implementation status, and a plan that has been executed is not a
current capability statement. When a historical plan and a current document disagree, the
current document wins: [architecture baseline](architecture-baseline.md),
[evidence map](evidence-map.md) and the [repository truth
audit](repository-truth-audit.md).
