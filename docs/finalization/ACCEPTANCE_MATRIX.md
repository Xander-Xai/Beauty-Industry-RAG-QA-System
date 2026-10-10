# Acceptance matrix

Every item lists the evidence path, the exact command run, and the observed
result. Levels: **PASS_CODE**, **PASS_LOCAL_REAL**, **PASS_CI**,
**BLOCKED_EXTERNAL**, **NEEDS_HUMAN**, **NOT_VERIFIED**.

- Commit: `61be8a9` on branch `finalize/engineering-closeout`
- Local run (UTC): 2026-10-10, Linux 6.6 WSL2, 20 vCPU, 15 GiB RAM, Python 3.10.12, 1× RTX 5060 Ti
- CI: the checks listed below run in `.github/workflows/{ci,lint,security}.yml`; PR head status is recorded in the PR conversation (this document is committed before the CI run for this SHA completes — treat the CI column as "job exists and runs this command", not "green"). **Do not read PASS_CI here as a claim about a specific run.**

## 1. Engineering checks (run locally, all green)

| # | command | result | level |
|---|---|---|---|
| E1 | `python -m pytest tests/ -q` | `2896 passed, 17 skipped` in ~146 s | PASS_CODE |
| E2 | `ruff check .` | `All checks passed!` | PASS_CODE |
| E3 | `ruff format --check .` | `331 files already formatted` | PASS_CODE |
| E4 | `python scripts/check_repo_consistency.py` | `Repository consistency check passed.` | PASS_CODE |
| E5 | `python -m benchmarks.retrieval_benchmark --list-configs` | 5 configs, all `BLOCKED` (exit 0) | BLOCKED_EXTERNAL |
| E6 | `python -m retrieval.rerank_status` | `BLOCKED` (CE-A/CE-B paths absent) | BLOCKED_EXTERNAL |
| E7 | `python scripts/validation/audit_annotations.py --dataset tests/evaluation/golden_set_v2/annotations_v1.jsonl --allow-empty` | empty dataset reported, `scorable: 0` (exit 0) | NEEDS_HUMAN |

New tests added by this closeout: `tests/test_engineering_closeout_regressions.py`
(17 tests re-running every fix, with positive controls) and
`tests/test_annotation_workpackage.py` (8 tests).

## 2. Fix-by-fix verification

| finding | evidence path | command | result | level |
|---|---|---|---|---|
| F1 load failure published as real rerank | `tests/test_engineering_closeout_regressions.py::test_F1_*` | `pytest tests/test_engineering_closeout_regressions.py -q` | pass | PASS_CODE |
| F2 `.mean()` collapse made ranking a no-op | `...::test_F2_*` | same | pass | PASS_CODE |
| F3 `config.json`-only dir reported available | `...::test_F3_*` | same | pass | PASS_CODE |
| F4 cross-principal session leak | `...::test_F4_*` | same | pass | PASS_CODE |
| F5 P2 overload 503 became 500 | `...::test_F5_*` | same | pass | PASS_CODE |
| F6 run report mislabeled cache/admission/error | `...::test_F6_*` | same | pass | PASS_CODE |
| F7 runtime rerank provenance override | `...::test_F7_*` | same | pass | PASS_CODE |

## 3. Retrieval benchmark data integrity (area A)

The seven integrity requirements are met by existing code plus the closeout tests;
no re-implementation was needed.

| requirement | evidence | level |
|---|---|---|
| Multi-passage ground truth not collapsed | `benchmarks/relevance.py::relevant_items_from_annotations`, `tests/benchmark/test_review_regressions.py` | PASS_CODE |
| `doc_id`/`chunk_id` chunk-level identity | `benchmarks/relevance.py::relevance_key` | PASS_CODE |
| Mock/scripted ranking never published | `results_are_benchmark`, `tests/benchmark/test_publication_gate.py` | PASS_CODE |
| Unreviewed labels cannot be scored | `benchmarks/annotation.py`, `tests/evaluation/test_annotation_lifecycle.py` | PASS_CODE |
| Corpus hash / id consistency | `benchmarks/corpus.py`, resolvers, `tests/benchmark/*` | PASS_CODE |
| Self-referential corpus refused | `benchmarks/backends.py` (read-only probe) | PASS_CODE |
| 301-row set not auto-promoted | `annotations_v1.jsonl` empty, contract v2 | PASS_CODE |

