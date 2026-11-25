# Documentation index

Start with the [project README](../README.md) for setup, architecture, evaluation and status. This index maps canonical references; the [evidence map](evidence-map.md) owns capability evidence, the [architecture baseline](architecture-baseline.md) owns the current runtime architecture, and dated validation records own their recorded run results.

## Version labelling

The canonical runtime version is `config.json` → `system.version` = `2.4.0`; the latest dated changelog release and current GitHub release are `[2.4.0]` / `v2.4.0`. Changes after that release remain under `[Unreleased]`. `v2.5` in filenames or documents is a historical working-milestone label, not a release; see [version policy](repository-truth-audit.md#version-policy).

## Evidence vocabulary

The [classification vocabulary](evidence-map.md#classification-vocabulary) is the single source of truth. This index repeats its exact inventory for quick reference:

- `HISTORICAL_PRODUCTION` — legacy classification name retained for compatibility only; no third-party production facts are published under it.
- `HISTORICAL` — superseded in-repository implementation, configuration or design retained for lineage.
- `REPO_VERIFIED` — implemented here and covered by deterministic tests or CI.
- `LOCAL_REAL_VALIDATION` — exercised against a real dependency on one local host.
- `DESIGN_TARGET` — recorded target/design without a reproducible benchmark.
- `PENDING` — real asset, runtime or credential required for validation is not available here.

`PARTIAL`, `EXECUTED`, `BLOCKED`, `PASS`, `NOT RUN` and `NOT EXECUTED` are run outcomes, not evidence levels; see [run outcomes](evidence-map.md#run-outcomes-that-are-not-evidence-levels).

## Canonical / Current

### Getting started and operation

- [Project overview and Quick Start](../README.md)
- [User guide](user-guide.md) · [Operations guide](operations-guide.md) · [Deployment guide](deployment-guide.md)
- [Kubernetes deployment contract](deployment-guide-k8s.md): static contract only; Docker Compose remains canonical and a real cluster run is pending.
- [Data administration guide](data-admin-guide.md) · [Industrial release contract](industrial-release-contract.md)
- [Configuration](../config.json) · [Environment variable template](../.env.example) · [API models](../api/models.py)
- [Data provenance and non-affiliation boundary](../DATA_PROVENANCE.md), backed by [`DATA_MANIFEST.json`](../DATA_MANIFEST.json) and the public-boundary checker.
- [Pre-launch checklist](pre-launch-checklist.md) · [SLO and incident runbook](slo-runbook.md)
- [Demo capture guide](demo/README.md): generated interface image uses synthetic mock data, not the real API.

### Architecture, security and evidence

- [Architecture baseline](architecture-baseline.md): canonical online and offline flow and architecture decision freeze.
- [Evidence map](evidence-map.md): canonical taxonomy, capability evidence and benchmark publication criteria.
- [Repository truth audit](repository-truth-audit.md) · [Production readiness](production-readiness.md)
- [Security regression coverage](security-regression-coverage.md) · [Security policy](../SECURITY.md)
- [Main-branch governance](main-branch-governance.md) · [Repository metadata record](repository-metadata.md)
- [Open-source project reference notes](open-source-project-references-2026-11-25.md)
- [Open-source hardcoding audit](open-source-hardcoding-audit.md) · [Repository drift audit](repository-drift-report.md) · [Canonical runtime audit](final-canonical-runtime-audit.md)
- [Agent V1 acceptance scope](agent-v1-acceptance.md): implementation/evidence scope, not a production acceptance report.

### Evaluation

- [RAGAS evaluation guide](ragas-evaluation-guide.md) · [Benchmark data quality](benchmark-data-quality.md)
- [Retrieval benchmark artifact contract](../artifacts/benchmarks/README.md) · [Performance artifact contract](../artifacts/performance/README.md)
- [Real Corpus V1 approved BM25 run](validation/real-v1-approved-bm25-run.md) · [Chinese analyzer comparison](validation/chinese-analyzer-ab.md)
- [RAG evaluation readiness](validation/rag-eval-readiness.md) · [Reviewed answer evaluation](validation/paired-reviewed-answer-eval.md)
- [Reviewed answer evaluation details](validation/reviewed-answer-eval.md) · [Retrieval failure triage](validation/retrieval-failure-triage.md)
- [Strict retrieval suite](validation/strict-retrieval-suite-20261124.md) · [Strict rerank experiment](validation/strict-rerank-experiment-20261124.md)
- [Dense experiment builder](validation/approved-dense-experiment-builder-20261124.md) · [Dense corpus parity](validation/dense-corpus-parity-20261124.md)
- [Strict RAGAS pipeline](validation/strict-ragas-pipeline-20261124.md) · [RAGAS result aggregation](validation/ragas-result-aggregation-20261124.md)
- [Paired ranking regression](validation/real-v1-paired-ranking-regression-20261124.md) · [Source approval and snapshot integrity](validation/source-approval-snapshot-integrity.md)

### Validation and operations

- [Deferred runtime validation index](deferred-runtime-validation.md): external assets, required environment, command and evidence-promotion criteria.
- [Real RAGAS evaluation status](validation/real-ragas-evaluation.md) · [Qdrant local validation](validation/qdrant-local-real-validation.md)
- [Context-memory/cache isolation](validation/context-memory-cache-isolation-20261124.md) · [Strict backend failure behavior](validation/strict-backend-failures-20261124.md)
- [Runtime/security validation snapshot](validation/v2.5-runtime-security-validation.md): `v2.5` is a historical working label, not a release.
- [Real corpus pre-review](validation/real-corpus-v1-pre-review.md) · [Real RAG evaluation bootstrap](validation/real-rag-evaluation-bootstrap.md)
- [Earlier BM25 run snapshot](validation/real-v1-bm25-run.md) · [Production-index governance](validation/production-index-governance.md)
- Each dated validation record is scoped to its stated data and environment; it does not override the current architecture or evidence map.

### Governance snapshots and backlog

- [Finalization index](finalization/README.md): [engineering audit](finalization/FINAL_ENGINEERING_AUDIT.md) · [acceptance matrix](finalization/ACCEPTANCE_MATRIX.md) · [evidence manifest](finalization/EVIDENCE_MANIFEST.md) · [reproducible evaluation](finalization/REPRODUCIBLE_EVALUATION.md) · [governance report](finalization/GOVERNANCE_REPORT.md) · [data review backlog](finalization/DATA_REVIEW_BACKLOG.md)

## Architecture & Evidence Truth

The [architecture baseline](architecture-baseline.md) is the canonical runtime diagram and architecture description. The [evidence map](evidence-map.md) is the canonical capability/evidence classification; validation records are scoped to their named data, model and environment.

## Design

- [Product requirements and architecture design](../PRD.md): goals and targets are labelled separately from current implementation.

## Historical / Implementation Plans

Any Markdown retained as historical documentation belongs under [`docs/archive/`](archive/README.md), begins with a historical-status banner and is not a source for current capability, architecture, metric or validation claims. Current documentation and dated validation records remain in their canonical folders. The former `docs/superpowers/` files are not present in the current tree; Git history is the only remaining source for them.

When documents disagree, current code/configuration and the canonical [architecture baseline](architecture-baseline.md), [evidence map](evidence-map.md) and [repository truth audit](repository-truth-audit.md) take precedence over historical snapshots.
