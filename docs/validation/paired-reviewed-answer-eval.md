# Paired reviewed answer comparison

Refs #86/#125/#154; built on #159. This is an offline scorer, not a runtime
switch. No production gate bypass is introduced. Compare declared policies
on an isolated authorized evaluation deployment with RBAC and Answer Gate
enforced. Keep the runtime config digest identical except the Evidence Gate
policy, whose digest is separate. Independent review must verify these
declarations against original execution evidence. Use the same reviewed
dataset bytes, corpus/epoch, model assets, retrieval configuration, permission
scopes, workload (including cache state), and review protocol.

Each arm's private manifest contains arm (baseline/candidate), unique run_id,
environment (ISOLATED_EVALUATION), experiment_variable (evidence_gate_policy),
answer_gate_enabled and rbac_enabled (both true), git_sha (40 hex),
knowledge_epoch (nonnegative integer), dataset_sha256, observations_sha256,
corpus_sha256, model_assets_sha256, retrieval_contract_sha256,
permissions_sha256, workload_sha256, runtime_config_sha256,
review_protocol_sha256, evidence_gate_policy_sha256 (all 64 hex).
The two policy hashes must differ. Hashes bind declared inputs, not
authenticate their origin. Observations use the #159 reviewed contract,
optionally including actual measured end_to_end_ms for every planned case
including failures; do not fill absent timings with zero. Treat this as a
declared nearest-rank P95, not independently verified performance.

```bash
python -m tests.evaluation.paired_answer_eval --dataset /private/gold.jsonl \
  --baseline /private/baseline.jsonl --candidate /private/candidate.jsonl \
  --baseline-manifest /private/baseline-manifest.json \
  --candidate-manifest /private/candidate-manifest.json \
  --out /private/new-paired-report.json
```

Incomplete observations retain planned bounds and missing IDs, suppress
aggregate deltas, and exit 1. Invalid input exits 2; complete diagnostic
exits 0, not a quality pass. Gains and losses include high-risk regressions
even if aggregate quality is unchanged. No real RAGAS, latency, provider
calls or independent human review are produced by this scorer. No policy
or architecture is automatically selected. Use existing pipeline threshold
overrides in an authorized isolated test; gate-off counterfactual execution
requires a separate safe offline execution design and is not implemented.
