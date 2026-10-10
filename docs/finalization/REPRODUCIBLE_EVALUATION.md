# Reproducible evaluation

How a third party with the right assets reproduces the retrieval evaluation —
and, just as important, how the repository behaves when those assets are absent
(it reports `BLOCKED`, it does not invent numbers).

Commit: `61be8a9` · written 2026-10-10 UTC.

## 1. What is required (and what is missing here)

| requirement | present on the audit host? | consequence |
|---|---|---|
| An independent corpus (Elasticsearch / Qdrant) whose chunks address the golden passages | **No** | Every retrieval config reports `BLOCKED` |
| CrossEncoder weights at `./models/cross-encoder-law` and `./models/cross-encoder-base` | **No** | Evidence Gate is fail-closed; rerank validation is `PENDING` |
| The sealed epoch's `corpus_sha256` | **No** | No annotation id can resolve |
| A human-reviewed golden set | **No** (`annotations_v1.jsonl` is empty) | No official metric is scorable |
| GPU topology matching the configured production layout | **No** (1× RTX 5060 Ti, not 2× A5000) | Topology gate is `PENDING` |

None of the missing items may be substituted. In particular: **do not index the
golden passages themselves and then measure recall** — that scores `1.0` by
construction and is refused in code (`benchmarks/backends.py`, the corpus probe is
read-only).

## 2. Data preparation

### 2.1 The committed golden set (source, not scorable)

`tests/evaluation/golden_set.jsonl` — 301 rows, fields `question / answer /
contexts / ground_truth / business_type / difficulty`. It has **no** stable
identity and **no** review state, so `--require-contract` refuses it. It is kept
unchanged as the historical set.

### 2.2 The v2 annotation workspace

`tests/evaluation/golden_set_v2/`:

| file | role |
|---|---|
| `annotations.schema.json` | JSON Schema for one record |
| `annotations_v1.jsonl` | the annotation dataset — **empty until a human fills it** |
| `annotation_workpackage_v1.jsonl` | 70-row worksheet of *candidates* (see below) |
| `ANNOTATION_WORKPACKAGE.md` | reviewer procedure and category table |
| `HUMAN_ANNOTATION_BACKLOG.md` | the outstanding decisions, regenerated on demand |

### 2.3 The annotation work package

`scripts/validation/build_annotation_workpackage.py` selects a
category-balanced subset and writes a worksheet. Its hard guarantees:

- every row is `source: llm_candidate`, `review_status: DRAFT_UNVERIFIED`,
  `scorable: false`;
- it names **no** annotator, **no** reviewer, **no** date;
- it writes **no** `doc_id`, `chunk_id`, `corpus_version`, `corpus_sha256`,
  `visual_required` or `complexity_label` — those are listed in `to_fill`.

Categories and default quota (70 rows):

| category | rows | source |
|---|---|---|
| `regulation_precise` | 15 | `business_type == regulation` |
| `ingredient_query` | 15 | `business_type == ingredient` |
| `product_knowledge` | 4 | `business_type == product` (all that exist) |
| `semantic_paraphrase` | 12 | questions with a paraphrase prefix |
| `multi_document` | 12 | ≥3 candidate passages |
| `image_related` | 12 | `business_type == image` |
| `no_answer_or_conflict` | **0** | **the source set has none — human authorship required** |

The missing `no_answer_or_conflict` samples are the important finding: the
committed set contains no unanswerable or conflicting-evidence question, so a
benchmark built only from it cannot show that the system declines when it should.

Rebuild it:

```bash
python3 scripts/validation/build_annotation_workpackage.py \
  --dataset tests/evaluation/golden_set.jsonl \
  --out tests/evaluation/golden_set_v2/annotation_workpackage_v1.jsonl \
  --markdown tests/evaluation/golden_set_v2/ANNOTATION_WORKPACKAGE.md
```

## 3. Annotation specification

A record becomes **scorable** only when all of the following hold. The gate is
`benchmarks/golden_set_contract.require_attributable` +
`benchmarks/annotation.validate_annotation`.

1. **Identity.** Every supporting passage has its own `doc_id` **and**
   `chunk_id`. The evaluation key is `doc_id::chunk_id`, so two chunks of one
   document stay distinct. A row-level id shared by several passages is refused
   (`AmbiguousRelevanceIdentityError`) — the id cannot say which passage it means.
2. **Multi-passage truth.** *All* supporting passages are kept. Collapsing to the
   first would inflate Recall@5 to a guaranteed `1.0`.