## 4. The four retrieval experiments — all BLOCKED

`python -m benchmarks.retrieval_benchmark --list-configs` output (verbatim,
abridged):

```text
  bm25                              BLOCKED  Elasticsearch BM25 only
                                            reasons: bm25=service_unreachable; corpus=corpus_does_not_contain_ground_truth
  dense                             BLOCKED  Qdrant dense (BGE) only
                                            reasons: dense=service_unreachable; corpus=corpus_does_not_contain_ground_truth
  hybrid_rrf                        BLOCKED  BM25 + dense fused with Reciprocal Rank Fusion
                                            reasons: bm25=service_unreachable; dense=service_unreachable; corpus=...
  hybrid_rrf_biencoder_crossencoder BLOCKED  hybrid_rrf_biencoder plus CrossEncoder ensemble rerank
                                            reasons: bm25=...; dense=...; biencoder=model_assets_unavailable; crossencoder=model_assets_unavailable; corpus=...
```

| experiment | level | reason |
|---|---|---|
| BM25 only | BLOCKED_EXTERNAL | Elasticsearch unreachable; no corpus correspondence |
| Dense only | BLOCKED_EXTERNAL | Qdrant unreachable |
| Hybrid RRF | BLOCKED_EXTERNAL | both stores unreachable |
| Hybrid + real CrossEncoder | BLOCKED_EXTERNAL | stores unreachable **and** no CE weights |

**No metric value is produced for any of these. This is correct.**

## 5. Human annotation (area B)

| item | evidence | level |
|---|---|---|
| 70-row category-balanced worksheet, no fabricated review state | `tests/evaluation/golden_set_v2/annotation_workpackage_v1.jsonl` | NEEDS_HUMAN |
| Reviewer procedure + blocking conditions | `tests/evaluation/golden_set_v2/ANNOTATION_WORKPACKAGE.md` | NEEDS_HUMAN |
| `no_answer_or_conflict` category has no source samples | work package report (`0` rows) | NEEDS_HUMAN (authoring required) |
| Independent corpus for id mapping | — | BLOCKED_EXTERNAL |
| Human-reviewed records | `annotations_v1.jsonl` (empty) | NEEDS_HUMAN |

## 6. External / human dependencies (what still must be supplied)

| dependency | blocks | level |
|---|---|---|
| Independent corpus (ES + Qdrant) + sealed epoch hash | all retrieval metrics, all annotation id mapping | BLOCKED_EXTERNAL |
| CrossEncoder weights (`cross-encoder-law`, `cross-encoder-base`) | real rerank validation, Evidence Gate "normal" mode | BLOCKED_EXTERNAL |
| Human annotator(s) + reviewer for the golden set | scorable gold, official metrics | NEEDS_HUMAN |
| Dual-A5000 (or matching) host | topology gate `SATISFIED` | BLOCKED_EXTERNAL |
| Authorization for weight download / external API / long GPU job | any real model execution | NEEDS_HUMAN |

## 7. Verdict

| dimension | verdict |
|---|---|
| Code correctness & tests | **PASS_CODE** (2896 passed, 17 skipped, ruff + consistency clean) |
| Real-dependency retrieval/gate/RBAC | **PASS_LOCAL_REAL** for in-memory/simulated stores only; real ES/Qdrant are BLOCKED_EXTERNAL |
| CI | jobs defined and run the same commands; see the PR check summary for the live status |
| Retrieval metrics | **NOT_MEASURABLE** |
| Model execution | **BLOCKED_EXTERNAL** |
| Human labeling | **NEEDS_HUMAN** |
| Dual-GPU production topology | **NOT_VERIFIED** |

**Project state: ENGINEERING_READY. METRICS_VERIFIED: no — and it must not be
marked so until the assets in §6 are supplied.**
