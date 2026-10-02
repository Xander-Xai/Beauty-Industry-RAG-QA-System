# Documentation index

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

## Validation

- [v2.5 runtime/security validation](validation/v2.5-runtime-security-validation.md): Redis
  multi-worker, trusted proxy, authenticated Elasticsearch and Prometheus evidence.
- [Real RAGAS evaluation](validation/real-ragas-evaluation.md): evaluator dependency
  isolation, correctness fixes, and the real-evaluation blockers.

## Interview / Architecture truth

- [Interview architecture baseline](interview-architecture-baseline.md): the current
  end-to-end retrieval/generation contract an interviewer can hold the code to.
- [Interview evidence map](interview-evidence-map.md): classification of every claim
  as `HISTORICAL_PRODUCTION`, `REPO_VERIFIED`, `LOCAL_REAL_VALIDATION`, `DESIGN_TARGET`
  or `PENDING`.

## Design

- [PRD](../PRD.md): product and architecture design. It records goals and target design as
  well as implemented capabilities; the runtime reconciliation table at the top separates
  current implementation from historical/target design.

## Historical / Implementation Plans

- [`docs/superpowers/plans/`](superpowers/plans/)
- [`docs/superpowers/specs/`](superpowers/specs/)

Historical plan ≠ current implementation status. Keep historical plans intact; use the audit
and canonical guides for the current repository state.
