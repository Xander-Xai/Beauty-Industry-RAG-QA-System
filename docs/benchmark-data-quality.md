# Benchmark data quality

> **Scope.** Every count in this document is the measured field coverage of the
> **legacy 301-row golden set** (`tests/evaluation/golden_set.jsonl`). It is
> not a statement about every golden set in the repository. The Real Corpus V1
> set (27 human-signed records) is a different scope with different coverage —
> it carries stable `(doc_id, chunk_id)` identity, `visual_required` and
> `complexity_label`, and measured 27/27 contract-valid — and has produced a
> formal BM25 result; see
> [`docs/validation/real-v1-approved-bm25-run.md`](validation/real-v1-approved-bm25-run.md).
> The gap analysis below still stands in full for the 301-row set.

This document records the data-quality gaps that limit the current retrieval
benchmark, measured against `tests/evaluation/golden_set.jsonl` at
`main` @ `b711d99` (re-measured; the coverage below is unchanged from the earlier
`aa189d9` anchor).

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

Adding **one** identifier per row is not enough and is not accepted. A row with a
single flat identifier and several ground-truth passages cannot say which passage
it names; the loader raises `AmbiguousRelevanceIdentityError` instead of scoring
it against a subset (see [Human review flow](#human-review-flow-for-the-two-missing-labels)).

`business_type` distribution: regulation 124, ingredient 91, formula 46,
general 20, image 16, product 4.
`difficulty` distribution: medium 135, easy 102, hard 64.

Ground truth per query: 4 passages for 228 queries, 3 for 48, 1 for 25.
Across 301 queries there are 1081 ground-truth passages but only 262 distinct
passages, so passages are shared between queries. **All 1081 are scored.** An
earlier loader kept only `contexts[0]` whenever a stable identifier was present,
which scored a four-passage query against one passage and made Recall@5 read
`found / 1` instead of `found / 4`.

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
corpus_sha256      (fingerprint of that corpus — binds the ids to one index state)
annotations        [{doc_id, chunk_id, text, relevance}, ...]
annotation         {annotator, method, annotated_at, source, review_status, reviewed_by, reviewed_at}
```

`relevance` is optional and defaults to `2` (highly relevant); the scale is
`0` not relevant / `1` partially relevant / `2` highly relevant. Binary
Recall/Hit/MRR treat `>= 1` as relevant; NDCG uses the whole scale.

`review_status` is required and has **no default**. A row whose review status is
not `REVIEWED` is `INVALID` with the reason `annotation_not_reviewed:<status>`,
so an unreviewed label — including one an LLM proposed — cannot enter a scored
run. `source` is `human` or `llm_candidate`; promoting a model candidate to
`REVIEWED` additionally requires `promoted_by`.

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
2. The annotator records `annotation.annotator`, `method`, `annotated_at`,
   `source`, and maps **each** supporting passage to its own real
   `(doc_id, chunk_id)` in the sealed `corpus_version`, plus that corpus's
   `corpus_sha256`.
3. A second reviewer sets `annotation.review_status = REVIEWED`,
   `reviewed_by` and `reviewed_at`. `reviewed_by` must differ from `annotator`.
   Only reviewed rows can be valid, so an unreviewed label cannot enter a scored
   run.

Every passage needs its own identifier. A single `doc_id` for a row with several
ground-truth passages is ambiguous and the loader refuses it
(`AmbiguousRelevanceIdentityError`) rather than scoring it against one passage.

The v2 annotation workspace — schema, audit tool and the outstanding-work list —
is [`tests/evaluation/golden_set_v2/`](../tests/evaluation/golden_set_v2/README.md).
Its `annotations_v1.jsonl` is committed **empty**: the annotation pass has not
been performed, and no record was synthesised to make it look otherwise. Audit it
with:

```text
python3 scripts/validation/audit_annotations.py \
  --dataset tests/evaluation/golden_set_v2/annotations_v1.jsonl --allow-empty
```

Full per-metric readiness (what is `VERIFIED`, `BLOCKED`, and why) is recorded in
[RAG evaluation readiness](validation/rag-eval-readiness.md).

## Data requirement and blocker evidence (issue #86)

The identity gap is not an oversight that can be patched by filling in blank fields; the
identifiers have no real source to be recovered from. Traced to origin:

- Every passage in `golden_set.jsonl` and its variants is a **synthetic fixture**. The
  passage texts appear only inside `tests/evaluation/golden_set.jsonl`,
  `golden_set_expanded.jsonl`, `sample_golden_set.jsonl` and the v2 workpackage — they do
  not occur as a document in any source corpus. (See `tests/evaluation/DATA_PROVENANCE.md`:
  the regulation names, limits and registry formats are fabricated placeholders.)
- There is no built corpus in the working tree: no `corpus/` directory and no sealed index.
- The v2 identifier resolvers (`qdrant_resolver` / `elasticsearch_resolver`) are
  existence-only, but they require a **live** Elasticsearch (`:9200`) and Qdrant (`:6333`).
  Neither is reachable here, so no `(doc_id, chunk_id)` can be resolved even if one were
  written.

Therefore `doc_id` / `chunk_id` / `source_id` **cannot be restored from a real source and
must not be invented**. Writing plausible-looking identifiers that no index resolves is the
exact failure `require_attributable()` exists to catch.

What the data task requires, and why each item is external:

1. **An authorised corpus.** Licensed or otherwise cleared source documents. The fixtures
   cannot substitute; `offline/source_trust.py` gates unlicensed material as `UNTRUSTED`.
2. **A sealed epoch.** `corpus_version` plus its `corpus_sha256`, so the identifiers bind
   to one immutable index state — otherwise a rebuild silently changes what a `doc_id`
   means.
3. **Per-passage resolution against the built index** (existence only, never relevance):
   each of the 1081 ground-truth passages needs its own real `(doc_id, chunk_id)`. This
   crosses into the shared prerequisite stack of issue #18 (running Elasticsearch + Qdrant
   + the offline pipeline).
4. **Human annotation and review.** `visual_required`, `complexity_label`, the annotator,
   and a distinct reviewer with an ISO date. No tool may substitute for this.

Until all four exist, issues #86 (dataset identity/labels) and #18 (real benchmark
execution) stay **OPEN/BLOCKED**. Field/format compliance is **not** relevance-label
validation: the format validator now prints this distinction itself
(`python -m tests.evaluation.validate_golden_set --dataset tests/evaluation/golden_set.jsonl`),
and `--require-attributable` turns the 0/301 state into a non-zero exit for a real gate.

## Unattributable samples are excluded, not down-graded (issue #86)

The v2 contract is a per-row verdict; a dataset is a mixture of valid and invalid
rows until an annotation pass completes. Two problems follow from that mixture,
and both are now handled explicitly rather than by the level-2 fallback:

* **Exclusion instead of silent down-grading.** `--drop-unattributable` on
  `python -m benchmarks.retrieval_benchmark` removes every sample that fails the
  contract and records the count, the excluded sample ids and their
  machine-readable reasons in the artifact (metadata field
  `unattributable_filter`). A sample is therefore never scored by normalized-text
  matching merely because its identifier is missing. If the filter leaves no
  sample, the run refuses with exit code 2 and writes no artifact — an empty
  dataset is not a successful run. The derived `sample_id` (the line index) is
  preserved on kept rows, so a filtered run is directly comparable to an
  unfiltered one.
* **Cross-row integrity.** `dataset_integrity_errors()` (in
  `benchmarks/golden_set_contract.py`) checks the invariants a per-row validator
  cannot see: a `sample_id` used by two rows, and one row that annotates the same
  passage twice. Either silently corrupts a metric — a duplicate id makes two
  queries share a result slot, and a duplicated annotation changes the Recall@K
  denominator. `--require-integrity` turns a non-empty result into exit code 2.
  A dataset without `sample_id` (the committed one) has nothing to compare and is
  unaffected.

Neither flag changes the default behaviour: without them the CLI behaves exactly
as before, and `metadata.dataset_integrity_errors` is still recorded so a reader
can see what was found even when the gate flag is off. The committed golden set
is still 0/301 attributable, so `--drop-unattributable` refuses it (exit 2) rather
than scoring it.