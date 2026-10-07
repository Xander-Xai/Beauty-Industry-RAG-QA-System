# Deferred runtime validation index

> **What this is.** The single index of every runtime validation this repository
> has *not* executed, consolidated from issues
> [#8](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/8),
> [#12](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/12),
> [#18](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/18),
> [#32](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/32),
> [#54](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/54) and
> [#60](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/60).
>
> **What it is not.** It is not evidence that anything below passed. Every entry's
> status is `NOT EXECUTED`. Completing one means producing the named artifact and
> promoting the evidence level exactly as the "Evidence promotion rule" says —
> never by editing this document.
>
> Each entry is written so that, once the environment exists, the procedure can be
> run without re-deciding how to validate it. Evidence levels follow the canonical
> vocabulary in [the interview evidence map](interview-evidence-map.md#classification-vocabulary).

## Index

| ID | Capability | Level | Issue | Execution |
|---|---|---|---|---|
| VAL-RETRIEVAL-001 | Real retrieval benchmark result | `PENDING` | [#18](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/18) | `NOT EXECUTED` |
| VAL-PERF-001 | Real performance artifact (QPS / P95 / P99) | `PENDING` | [#32](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/32) | `NOT EXECUTED` |
| VAL-OBS-001 | OTLP runtime export closure (app → exporter → collector → backend → span) | `PENDING` | [#32](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/32) | `NOT EXECUTED` |
| VAL-OBS-002 | Grafana panels populated + Prometheus alerts evaluated | `PENDING` | [#32](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/32) | `NOT EXECUTED` |
| VAL-MODEL-001 | Real BGE embedding smoke | `PENDING` | [#8](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/8) | `NOT EXECUTED` |
| VAL-MODEL-002 | Real CLIP image-embedding smoke | `PENDING` | [#8](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/8) | `NOT EXECUTED` |
| VAL-MODEL-003 | Real PaddleOCR runtime smoke | `PENDING` | [#8](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/8) | `NOT EXECUTED` |
| VAL-SCHED-001 | Real Airflow DAG execution | `PENDING` | [#8](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/8) | `NOT EXECUTED` |
| VAL-GPU-001 | Real 4B / 14B vLLM GPU topology | `PENDING` | [#12](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/12) | `NOT EXECUTED` |
| VAL-RAGAS-001 | Real RAGAS quality evaluation | `PENDING` | [#12](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/12) | `NOT EXECUTED` |
| VAL-K8S-001 | Real Kubernetes deployment + readiness admission | `PENDING` | [#54](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/54) | `NOT EXECUTED` |
| VAL-E2E-001 | Browser → real RAG backend end-to-end smoke | `PENDING` | [#60](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/60) | `NOT EXECUTED` |

---

## VAL-RETRIEVAL-001 — Real retrieval benchmark result

- **Capability:** BM25 / Dense / Hybrid+RRF retrieval metrics over a real corpus.
- **Current evidence level:** `PENDING` (framework `REPO_VERIFIED`).
- **Reason deferred:** no committed benchmark corpus with ground-truth passages and no real Qdrant/Elasticsearch run; a metric cannot be produced honestly without them.
- **Required environment:** Qdrant and Elasticsearch populated from the same epoch; configured BGE and CrossEncoder assets.
- **Required data / models:** an independent golden set with ground-truth passages; the configured text embedder.
- **Prerequisite:** a real retrieval executor wired into the benchmark CLI. `artifacts/benchmarks/README.md` records that `run_configuration` receives no `retriever_factory` from the CLI, so every real configuration currently stops with "no real retrieval executor is wired"; wiring that factory is part of this validation, not assumed by it.
- **Exact procedure:** wire the executor, populate both stores, run the executor under `benchmarks/`, and commit the six-file artifact contract under `artifacts/benchmarks/<run-id>/`.
- **Expected artifact:** `artifacts/benchmarks/<run-id>/` meeting the six-file contract in `artifacts/benchmarks/README.md`.
- **Acceptance criteria:** the artifact records git SHA, dataset and config hashes, model name/revision, hardware, sample count, retrieval metrics, latency, success/failure counts, commands and limitations; the numbers are reproducible from the artifact.
- **Failure interpretation:** a `BLOCKED` run with a `blocked_reason` is a failed attempt, not a zero; it produces no publishable metric.
- **Evidence promotion rule:** `PENDING` → `REPO_VERIFIED` (result) only with a committed reproducible artifact; never to `LOCAL_REAL_VALIDATION` without the real-service run.
- **Related issue:** #18. **Status:** `NOT EXECUTED`.

## VAL-PERF-001 — Real performance artifact

- **Capability:** throughput, P50/P95/P99 and error rate against a live stack.
- **Current evidence level:** `PENDING` (framework `REPO_VERIFIED`).
- **Reason deferred:** no live API + retrieval + model stack; `tests/load/locustfile.py` blocks on an unreachable API or a missing bearer token.
- **Required environment:** canonical FastAPI deployment with reachable Redis, Qdrant, Elasticsearch and configured model endpoints.
- **Required data / models:** a declared workload; real model endpoints.
- **Exact procedure:** start the stack, run the declared workload, and write the seven-file artifact under `artifacts/performance/<run-id>/`.
- **Expected artifact:** `artifacts/performance/<run-id>/` meeting `artifacts/performance/README.md`.
- **Acceptance criteria:** declared workload, request count, concurrency, throughput, errors, P50/P95/P99 recorded; unmeasured values are `null`, never `0`; environment provenance captured.
- **Failure interpretation:** a `PARTIAL` run means the workload did not finish; unmeasured values stay `null`.
- **Evidence promotion rule:** `PENDING` → `REPO_VERIFIED` (result) only with a committed artifact from a live stack.
- **Related issue:** #32. **Status:** `NOT EXECUTED`.

## VAL-OBS-001 — OTLP runtime export closure

- **Capability:** application → exporter → collector → backend → a span actually queried.
- **Current evidence level:** `PENDING` (exporter `REPO_VERIFIED`).
- **Reason deferred:** the exporter is opt-in and disabled by default; no collector/backend has been run.
- **Required environment:** `docker-compose.observability.yml` (collector + backend) with `OTEL_EXPORT_ENABLED=true`.
- **Required data / models:** none beyond the running stack.
- **Exact procedure:** enable export, send one request, and retain the trace/request correlation record plus the queried span.
- **Expected artifact:** a committed trace-correlation record and the queried span.
- **Acceptance criteria:** `rag_otel_exporter_enabled` is on and the same request's span is retrievable from the backend.
- **Failure interpretation:** a running container is not evidence; only a queried span is.
- **Evidence promotion rule:** `PENDING` → `LOCAL_REAL_VALIDATION` only with the retained correlation record.
- **Related issue:** #32. **Status:** `NOT EXECUTED`.

## VAL-OBS-002 — Grafana panels populated and alerts evaluated

- **Capability:** dashboard panels populate from real metrics; alert rules evaluate in a running Prometheus.
- **Current evidence level:** `PENDING` (dashboard JSON and rules `REPO_VERIFIED`).
- **Reason deferred:** no running Prometheus/Grafana in the repository.
- **Required environment:** a Prometheus scraping `/api/metrics` and a Grafana importing `monitoring/grafana/dashboards/rag-overview.json`.
- **Required data / models:** none beyond the running stack.
- **Exact procedure:** import the dashboard, confirm panels populate, and exercise one alert rule to a firing state.
- **Expected artifact:** a screenshot or export of populated panels and an evaluated alert.
- **Acceptance criteria:** each panel has real data and at least one rule is observed in `firing`/`pending`.
- **Failure interpretation:** an imported-but-empty dashboard is not evidence.
- **Evidence promotion rule:** `PENDING` → `LOCAL_REAL_VALIDATION` only with the retained observation.
- **Related issue:** #32. **Status:** `NOT EXECUTED`.

## VAL-MODEL-001 — Real BGE embedding smoke

- **Capability:** the configured BGE model loads and produces finite, correctly-dimensioned vectors.
- **Current evidence level:** `PENDING` (adapter contract `REPO_VERIFIED`).
- **Reason deferred:** model weights are not in the repository.
- **Required environment:** the configured BGE model directory (`EXTERNAL_MODEL_ASSET_REQUIRED` when absent).
- **Required data / models:** the BGE weights and tokenizer.
- **Exact procedure:** `python3 scripts/smoke_bge_ingestion.py --model-path <dir>`.
- **Expected artifact:** exit code `0` with a pass line; exit `3` means the asset is missing (no false success).
- **Acceptance criteria:** dimension/finiteness/non-zero-norm checks pass and the in-memory Qdrant query returns the expected document.
- **Failure interpretation:** exit `3` is "not executed", never "passed".
- **Evidence promotion rule:** `PENDING` → `LOCAL_REAL_VALIDATION` only with a recorded pass from real weights.
- **Related issue:** #8. **Status:** `NOT EXECUTED`.

## VAL-MODEL-002 — Real CLIP image-embedding smoke

- **Capability:** the configured CLIP model produces finite, correctly-dimensioned image vectors.
- **Current evidence level:** `PENDING` (adapter contract `REPO_VERIFIED`).
- **Reason deferred:** model weights are not in the repository.
- **Required environment / data:** the configured CLIP weights and a sample image.
- **Exact procedure:** run the CLIP smoke harness against the configured weights.
- **Expected artifact:** a recorded pass with the vector dimension and a query round-trip.
- **Acceptance criteria:** dimension/finiteness/non-zero-norm checks pass.
- **Failure interpretation:** missing asset means not executed.
- **Evidence promotion rule:** `PENDING` → `LOCAL_REAL_VALIDATION` only with a recorded real-weights pass.
- **Related issue:** #8. **Status:** `NOT EXECUTED`.

## VAL-MODEL-003 — Real PaddleOCR runtime smoke

- **Capability:** PaddleOCR extracts text from a real image through the provider interface.
- **Current evidence level:** `PENDING`.
- **Reason deferred:** the OCR runtime/dependencies are not installed here.
- **Required environment / data:** PaddleOCR installed; a real image with known text.
- **Exact procedure:** run `PaddleOCRProvider` against the sample image and assert the expected text.
- **Expected artifact:** a recorded extraction result.
- **Acceptance criteria:** expected tokens are present in the extracted blocks.
- **Failure interpretation:** an import error means not executed.
- **Evidence promotion rule:** `PENDING` → `LOCAL_REAL_VALIDATION` only with the recorded extraction.
- **Related issue:** #8. **Status:** `NOT EXECUTED`.

## VAL-SCHED-001 — Real Airflow DAG execution

- **Capability:** the knowledge-base DAGs run on a real Airflow scheduler.
- **Current evidence level:** `PENDING`.
- **Reason deferred:** no Airflow runtime here.
- **Required environment / data:** an Airflow deployment able to import `dags/knowledge_base_dags.py`.
- **Exact procedure:** register the DAGs, trigger a run, and record the task outcomes.
- **Expected artifact:** an Airflow run record / task log.
- **Acceptance criteria:** the DAG completes and produces the expected offline outputs.
- **Failure interpretation:** DAG registration alone is not execution.
- **Evidence promotion rule:** `PENDING` → `LOCAL_REAL_VALIDATION` only with the run record.
- **Related issue:** #8. **Status:** `NOT EXECUTED`.

## VAL-GPU-001 — Real 4B / 14B vLLM GPU topology

- **Capability:** the configured shared-4B + 14B vLLM topology serves rewrite and generation on real GPUs.
- **Current evidence level:** `PENDING` (routing contract `REPO_VERIFIED`).
- **Reason deferred:** model weights are absent and `vllm` is not installed.
- **Required environment / data:** GPUs, the 4B and 14B weights, a vLLM deployment matching `config.json`'s endpoints.
- **Exact procedure:** start both endpoints, run the documented routing smoke, and record latency and routing decisions.
- **Expected artifact:** a topology record with endpoint URLs, model names and a request/response trace.
- **Acceptance criteria:** rewrite and simple generation hit `gen_4b`; complex generation hits `gen_14b`; responses are non-empty.
- **Failure interpretation:** a running container without a served request is not evidence.
- **Evidence promotion rule:** `PENDING` → `LOCAL_REAL_VALIDATION` only with the recorded request trace.
- **Related issue:** #12. **Status:** `NOT EXECUTED`.

## VAL-RAGAS-001 — Real RAGAS quality evaluation

- **Capability:** a real RAGAS quality score over the golden set.
- **Current evidence level:** `PENDING` (harness `REPO_VERIFIED`).
- **Reason deferred:** no approved evaluator provider or credential.
- **Required environment / data:** an approved evaluator dependency and credential; `tests/evaluation/golden_set.jsonl`; the live pipeline dependencies (vLLM/Qdrant/ES/Redis) for a real pipeline run.
- **Exact procedure:** run `python -m tests.evaluation.ragas_eval --require-ragas --pipeline ...` with a configured provider and the live pipeline. `--require-ragas` alone evaluates the dataset reference answers (an evaluator smoke) and must not be accepted as a pipeline-quality result.
- **Expected artifact:** a pipeline-quality report with pipeline provenance, bound to the git SHA and the golden-set hash.
- **Acceptance criteria:** the `--pipeline` mode runs against the live pipeline and produces a report (not the evaluator-unavailable fallback) with the configured evaluator.
- **Failure interpretation:** a missing dependency or credential fails fast with no report; that is not a score of zero.
- **Evidence promotion rule:** `PENDING` → `LOCAL_REAL_VALIDATION` only with a real report; never claim a score without it.
- **Related issue:** #12. **Status:** `NOT EXECUTED`.

## VAL-K8S-001 — Real Kubernetes deployment + readiness admission

- **Capability:** the `deploy/k8s/` manifests roll out and readiness admits/refuses traffic by dependency state.
- **Current evidence level:** `PENDING` (manifests + static checks `REPO_VERIFIED`).
- **Reason deferred:** no real cluster or reachable dependencies.
- **Required environment / data:** a Kubernetes cluster; reachable Redis, Qdrant, Elasticsearch, MinIO and model endpoints; the built image.
- **Exact procedure:** apply the manifests, capture rollout and Pod readiness, then exercise the degraded cases in issue #54.
- **Expected artifact:** rollout status, Pod readiness and the dependency-outage observations.
- **Acceptance criteria:** readiness reflects dependency state (one retrieval backend down remains degradable; both down fails readiness; a required generation endpoint down fails readiness).
- **Failure interpretation:** static manifest validation is not a cluster run.
- **Evidence promotion rule:** `PENDING` → `LOCAL_REAL_VALIDATION` only with a captured cluster run.
- **Related issue:** #54. **Status:** `NOT EXECUTED`.

## VAL-E2E-001 — Browser → real RAG backend end-to-end smoke

- **Capability:** the real React client drives the real FastAPI monolith end to end.
- **Current evidence level:** `PENDING`.
- **Reason deferred:** the committed demo drives the UI against the synthetic `docs/demo/mock_api.py`, not the real backend.
- **Required environment / data:** React frontend, canonical FastAPI monolith, real Redis/Elasticsearch/Qdrant and model endpoints; test identities with distinct RBAC scopes.
- **Exact procedure:** drive the real frontend with Playwright against the real backend; verify login metadata, one allowed query with citations, one cross-role denied/refused query, session continuity and one degraded path.
- **Expected artifact:** a reproducible browser/API capture tied to a git SHA, with commands and observed evidence.
- **Acceptance criteria:** the browser actually talks to the canonical backend and the RBAC outcomes match the test identities.
- **Failure interpretation:** a synthetic mock API capture is not this validation.
- **Evidence promotion rule:** `PENDING` → `LOCAL_REAL_VALIDATION` only with the committed real-backend capture.
- **Related issue:** #60. **Status:** `NOT EXECUTED`.
