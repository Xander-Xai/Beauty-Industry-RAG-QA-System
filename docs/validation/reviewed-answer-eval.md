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

The CLI hashes the exact dataset bytes it scores, and requires a new output path.
Existing reports, symlinks and hard-link aliases are never overwritten; use a fresh
run-specific output file for each execution.


## Strict business-task success

A reviewed answer is successful only if the labeled expected outcome is met
and there is no observed RBAC leak. Expected answers must have full evidence
support and citations, with no explicit Evidence Gate or Answer Gate rejection.
Expected refusals must actually be refused with not_applicable support, a delivered refusal message, a separately reviewed correct business reason and a private evidence reference.
Partial evidence, unnecessary refusal and unauthorized disclosure are not successes.

The metrics block reports success count and rate among reviewed observations.
The CLI also reports lower and upper bounds over the entire labeled set:
missing observations contribute zero to the lower bound and at most one to the
upper bound. These are deterministic missing-review bounds, not statistical
confidence intervals. With complete coverage the bounds coincide. Exit 0 still
means complete scoring, not a quality pass or publication approval.


## Refusal reason evidence (scoring revision v2)

A safe system outage is not a successful regulatory decision. For refused
observations record the independent review classification
`refusal_reason_review` (business_reason_verified / wrong_business_reason /
infrastructure_failure / unclassified), plus `refusal_evidence_ref` pointing to
the private review evidence. A correct business reason must be verified
against the frozen expected behavior, the actual delivered refusal, authorized
sources and execution trace. Do not infer this from the gate's own status or
fabricate review references. The scorer validates structure, not authenticity.

Legacy missing reason rows remain readable but earn no strict refusal credit.
Legacy `refusal_recall` is disposition-only, not correct-reason quality.
Use `verified_refusal_precision` (verified correct / all observed refusals)
and `verified_refusal_recall` (verified correct / observed must-refuse cases);
retain coverage and strict success bounds across the full planned population.
Unclassified and infrastructure refusals are counted separately. Partial
coverage or unverified reasons cannot establish publishable accuracy.

## Paired interview evidence protocol (#18 / #86 / #125 / #154)

Freeze corpus/epoch, actual model assets, candidate depths, reviewed sample
identities, and environment. Compare BM25, Dense, Hybrid RRF and BiEncoder/
single/dual CE challenger chains using the existing strict retrieval suite.
Only same-corpus paired metrics are comparable. No-answer cases are evaluated
in the answer lane, not treated as retrieval successes. Compare Evidence Gate
enabled and disabled only in an isolated authorized evaluation environment,
never in public serving; keep Answer Gate and RBAC in place. Record independent
evidence support, verified refusal precision/recall, unsafe answers, critical
regulatory misses, latency and failures. Separate retrieval-only P95 from
end-to-end response P95. RAGAS faithfulness requires a real configured
evaluator and remains separate from human support judgments. Choose the
simplest ranking chain meeting declared quality and safety gates; do not
remove a stage solely because fixture tests pass. Begin with the existing
27 reviewed retrieval cases and separately expand toward 100–200 independently
reviewed cases; do not relabel legacy 301 records as gold. Preserve held-out
cases and do not tune thresholds on them.

No actual provider/model/ERP or retrieval service calls are made by this
offline scorer; no new empirical results are claimed.
