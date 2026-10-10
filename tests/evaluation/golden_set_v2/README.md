# Golden set v2 — annotation workspace

This directory holds the **second version** of the retrieval golden set. The 301
committed Q&A pairs in `tests/evaluation/golden_set.jsonl` are retained unchanged
and remain the historical set; nothing in this directory modifies, replaces or
claims to have reviewed them.

## Current state — measured, not estimated

| file | rows | meaning |
|---|---|---|
| `annotations_v1.jsonl` | **0** | the annotation pass **has not been performed** |
| `annotations.schema.json` | — | JSON Schema for a record |
| `HUMAN_ANNOTATION_BACKLOG.md` | 301 outstanding | generated list of decisions still required |

`annotations_v1.jsonl` is committed **empty on purpose**. Filling it requires a
human who reads each question and decides what supports it. No record was
synthesised, inferred from the question text, or copied from a model proposal,
because an unreviewed label presented as ground truth is worse than a missing
one: it produces a retrieval score that looks real and is not.

Verify the current state at any time:

```bash
python3 scripts/validation/audit_annotations.py \
  --dataset tests/evaluation/golden_set_v2/annotations_v1.jsonl --allow-empty
```

Exit code `0` means "structurally fine"; `scorable: 0` is the number that
matters, and it stays `0` until a person reviews the records.

## Why a new version is needed

The committed golden set has no stable document identity. Measured over the
committed file: `doc_id` / `chunk_id` / `source_id` are present on **0 / 301**
rows, so relevance matching falls back to normalized exact text. It also has
`visual_required` on 0 / 301 rows and no stored complexity label at all.

Its 301 questions carry 1081 ground-truth passages spanning only 262 distinct
ones. Mapping each of those passages to a real indexed chunk is the work; doing
it by eye is what this directory is for.

## Record shape

See `annotations.schema.json`. The fields that are easy to get wrong:

- **`annotations[]`** — every supporting passage, not just the best one. Each
  entry carries its own `doc_id` + `chunk_id`. The evaluation key is
  `doc_id::chunk_id`, so two chunks of one document stay distinguishable; a
  single identifier for a document with several chunks is not enough.
- **`relevance`** — `2` highly relevant, `1` partially relevant, `0` considered
  and rejected (excluded from scoring). Defaults to `2`, so leaving it out is
  safe but says less.
- **`visual_required`** — `true` **only** when the question cannot be answered
  from text alone and needs an image, label, diagram or scanned page. Do not
  derive it from `business_type == "image"`: that field marks a domain, not a
  visual dependency.
- **`complexity_label`** — whether a single retrieval hop suffices. Do not paste a
  Router prediction here; measuring a classifier against its own output is
  circular. Store Router output in a separate field.
- **`corpus_version` + `corpus_sha256`** — the sealed epoch and its fingerprint.
  Get the hash from the artifact the corpus probe writes:
  `metadata.json → corpus.fingerprints[].content_sha256`. Without it a chunk id
  could silently refer to a different index revision later.

## Review lifecycle — the part that cannot be skipped

```text
DRAFT_UNVERIFIED   a proposal; nobody has checked it. NEVER scorable.
REVIEWED           a named person accepted it. Only this may be scored.
```

`review_status` is required and has **no default**. A record that does not state
its review state is rejected — an LLM proposal and a reviewed label are
otherwise shaped identically.

To record a model proposal: set `source: "llm_candidate"` and
`review_status: "DRAFT_UNVERIFIED"`. It will be audited, reported, and refused by
the scoring gate. That is the intended behaviour, not an error to work around.

Promotion is explicit and leaves an audit trail:

```bash
python3 - <<'PY'
from benchmarks.annotation import load_records, promote_to_reviewed
record = load_records("tests/evaluation/golden_set_v2/annotations_v1.jsonl")[0]
promoted = promote_to_reviewed(
    record,
    reviewer="<the person who checked it>",
    reviewed_at="2026-10-10",
    promoted_by="<the person who checked it>",
)
PY
```

It refuses to invent a reviewer, refuses a non-ISO date, and refuses a record
with no named `annotator` — promoting an unattributed record would file an
unknown origin under a real person's name.

`reviewed_by` must differ from `annotator`; self-review is rejected.

## What the audit tool rejects

`scripts/validation/audit_annotations.py` fails closed on: a missing or unknown
`review_status`; an unreviewed draft reaching a scored run; an
`llm_candidate` promoted without `promoted_by`; a duplicate `(doc_id, chunk_id)`;
an out-of-vocabulary relevance grade; a missing or malformed `corpus_sha256`; a
dataset mapped against two different corpus states; self-review; a reviewed
record without `reviewed_by`/`reviewed_at`.

Regenerate the outstanding-work list at any time:

```bash
python3 scripts/validation/audit_annotations.py \
  --dataset tests/evaluation/golden_set_v2/annotations_v1.jsonl \
  --backlog tests/evaluation/golden_set_v2/HUMAN_ANNOTATION_BACKLOG.md \
  --source-dataset tests/evaluation/golden_set.jsonl --allow-empty
```

## Definition of done

- every one of the 301 samples has a record;
- every record is `REVIEWED` with a distinct `reviewed_by`;
- `audit_annotations.py` exits `0` with `scorable: 301`;
- a corpus exists whose chunks the ids resolve in, verified by
  `scripts/validation/validate_golden_set_contract.py --resolve-qdrant`
  (or `--resolve-es`).

Until all four hold, the retrieval benchmark reports `BLOCKED` for the corpus and
`NOT_VERIFIED` for every retrieval metric. That is the correct output.