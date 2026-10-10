# Evidence manifest

Every capability statement in this repository, mapped to the source that
implements it, the test that fails if the control is removed, the CI job that runs
that test, any artifact it leaves, and its evidence level.

Levels (canonical vocabulary: [`docs/evidence-map.md`](../evidence-map.md)):

- **PASS_CODE** — implemented and unit-tested, no external dependency.
- **PASS_LOCAL_REAL** — executed against a real dependency on this host.
- **PASS_CI** — the CI job is green on this branch.
- **BLOCKED_EXTERNAL** — cannot run without an asset/service that is absent.
- **NEEDS_HUMAN** — requires a human decision that has not been made.
- **NOT_VERIFIED** — claimed nowhere; no evidence either way.

**Code commit under test:** `61be8a9`; the closeout documents are committed on top. PR #92 was squash-merged into `main` as `f3d03054d1f108d9ce82abb4499f65a4e0967e46`; the verification results below are for `61be8a9` (code unchanged by the doc commits). · 2026-10-10 UTC. CI status: see [`ACCEPTANCE_MATRIX.md`](ACCEPTANCE_MATRIX.md).

## 1. Retrieval benchmark integrity

| capability | source | test | CI | artifact | level |
|---|---|---|---|---|---|
| Multi-passage ground truth is not collapsed to one item | `benchmarks/relevance.py::relevant_items_from_annotations`, `benchmarks/dataset.py::_relevant_items_for_row` | `tests/benchmark/test_review_regressions.py` | 测试套件 | — | PASS_CODE |
| `doc_id::chunk_id` identity keeps chunks of one document distinct | `benchmarks/relevance.py::relevance_key` | `tests/benchmark/*` + `tests/evaluation/test_golden_set_contract.py` | 测试套件 | — | PASS_CODE |
| A row-level id with several passages is refused | `benchmarks/dataset.py::AmbiguousRelevanceIdentityError` | `tests/benchmark/test_review_regressions.py` | 测试套件 | — | PASS_CODE |
| A synthetic/mock retriever can never be published as a benchmark result | `benchmarks/retrieval_benchmark.py` (`results_are_benchmark`, `synthetic_retriever`) | `tests/benchmark/test_publication_gate.py` | 评估确定性守卫 | `metadata.json` | PASS_CODE |
| Unreviewed (LLM) labels cannot enter official scoring | `benchmarks/annotation.py`, `benchmarks/golden_set_contract.py` | `tests/evaluation/test_annotation_lifecycle.py` | 测试套件 | — | PASS_CODE |
| Corpus hash / index identity is bound to the labels | `benchmarks/corpus.py`, `golden_set_contract.qdrant_resolver`/`elasticsearch_resolver` | `tests/benchmark/*`, `tests/validation/*` | 测试套件 | `metadata.json → corpus` | PASS_CODE |
| A self-referential corpus is refused | `benchmarks/backends.py` (read-only probe) | `tests/benchmark/test_publication_gate.py` | 评估确定性守卫 | — | PASS_CODE |
| The 301-row set is not auto-promoted to human Gold | `tests/evaluation/golden_set_v2/annotations_v1.jsonl` (empty) + contract | `tests/evaluation/test_golden_set_contract.py` | 测试套件 | — | PASS_CODE |

## 2. Rerank / Evidence Gate honesty

| capability | source | test | CI | artifact | level |
|---|---|---|---|---|---|
| A weight-load failure cannot be published as a real rerank | `retrieval/rerank_validation.py::run_comparison` | `tests/test_engineering_closeout_regressions.py::test_F1_*` | 测试套件 | `artifacts/rerank/*` | PASS_CODE |
| The rerank comparison can actually detect a reordering (no `.mean()` collapse) | `retrieval/rerank_validation.py::_CrossEncoderRanker.rank`, `candidate_order` | `test_F2_*` | 测试套件 | — | PASS_CODE |
| `config.json` alone is not a model | `retrieval/rerank_status.py::_model_available` | `test_F3_*` | 测试套件 | — | PASS_CODE |
| Rerank provenance is a runtime observation, not a filesystem guess | `retrieval/cross_encoder_ensemble.py::rerank`, `core/run_report.py::rerank_evidence` | `test_F7_*` | 测试套件 | `artifacts/rerank/*` | PASS_CODE |
| The validation harness never downloads weights | `retrieval/rerank_validation.py` | `tests/test_rerank_validation_honesty.py` | 测试套件 | — | PASS_CODE |
| A real CrossEncoder run produced a rerank metric | — | — | — | — | **BLOCKED_EXTERNAL** (no weights) |

