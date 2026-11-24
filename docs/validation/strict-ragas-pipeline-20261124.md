# Complete pipeline RAGAS acceptance

Issue #12: opt-in strict acceptance of an already configured real evaluator and
RAG pipeline. The legacy exploratory report behavior is unchanged.

```bash
python -m tests.evaluation.ragas_eval --dataset /path/to/approved-evaluation.jsonl \
  --pipeline --require-ragas --require-complete-pipeline --limit 5 \
  --report-dir /path/to/new-private-run
```

The command requires an independently approved evaluator environment, compatible
dependencies, model assets, data authorization and provider budget. No external
calls or metrics were executed by this change. See the existing isolated
RAGAS dependency/security notes in [real-ragas-evaluation.md](real-ragas-evaluation.md).

Strict mode returns exit 6 without saving a quality report when selected samples
fail, per-sample IDs/counts differ, or any requested overall or row metric is
missing/non-finite. A measured zero is valid. Coverage refers only to the
selected sample set, not the entire corpus or production performance. Selection
by ID preserves the original dataset index for derived sample IDs.

## Offline preflight before evaluator spending

Append `--preflight-only` to the strict pipeline command above to validate the
entire JSONL dataset and selected sample IDs without loading the RAG pipeline
or requiring evaluator credentials. Strict execution uses the same preflight
before model initialization; malformed JSON, non-object records, invalid
reference fields, duplicate effective IDs and unknown selections return exit 7.
A preflight pass proves dataset structure and selection only, not annotation
quality, source authorization, actual model quality or successful retrieval.
