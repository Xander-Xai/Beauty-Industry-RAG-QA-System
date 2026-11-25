# Real RAGAS evaluation validation

## Current security admission — 2026-11-25

- Upstream's latest stable release is `ragas==0.4.4` (released 2026-11-14). Its release notes fix the v0.4.3 `langchain-community` VertexAI import failure and GHSA-95ww-475f-pr4f / CVE-2026-6587 (affected RAGAS `>=0.2.3, <=0.4.3`).
- Disposable Python 3.11 candidate: `ragas 0.4.4`, `datasets 5.2.0`, `langchain-core 1.8.4`, `langchain-openai 1.8.5`, `instructor 1.17.0`, `setuptools 84.2.1`. `pip check` passed and `import ragas`, `evaluate`, `datasets`, plus the four metric classes used by this repository imported successfully. Legacy `ragas.metrics` imports emit deprecation warnings.
- `pip-audit --strict --desc` still reports `diskcache 5.6.3`, `PYSEC-2026-2447` / `GHSA-w8v5-vhqr-4h9v` / `CVE-2025-69872`: unsafe pickle deserialization, affected versions `<=5.6.3`, no fixed version published. PyPI's latest is still 5.6.3. Therefore **no safe evaluator combination is currently admitted**, despite the RAGAS 0.4.4 fixes. The pre-existing pinned requirements also have five open Dependabot alerts (#12).
- Project evaluator, CI opt-in and runtime integration test now require `RAGAS_SECURITY_APPROVED=1`; the CI opt-in first runs strict `pip-audit` before checking/injecting `OPENAI_API_KEY`. This value must only be supplied after a clean audit of the exact resolved evaluator environment. No data or evaluator credentials were used in this candidate test.
- Issue #12 remains OPEN. Release conditions: a stable compatible evaluator dependency set with clean strict audit, clean resolver/import checks, project metric imports and harness regression tests; then separately authorized data and credentials before any real evaluation.

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

`Stage outcome` records what one execution did. `Evidence level` uses the canonical
vocabulary from
[Evidence map → Classification vocabulary](../evidence-map.md#classification-vocabulary);
no score was produced here, so no quality level is claimed.

| Stage | Stage outcome | Evidence level | Evidence basis |
|---|---|---|---|
| Deterministic guard | `PASS` | `REPO_VERIFIED` (harness) | 64 evaluation tests pass without any real RAGAS; schema/count/classification, single-evaluation, pipeline-answer, failure-accounting and provenance tests |
| Dependency isolation | `PARTIAL` | `LOCAL_REAL_VALIDATION` (isolated venv) | isolated venv imports cleanly and `pip check` is clean, but `pip-audit` reports advisories (see below) |
| Real evaluator smoke (Level 2) | `BLOCKED` | `PENDING` | no `OPENAI_API_KEY` in this environment; no score produced |
| Real pipeline RAGAS (Level 3) | `BLOCKED` | `PENDING` | no evaluator key and no VLLM/Qdrant/ES/Redis infrastructure to run `OnlineRAGPipeline` |

## Dependency findings (real, not faked)

- **Historical finding (2026-10-02):** `ragas 0.4.3` imported a removed
  `langchain_community.chat_models.vertexai` module. Upstream fixed this in
  `ragas 0.4.4`; the current blocker is the unpatched `diskcache` advisory above.
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