## 3. Evidence Gate / Answer Gate / RBAC

| capability | source | test | CI | artifact | level |
|---|---|---|---|---|---|
| No-evidence requests are refused upstream | `retrieval/evidence_gate.py`, `core/pipeline.py:464-505` | `tests/test_evidence_gate.py`, `tests/test_evidence_gate_degradation.py` | 测试套件 | run report | PASS_CODE |
| A normal evidence-backed answer **passes** (positive control) | `retrieval/answer_gate.py` | `tests/test_gate_degradation_scenarios.py` | 测试套件 | — | PASS_CODE |
| `AnswerGate.verify(top_doc=None)` is unreachable on the monolith path | `retrieval/evidence_gate.py:105-115` + `core/pipeline.py` ordering | `tests/test_answer_gate_no_evidence_reachability.py` | 测试套件 | — | PASS_CODE |
| Cross-role / cross-dept retrieval leakage is filtered before and after fusion | `retrieval/parallel_recall.py:94-108,275,502,535` | `tests/test_rbac_cross_identity.py`, `tests/test_retrieval_authorization_contract.py` | 测试套件 | — | PASS_CODE |
| Cross-role cache leakage is prevented (physical L2 partition) | `cache/redis_cache.py` | `tests/test_cache.py` | 测试套件 | — | PASS_CODE |
| Cross-identity **session** leakage is prevented | `core/pipeline_context.py` (owner-scoped) | `test_F4_*` | 测试套件 | — | PASS_CODE |
| These controls hold against a real Elasticsearch / Qdrant | — | `tests/integration/test_*_runtime.py` (gated) | — | `docs/validation/*` | **BLOCKED_EXTERNAL** |

## 4. HTTP / runtime contract

| capability | source | test | CI | artifact | level |
|---|---|---|---|---|---|
| P2 overload returns a real 503 | `core/pipeline.py::_handle_rejection`, `api/routes.py` | `test_F5_*` | 测试套件 | — | PASS_CODE |
| vLLM timeout → 200-with-error-body (documented divergence) | `router/stateless_router.py`, `core/pipeline.py:586-592` | `tests/test_gate_degradation_scenarios.py` | 测试套件 | — | PASS_CODE (documented) |
| Run report names the true terminal outcome | `core/run_report.py::_classify_outcome` | `test_F6_*`, `tests/test_run_report.py` | 测试套件 | run report | PASS_CODE |

## 5. Access control

| capability | source | test | CI | artifact | level |
|---|---|---|---|---|---|
| Malformed JWT / dev-header masks fail closed | `common/auth.py:127-230` | `tests/test_auth_identity_resolution.py` | 测试套件 | — | PASS_CODE |
| Document authorization denies malformed metadata | `common/auth.py::is_document_authorized` | `tests/test_rbac_cross_identity.py` | 测试套件 | — | PASS_CODE |

## 6. Explicitly out of scope / unverified

| statement | level |
|---|---|
| Any Hit@K / Recall@K / NDCG / MRR number | **NOT_VERIFIED** — no corpus |
| Any RAGAS / faithfulness score | **NOT_VERIFIED** — evaluator gated, not run |
| QPS / P95 under load | **NOT_VERIFIED** — no load test executed |
| Dual RTX A5000 production validation | **NOT_VERIFIED** — host has 1× RTX 5060 Ti |
| The microservice path enforces the Answer Gate | **NOT_VERIFIED** — it does not (`api-gateway/`) |
| Enterprise data / golden set / regulation corpus quality | **NEEDS_HUMAN** |

## 7. How each level was established

- **PASS_CODE**: the test runs in the local `pytest` suite and in the
  `测试套件` / `评估确定性守卫` CI jobs.
- **PASS_LOCAL_REAL**: executed on this host against the real component (e.g. the
  in-memory Qdrant / fake ES in `tests/offline/`; note these are *simulated* and
  are labelled as such).
- **BLOCKED_EXTERNAL**: `--list-configs` prints `BLOCKED` with a machine-readable
  reason; no number is produced.
