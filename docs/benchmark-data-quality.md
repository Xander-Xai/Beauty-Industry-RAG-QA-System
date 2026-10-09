# Benchmark data quality

This document records the data-quality gaps that limit the current retrieval
benchmark, measured against `tests/evaluation/golden_set.jsonl` at
`main` @ `aa189d9`.

Nothing here is inferred. Every count below is the measured field coverage of
the dataset as committed.

## Measured coverage

| field | coverage | usable for breakdown |
|---|---|---|
| `question` | 301 / 301 | query input |
| `answer` | 301 / 301 | reference answer |
| `ground_truth` | 301 / 301 | reference text (not used as an identifier) |
| `contexts` | 301 / 301 | ground-truth passages |
| `business_type` | 301 / 301 | yes |
| `difficulty` | 301 / 301 | yes |
| `visual_required` | 0 / 301 | **no** |
| `complexity` (or any stored simplicity label) | 0 / 301 | **no** |
| `doc_id` / `chunk_id` / `source_id` | 0 / 301 | **no** |

`business_type` distribution: regulation 124, ingredient 91, formula 46,
general 20, image 16, product 4.
`difficulty` distribution: medium 135, easy 102, hard 64.

Ground truth per query: 4 passages for 228 queries, 3 for 48, 1 for 25.
Across 301 queries there are 1081 ground-truth passages but only 262 distinct
passages, so passages are shared between queries.

## `visual_required`

Missing for all 301 samples, so the benchmark does not produce a
visual / non-visual breakdown.

A future definition should be based on whether the answer is obtainable from
text alone:

> `visual_required = true` only when the question cannot be answered from text
> alone and requires reading an image, label, diagram or scanned visual page.

It must be a human/reference label applied to the dataset. It must not be
derived from the `image` business type, because `business_type = image` marks a
question *domain*, not whether the answer needs visual content.

## `complexity_label`

There is no stored complexity label. The runtime Router predicts complexity
online, but a Router prediction must not be recorded as ground truth: measuring a
classifier against its own output is circular.

Proposed future schema:

```text
complexity_label    = human / reference label (ground truth)
router_prediction   = runtime Router output
router_confidence   = runtime Router confidence
```

With both fields present, Router accuracy becomes measurable as a separate
metric rather than being assumed.

## Consequences for benchmark v1

The v1 breakdown therefore supports only:

```text
overall
business_type
difficulty
```

`visual_required` and `complexity` breakdowns are absent, not zero and not
guessed. The harness reports this in every artifact via `unavailable_buckets`,
so an absent breakdown can never be mistaken for a measured one.

## Not modified in this PR

Neither field was added to the dataset in this change. Adding labels without a
reviewed annotation pass would create the appearance of bucket coverage without
real ground truth, so label creation is left to a separate, explicit data task.

## v2 contract: enforced, not just documented

The gaps above are now a machine-checked contract rather than prose.
`benchmarks/golden_set_contract.py` (`golden-set-contract/v2`) requires every row
to carry:

```text
sample_id
question
business_type      (enum)
difficulty         (enum)
visual_required    (bool, human decision — never derived)
complexity_label   ("simple" | "complex", human decision)
corpus_version     (the sealed epoch the identifiers resolve in)
annotations        [{doc_id, chunk_id, text}, ...]
annotation         {annotator, method, annotated_at, reviewed_by}
```

A row that cannot satisfy a clause is `INVALID` with a machine-readable reason;
nothing is guessed. Identifier resolution (`qdrant_resolver` /
`elasticsearch_resolver`) is existence-only: it answers "does this
`(doc_id, chunk_id)` exist in `corpus_version`", never "is it relevant", so a
retriever's own output can never become ground truth.

Measured on the committed dataset: **0 / 301 rows are contract-valid** (no
`sample_id`, no `annotations`, no `corpus_version`, no `visual_required`, no
`complexity_label`, no provenance). The evaluation pipeline therefore refuses to
score it:

```text
python3 scripts/validation/validate_golden_set_contract.py --dataset tests/evaluation/golden_set.jsonl
# -> 301/301 INVALID, exit 1
```

### Human review flow for the two missing labels

1. An annotator reads the question and decides `visual_required` by the rule in
   the section above and `complexity_label` by whether a single retrieval hop is
   sufficient.
2. The annotator records `annotation.annotator`, `method` and `annotated_at`, and
   maps each supporting passage to a real `(doc_id, chunk_id)` in the sealed
   `corpus_version`.
3. A second reviewer sets `annotation.reviewed_by`. Only reviewed rows can be
   valid, so an unreviewed label cannot enter a scored run.

Full per-metric readiness (what is `VERIFIED`, `BLOCKED`, and why) is recorded in
[RAG evaluation readiness](validation/rag-eval-readiness.md).