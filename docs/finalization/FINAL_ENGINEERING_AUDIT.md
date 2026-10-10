# Final engineering audit

| field | value |
|---|---|
| Repository | `github.com/Xander-Xai/Beauty-Industry-RAG-QA-System` |
| Branch | `finalize/engineering-closeout` |
| Commit | `61be8a9` (this document is written at this SHA) |
| Base | `origin/fix/rerank-gate-reliability-audit` (`7d30f38`, PR #91) |
| Audit date (UTC) | 2026-10-10 |
| Host | Linux 6.6 WSL2, 20 vCPU, 15 GiB RAM, Python 3.10.12 |
| GPU present | 1× NVIDIA GeForce RTX 5060 Ti (torch 2.12.1+cu130) |
| CrossEncoder weights | **absent** (`./models/cross-encoder-law`, `./models/cross-encoder-base`) |
| Local suite | `2896 passed, 17 skipped` in 146 s — see [acceptance matrix](ACCEPTANCE_MATRIX.md) |

This audit records what was changed, why, and at what evidence level. It does not
claim any retrieval-quality metric: no metric can be quoted from a host whose
corpus and CrossEncoder weights are absent, and none is.

## 1. What this closeout changed

Six defects were reproduced by execution **before** being fixed. Each is a case
where the system could publish a claim that was not true — a fabricated rerank
result, a mislabeled audit outcome, or a permission leak. The full register is in
[`tests/test_engineering_closeout_regressions.py`](../../tests/test_engineering_closeout_regressions.py),
one test per finding, each with a positive control.

### 1.1 Rerank / Evidence claims (highest severity)

| id | defect (before) | reproduction | fix |
|---|---|---|---|
| **F1** | `rerank_validation.run_comparison` published `status=OK` + `provenance=cross_encoder` + `is_reranking_evidence=true` after a **total weight-load failure**. It called `run_smoke()` and never read its status. | Forced `_load_ranker` to raise; the artifact claimed evidence with `executed:false` and `comparison.cross_encoder:null`. | Propagate `run_smoke().status`; a failed smoke run returns `FAILED`/`deterministic_fallback`, no `top1_rate_delta`, and a recorded degradation. |
| **F2** | `_CrossEncoderRanker.rank` scored a batch with `.predict(...).mean()`, collapsing **every document onto one number**. The sort was a no-op, `positive_at_rank1_rate` was structurally `1.0`, and `top1_rate_delta` structurally `0.0` — an unfalsifiable "the reranker works" claim. | A stub model scoring `[10,9,8]` produced `{A:9,B:9,C:9}` and left the input order unchanged. | Score each `(query, document)` pair individually; reject a misaligned score vector instead of padding with `0.0`. |
| **F3** | `rerank_status._model_available` returned `AVAILABLE` for a directory holding only `config.json` (zero weight bytes); it also accepted a bare file. | `_model_available(dir_with_only_config_json)` → `(True, "")`. | Require a real weight file (`*.bin` / `*.safetensors` / index) on top of the config markers; reject a non-directory. |

**F2 also required a second fix**: `SMOKE_PAIRS` always presented the positive
document at index 0, so *both* sides of the comparison scored `1.0` by
construction. `candidate_order()` now rotates the presentation by a hash of
`pair_id`, drawing the offset from `1..n-1`, so the positive is never first and
both sides must actually rank.

### 1.2 Gate / audit reporting

| id | defect (before) | fix |
|---|---|---|
| **F6** | `run_report` reported `rejected_by_evidence_gate` from its `else` branch for a **cache hit** (an answer), an **admission rejection** (never reached retrieval), and a **generation error after the gate passed**. | `_classify_outcome` names six terminal states; gates are checked before `degraded`. |
| **F7** | The rerank provenance was derived from a **filesystem precheck**, so weights on disk + an in-request load failure reported `cross_encoder`. | `CrossEncoderEnsemble.rerank` now writes an **observed** per-request provenance onto the context; `run_report` prefers it and records `provenance_source: runtime`. |
| — | `cache_lookup` stage reported `L2_SESSION` hits as `cache_miss`. | Added `L2_SESSION` to the hit set. |

### 1.3 Security / HTTP contract

| id | defect (before) | fix |
|---|---|---|
| **F4** | `SessionState` was keyed only on a caller-supplied `session_id`. One principal could read another's `dialog_rounds` and `locked_doc_ids` via `/api/chat` and `/api/dialog_history` — a path **outside** the role/dept mask machinery, so document RBAC never saw it. | Sessions are owner-scoped (memory key and Redis key). Every online call site passes the verified `identity.user_id`. A foreign owner's payload yields a new **empty** session (fail-closed, API unchanged). |
| **F5** | The P2-overload branch assigned a `JSONResponse(503)` to `ctx.final_response`, whose `QueryResponse.answer` is typed `str`, so the documented 503 surfaced as **HTTP 500**. | The status travels out of band on `http_status_override` and the route returns it directly. |

## 2. Deliberately NOT changed

- **vLLM timeout stays HTTP 200 with an error body.** Moving it to 503 is a
  client-visible contract change. `frontend/src/App.jsx` retries only on 401,
  nginx sets no `proxy_next_upstream`, and the load test does not retry — so a
  503 today becomes a hard, non-retried user-visible error. The divergence is
  recorded in `docs/production-readiness.md` and pinned by
  `tests/test_gate_degradation_scenarios.py`. The fix must change the client
  first, then the server, with a regression test.
- **Microservice path (`api-gateway/`, `generation-service/`) is not the
  canonical runtime** (`docs/architecture-baseline.md`). It has no Answer Gate
  and a fail-open Evidence Gate on the generation path. This closeout did not
  rebuild it: the documented runtime is the monolith, and rewriting a retained
  path without a live deployment to test against would be unverifiable. It is
  listed as a risk in §3.
- **The 301-row golden set is not re-labeled or auto-promoted.** It has no
  `doc_id` / `chunk_id` / `corpus_version` / review state, so it fails the v2
  contract and cannot enter official scoring. This is correct, not a defect.

## 3. Remaining risks (not fixed here)

| risk | where | why not fixed | evidence level |
|---|---|---|---|
| No retrieval metric of any kind | everything under `benchmarks/` | No independent corpus and no CrossEncoder weights on this host | **BLOCKED_EXTERNAL** |
| Microservice path has no Answer Gate, fail-open Evidence Gate | `api-gateway/routers/generation.py` | Retained path, not the canonical runtime; needs a live deployment to verify | **NOT_VERIFIED** |
| `user_id = req.user_id or identity.user_id` lets a body field override the JWT for audit / A-B assignment | `api/routes.py:132` | Masks still come from the verified identity, so no privilege is granted; changing it is a behavioural contract change needing its own review | **PASS_CODE** (fix applied), see note |
| Session owner axis is the JWT `user_id`; two principals sharing it are one principal | `core/pipeline_context.py` | By construction; a stronger key would need a stable subject id the JWT does not currently expose | **PASS_LOCAL_REAL** (in-memory) |
| Dual-A5000 topology cannot be confirmed from this host | `router/gpu_gate.py` | Single RTX 5060 Ti present; the gate compares GPU **count**, not GPU **model** | **BLOCKED_EXTERNAL** |

## 4. Acceptance summary

The full matrix — `PASS_CODE` / `PASS_LOCAL_REAL` / `PASS_CI` /
`BLOCKED_EXTERNAL` / `NEEDS_HUMAN` / `NOT_VERIFIED` — is in
[`ACCEPTANCE_MATRIX.md`](ACCEPTANCE_MATRIX.md).

**Project state: ENGINEERING_READY, METRICS_NOT_VERIFIED.** The engineering
controls are implemented and tested; no retrieval-quality number exists and none
may be quoted until a corpus and the CrossEncoder weights are supplied and a
human annotates the golden set.
