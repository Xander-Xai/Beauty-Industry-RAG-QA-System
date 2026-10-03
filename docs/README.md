# Documentation index

## Version labelling

Canonical runtime version is `config.json` → `system.version` = `2.3.0`; the newest dated
release heading in `CHANGELOG.md` is `[2.3.0]` and everything after it is recorded under
`[Unreleased]`.

Where a doc or filename says **v2.5**, that is a **historical working milestone /
development-phase label**, not a release and not the current runtime version — see
[Version policy](repository-truth-audit.md#version-policy). Files are not renamed to avoid
breaking existing links.

## Evidence vocabulary

One canonical taxonomy classifies every evidence claim in the current documentation:
[Classification vocabulary](interview-evidence-map.md#classification-vocabulary). That
table is the single source; this index does not extend it.

- `HISTORICAL_PRODUCTION` — work actually done in a former employer's production environment.
- `HISTORICAL` — a superseded in-repository implementation/config/design kept for lineage.
- `REPO_VERIFIED` — implemented here and covered by collected deterministic tests or CI.
- `LOCAL_REAL_VALIDATION` — exercised here against a real dependency on one local host.
- `DESIGN_TARGET` — a recorded PRD/plan target with no implementation or reproducible benchmark.
- `PENDING` — implemented but the real asset/runtime/credential needed to validate it is missing here.

`PARTIAL`, `EXECUTED`, `BLOCKED`, `PASS` and `NOT RUN` are **run outcomes**, not evidence
levels; see [Run outcomes that are not evidence levels](interview-evidence-map.md#run-outcomes-that-are-not-evidence-levels).

## Canonical / Current

- [Project overview and quick start](../README.md)
- [Deployment guide](deployment-guide.md)
- [User guide](user-guide.md)
- [Operations guide](operations-guide.md)
- [Data administration guide](data-admin-guide.md)
- [Repository truth audit](repository-truth-audit.md)
- [Open-source configuration and hardcoding audit](open-source-hardcoding-audit.md)
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

## Interview / Architecture truth

- [Interview architecture baseline](interview-architecture-baseline.md): the current
  end-to-end retrieval/generation contract an interviewer can hold the code to.
- [Interview evidence map](interview-evidence-map.md): the canonical evidence vocabulary
  and the classification of every claim. This map owns the taxonomy; the truth audit's
  `Status` column and the validation records' `Evidence level` columns use the same
  levels.

## Design

- [PRD](../PRD.md): product and architecture design. It records goals and target design as
  well as implemented capabilities; the runtime reconciliation table at the top separates
  current implementation from historical/target design.

## Historical / Implementation Plans

- [`docs/superpowers/plans/`](superpowers/plans/)
- [`docs/superpowers/specs/`](superpowers/specs/)

Historical plan ≠ current implementation status. Keep historical plans intact; use the audit
and canonical guides for the current repository state.
