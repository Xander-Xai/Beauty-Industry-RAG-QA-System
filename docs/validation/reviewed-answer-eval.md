# Reviewed answer evaluation (offline diagnostic)

Part of #86 and #125. This is an input contract and scorer, not a recorded
model evaluation or a published accuracy result. The 12-row repository answer
set is a separate test specification, not the 27-case frozen BM25 corpus.
Confirm that each expected outcome is valid against the actual corpus and
permission scope before commissioning human review.

## Collect evidence

Run the intended pipeline against an authorized, isolated corpus. Record the
actual final response and citations, Evidence Gate decision, Answer Gate result,
run/commit/model/knowledge epoch, identity scope, and source hashes in a private
run bundle. Do not publish private queries, restricted citations or customer
data. An independent human reviewer should inspect the delivered response,
source passages and permissions, then prepare one JSONL observation per sample.

Required fields: sample_id, gate_decision, final_disposition (answered/refused),
answer_text, citations (array of string IDs), evidence_support
(supported/partially_supported/unsupported/not_applicable), rbac_leak (boolean),
reviewer, reviewed_at, review_status (HUMAN_REVIEWED). Refusals require
not_applicable; supported or partially supported answers require citations.
The actual final disposition may differ from the Evidence Gate decision, e.g.
an Answer Gate rejection following an Evidence Gate pass. Never generate
reviewer names or substitute LLM self-scores for human judgments.

## Score

```bash
python -m tests.evaluation.answer_eval \
  --dataset tests/evaluation/answer_eval_set.jsonl \
  --observations /private/reviewed-answer-observations.jsonl \
  --out /private/answer-eval-report.json
```

Exit 0: complete diagnostic coverage; exit 1: incomplete coverage, with missing
IDs and observed-only metrics; exit 2: malformed input. Complete coverage does
not imply good answer quality. The report includes both input SHA-256 hashes
and never marks publication eligibility as verified. Review attestation is
caller-supplied, not independently authenticated by this CLI. Keep original
run provenance and human review evidence separately.

No real observations or performance claims are supplied by this change.
