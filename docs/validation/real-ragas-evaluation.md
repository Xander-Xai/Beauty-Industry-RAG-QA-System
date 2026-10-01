# Real RAGAS evaluation validation

Local validation for the RAGAS evaluation path (Issue #12). This records what
was actually executed, what was fixed, and what remains blocked. Local
evaluation is not a production quality benchmark.

## Environment

- Verification date: 2026-10-02
- Branch: `validation/real-ragas-evaluation` (based on `main` `89a4195`; see PR for the head SHA)
- Python: 3.10.12
- Provider / evaluator model (config): `openai` / `gpt-4o-mini` (`config.json` → `ragas.llm_backend`)
- Dataset: `tests/evaluation/golden_set.jsonl` (301 entries)
- Isolated evaluator venv (`/.venv-ragas`, disposable, gitignored): ragas 0.2.15, langchain-community 0.3.31, langchain-core 0.3.86, langchain-openai 0.3.35, datasets 5.0.1, openai 2.54.0

## Status

| Stage | Result | Evidence |
|---|---|---|
| Deterministic guard | PASS | 64 evaluation tests pass without any real RAGAS; schema/count/classification, single-evaluation, pipeline-answer, failure-accounting and provenance tests |
| Dependency isolation | PARTIAL | isolated venv imports cleanly and `pip check` is clean, but `pip-audit` reports advisories (see below) |
| Real evaluator smoke (Level 2) | BLOCKED | no `OPENAI_API_KEY` in this environment; no score produced |
| Real pipeline RAGAS (Level 3) | BLOCKED | no evaluator key and no VLLM/Qdrant/ES/Redis infrastructure to run `OnlineRAGPipeline` |

## Dependency findings (real, not faked)

- **Latest RAGAS is import-broken.** `ragas 0.4.3` does `from
  langchain_community.chat_models.vertexai import ChatVertexAI`, but
  `langchain-community 0.4.x` no longer ships that module. `pip check` is clean
  yet `import ragas` raises `ModuleNotFoundError`. This is why the global
  environment cannot run RAGAS today.
- **Importable RAGAS carries advisories.** `ragas 0.2.15` imports cleanly in a
  fresh venv with `langchain-community` 0.3.x, but `pip-audit` reports 15
  advisories across `diskcache`, `langchain`, `langchain-core`,
  `langchain-openai`, `langchain-text-splitters`, `ragas`:
  - `ragas 0.2.15`: PYSEC-2026-3046 (no fix), PYSEC-2026-3047 (fix 0.3.0rc1)
  - `langchain 0.3.x` advisories are fixed only in `langchain 1.x`, which
    `ragas 0.2.x` cannot use.
- Consequence: RAGAS stays out of default `requirements.txt`; the pinned,
  isolated `requirements-ragas.txt` exists only for disposable eval venvs and
  the opt-in CI smoke job. Production Security CI is unchanged.

## Correctness fixes (P0)

- **Duplicate evaluation removed.** `main()` previously called
  `evaluate()`/`evaluate_with_pipeline()` and then `reporter.run_and_report()`,
  which called `evaluate()` a second time. `main()` now evaluates exactly once
  and `RAGASReporter.build_report()` builds the report from the stored run
  state without re-calling the evaluator.
- **Pipeline/report answer mismatch fixed.** In `--pipeline` mode the report now
  uses the real pipeline-generated answers and real retrieved contexts.
  Previously the reporter re-evaluated with the dataset's golden answers, so the
  report described reference answers, not pipeline output.
- **Pipeline answer extraction fixed.** `OnlineRAGPipeline.process(ctx)` returns
  a `str`; the old code looked for a dict/`.answer` attribute and therefore
  recorded empty answers. It now uses the returned string or `ctx.final_response`.
- **Failure accounting added.** Failed pipeline samples are recorded
  (`sample_id`, `stage`, `exception`, sanitized message), excluded from the
  aggregate, and an all-failed run raises and exits non-zero instead of
  evaluating empty answers.
- **Honest failure.** Missing dependency → exit 2; missing evaluator key in
  `--require-ragas` mode → exit 3; neither path writes a report.

## Commands run

```bash
python3 -m pytest tests/evaluation/ -q                     # 64 passed (no real RAGAS)
python3 -m tests.evaluation.ragas_eval --dataset tests/evaluation/golden_set.jsonl \
  --require-ragas --report-dir /tmp/opencode/ragas-out     # exit 2, no report
python3 -m tests.evaluation.ragas_eval --dataset tests/evaluation/golden_set.jsonl \
  --report-dir /tmp/opencode/ragas-out                     # exit 3 (UNAVAILABLE), no report
# isolated venv
python -m venv .venv-ragas && . .venv-ragas/bin/activate
pip install -r requirements-ragas.txt && pip check          # No broken requirements found
python -c "import ragas; from ragas import evaluate; from ragas.metrics import faithfulness"
pip-audit --path .venv-ragas/lib/python3.10/site-packages    # 15 advisories (recorded)
```

## Known limitations

- No real evaluator request was executed (no API key); no metric value is reported.
- The project pipeline was not executed (no model weights / infrastructure).
- RAGAS evaluator scores are known to vary across repeated runs; this path is not claimed to be bit-for-bit deterministic.
- Only a small-sample workflow is designed; a full 301-entry run is intentionally not performed here.

## Evidence boundary

Real RAGAS evaluation on a local validation sample would not be equivalent to a
production quality benchmark. No production quality, latency, or business-impact
claim is introduced without reproducible production evidence.
