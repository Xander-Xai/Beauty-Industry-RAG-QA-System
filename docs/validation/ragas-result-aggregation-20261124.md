# RAGAS result aggregation contract (2026-11-24)

The isolated evaluator pin uses RAGAS 0.2.15. Its documented EvaluationResult
exposes per-sample scores and to_pandas(), not one scalar attribute per metric:
https://docs.ragas.io/en/v0.2.15/references/evaluation_schema/

Aggregate observed finite numeric values by metric from score rows, with the
public dataframe API as a fallback. Reject a row-count mismatch, malformed
row shape or dataframe extraction failure; omit unobserved metrics. Measured
zero is valid. Legacy scalar results are supported only when no row API exists.
Strict pipeline acceptance still requires all selected rows and metrics to be
finite and present; partial aggregates do not satisfy it.

Offline regressions exercise row-oriented and dataframe-shaped results.
No model request or real RAGAS runtime was executed by this change; the
dependency and provider blockers in the historical real evaluation report
remain separate.

```bash
pytest -q tests/evaluation/test_ragas_result_aggregation.py tests/evaluation/test_ragas_eval.py
```