3. **Provenance.** `annotation = {annotator, method, annotated_at, source,
   review_status}`. `review_status` has no default.
4. **Review.** `review_status: REVIEWED` requires `reviewed_by` (≠ `annotator`)
   and `reviewed_at`. An `llm_candidate` also requires `promoted_by`.
5. **Corpus binding.** `corpus_version` (the sealed epoch) and `corpus_sha256`
   (the fingerprint the mapping was made against). A dataset mapped to two
   different corpus states is refused.
6. **Visual / complexity are human decisions.** Never derived from
   `business_type`; never a Router prediction.

Lifecycle:

```text
candidate (llm)  ──►  DRAFT_UNVERIFIED  ──►  REVIEWED (named reviewer, date, promoted_by)
                        never scorable              the only scorable state
```

Promotion is explicit and auditable:

```python
from benchmarks.annotation import promote_to_reviewed

promoted = promote_to_reviewed(record, reviewer="<name>", reviewed_at="2026-10-11", promoted_by="<name>")
```

## 4. Model preparation

- Place CrossEncoder weights where `config.json → gpu1.models.cross_encoder_a/b
  → model_path` points. `retrieval.rerank_status` requires a real weight file
  (F3) before reporting `AVAILABLE`.
- Optionally, a **public general-purpose reranker** may be substituted for a
  scoped experiment. It must be labelled as such and must **never** be described
  as the enterprise regulation CrossEncoder. No such substitution is committed.
- Downloading weights, consuming an external API, or a long GPU job requires
  explicit authorisation. Without it, only the verification entry points and the
  blocking report are produced.

## 5. Commands

### 5.1 Always runnable (no external assets)

```bash
python -m pytest tests/ -q
ruff check .
ruff format --check .
python scripts/check_repo_consistency.py
python -m benchmarks.retrieval_benchmark --list-configs
python -m retrieval.rerank_status
python scripts/validation/audit_annotations.py \
  --dataset tests/evaluation/golden_set_v2/annotations_v1.jsonl --allow-empty
```

### 5.2 Requires an independent corpus and weights

```bash
# 1. confirm availability (expect BLOCKED without assets)
python -m benchmarks.retrieval_benchmark --list-configs

# 2. run one configuration
python -m benchmarks.retrieval_benchmark --config bm25 --limit 50

# 3. the four experiment groups, sharing one dataset and one top_k
python -m benchmarks.retrieval_benchmark --config bm25
python -m benchmarks.retrieval_benchmark --config dense
python -m benchmarks.retrieval_benchmark --config hybrid_rrf
python -m benchmarks.retrieval_benchmark --config hybrid_rrf_biencoder_crossencoder

# 4. real CrossEncoder rerank validation (only meaningful with weights)
python -m retrieval.rerank_validation --smoke --require-real
python -m retrieval.rerank_validation --compare
```

## 6. Artifact formats

- **Benchmark run** → `artifacts/benchmarks/<run_id>/`: `metadata.json`,
  `summary.json`, `per_query_results.jsonl`, `environment.txt`. `metadata.json`
  carries `git_sha`, `dataset_sha256`, `sample_ids_hash`, `effective_config`,
  `dataset_contract`, `attributable`, `synthetic_retriever`,
  `results_are_benchmark`, and the `corpus` block (fingerprints + correspondence).
  **`results_are_benchmark` is `true` only when results exist, the dataset is
  attributable, and no synthetic retriever was injected.**
- **Rerank validation** → `artifacts/rerank/`: `status`, `provenance`
  (`cross_encoder` / `deterministic_fallback`), `is_reranking_evidence`,
  `provenance_source`, `model_revision`, `degradations`, `comparison`. Only
  `is_reranking_evidence == true` may be quoted.
- **Corpus fingerprint** → embedded in benchmark `metadata.json → corpus`.

## 7. The four experiment groups

All four are **BLOCKED on this host**. They must share the same queries, the same
corpus version and the same valid Gold; a non-executable one stays `BLOCKED` —
never filled with zeros or a fabricated gain.

| experiment | config | state |
|---|---|---|
| BM25 only | `bm25` | **BLOCKED_EXTERNAL** (ES unreachable) |
| Dense only | `dense` | **BLOCKED_EXTERNAL** (Qdrant unreachable) |
| Hybrid RRF | `hybrid_rrf` | **BLOCKED_EXTERNAL** |
| Hybrid + real CrossEncoder | `hybrid_rrf_biencoder_crossencoder` | **BLOCKED_EXTERNAL** (also no weights) |

`--list-configs` output is the evidence; it is reproduced in the acceptance matrix.
