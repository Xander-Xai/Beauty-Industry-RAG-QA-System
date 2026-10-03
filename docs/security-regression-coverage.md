# Security regression coverage: deterministic controls and their evidence

Scope: the deterministic regression coverage that exists for a fixed set of
enterprise-RAG security threat cases. This document records what is **actually
covered**, what is **not**, and where the boundary of each control lies.

Two constraints shape every row:

1. **A control is not its own evidence.** A row claims coverage only where a
   collected deterministic test fails when the production control is removed. The
   mutation checks below are how each newly added test was verified.
2. **A defense-in-depth control is described as one.** Every control here is a
   layer that reduces a specific attack surface. None of them makes the
   underlying threat impossible, and no row should be read that way.

Evidence levels are the canonical vocabulary defined in
[Interview evidence map](interview-evidence-map.md#classification-vocabulary).
This document introduces no status word of its own.

## Coverage matrix

| Threat | Control | Production path | Regression test | Evidence status | Remaining gap |
|---|---|---|---|---|---|
| **A.** Indirect instruction injection inside retrieved chunks | Structural trust boundary: untrusted evidence is confined between reserved delimiters, the user instruction is placed outside and after it, and a system-message policy names the block as data-not-instructions | `models/llm_client.py:434` (escape), `:438`–`:444` (framing), `:64` (preamble), `:99`–`:140` + `:473`/`:479`/`:496` (policy on all three system-prompt branches) | `tests/test_llm_client.py::TestRetrievalTrustBoundary` (`test_poisoned_evidence_is_kept_inside_the_untrusted_block`), `::TestReservedMarkerEscape` | `REPO_VERIFIED` (deterministic, structure-level) | Message-structure only. `models/llm_client.py:29`–`:32` states this does not resolve prompt injection and cannot show the model will not follow instructions found in retrieved content. No real-model behaviour is measured here. |
| **B.** Poisoned retrieved document (attacker/submitter-controlled content reaching the LLM) | **No ingestion-side control exists.** The only control on this path is the prompt-layer boundary in row A | — (nothing in `offline/` inspects or quarantines content) | — | `PENDING` | Ingestion validates identity, shape, permissions and epoch only (`offline/validation.py:13`–`:48`, `offline/validator.py:56`–`:113`, `offline/document_processor.py:92`). Text is decoded, newline-normalized and stripped before it is chunked and embedded, and size-capped (`offline/document_processor.py:92`–`:93`, applied at `:206`, `:234`, `:319`, `:351`; `offline/text_ingestion.py:145`–`:152`); the ingested text is otherwise carried through uninspected, and nothing in `offline/` sanitizes, quarantines or scores it. See [Bounded gaps](#bounded-gaps). |
| **C.** Forged `retrieved_context` / `user_query` / `continuation` boundary markers | Every reserved marker is defined once, and every untrusted channel is escaped before framing: current query, evidence, replayed history (user + assistant), and the continuation assistant prefix | `models/llm_client.py:52` (marker tuple), `:69`–`:96` (escaper), applied at `:332`, `:411`, `:418`, `:434`, `:435` | `tests/test_llm_client.py::TestUserQueryFramingCollision`, `::TestHistoryMarkerEncoding`, `::TestContinuationPrefixEncoding`, `::TestContinuationInstructionBoundary`, `::TestSecurityPolicyTagCoupling` | `REPO_VERIFIED` (deterministic) | Encoding is exact-literal. Case or whitespace near-misses (`<Retrieved_Context>`, `<retrieved_context >`) are not encoded; they are also not true boundaries, so framing uniqueness is preserved, but no test pins that near-miss behaviour. |
| **D.** Cross-role retrieval leakage | Post-recall authorization choke point re-applied per channel, after fusion, after fallback, and on the cached async-CLIP path; missing or malformed document metadata is denied; the BM25 path pushes the same filters into Elasticsearch | `retrieval/parallel_recall.py:71`–`:85`, `:223` (final guard), `:450`, `:483`; `common/auth.py:234`–`:249`; `retrieval/bm25_retriever.py:135`–`:192`; `api/routes.py:451` (media route) | `tests/test_retrieval_authorization_contract.py` (per-channel filtering, fallback scoping, cached-CLIP re-check, metadata fail-closed, BM25 filter shape), `tests/test_architecture_contract.py:58`/`:107`/`:136`/`:167`, `tests/test_media_route.py` (incl. `TestMediaEndpointFailClosed`), `tests/test_bitmask_rbac.py` | `REPO_VERIFIED` (deterministic) | Elasticsearch coverage inspects the generated query, not a live ES result set; the only store actually exercised is in-memory Qdrant. The `role_mask == 0` combined with a non-matching `dept_mask` exclusion **is** covered, at `tests/test_retrieval_authorization_contract.py:28`. |
| **E.** Cross-role Redis L2 cache leakage | Physical L2 address is partitioned by role and dept, and the partition is applied by the cache object itself rather than by caller discipline. Permission scope is validated **before** any cache I/O | `cache/redis_cache.py:162`–`:179` (`rag:l2:rm:<role>:dm:<dept>:<key>`), `:134`–`:159` (validator), called first in `get`/`set` at `:197`/`:233`; logical key `core/pipeline.py:614`–`:644` | `tests/test_cache.py::TestL2PermissionPartitioning`, `::TestPermissionScopeValidation` (incl. zero-Redis-call assertions), `::TestPipelineLogicalCacheKey` | `REPO_VERIFIED` (deterministic) | L1 in-process cache serves only the public partition by design (`cache/redis_cache.py:199`, `:235`). `invalidate_by_epoch` clears L1 only and relies on TTL for L2 (`cache/redis_cache.py:257`); no test asserts old-epoch entries can never be served. |
| **F.** Malformed JWT permission claims | Fail-closed validation at identity ingress: `type(value) is int` plus a `[0, 2**32-1]` range check, applied before `UserIdentity` construction so Pydantic coercion cannot launder a mask. A present-but-null claim is treated as malformed, not absent. Issuer refuses to let `extra_claims` shadow the claims it owns | `common/auth.py:127`–`:150` (validator), `:153`–`:183` (identity), `:294`–`:301` (ingress); `auth/jwt_auth.py:100` + `:127`–`:133` (issuer reserved claims) | `tests/test_auth_identity_resolution.py` (14 malformed-claim cases + HS256 fallback + named-role fallback), `tests/test_auth_routes.py` (`test_float_admin_mask_does_not_escalate`, `test_malformed_dept_claim_is_rejected_on_admin_route`, `test_admin_authorization_uses_validated_identity`), `tests/test_jwt_auth.py` (issuer reserved-claim set) | `REPO_VERIFIED` (deterministic) | `verify_token` (`auth/jwt_auth.py:166`) validates signature, expiry and `type` only — it performs no claim validation by design, delegating that to receivers. The dev-header identity branch (`common/auth.py:306`–`:315`) coerces with `int()` and applies no range check, unlike the JWT path; only its happy path is covered. |
| **G.** Document deletion / stale-chunk behavior | Deletion is achieved by epoch isolation: a removed source is never written into the target epoch, and the pre-seal validator fails if any document outside the expected set is present. Shrinking a document supersedes the previous chunk set in the same epoch | `offline/snapshot_builder.py:317`–`:324` (deletion), `offline/validator.py:133`–`:145` (unexpected-document gate), `offline/state_store.py:142`/`:186` (`mark_deleted`, `diff`); stale-chunk removal `offline/text_ingestion.py:416`–`:418`, `offline/qdrant_writer.py:222`–`:224`, `offline/elasticsearch_writer.py:146`–`:150` | `tests/offline/test_snapshot_builder.py:103`/`:117` (deleted doc absent from target epoch), `tests/offline/test_reconciliation_fixes.py::test_validator_rejects_unexpected_documents`, `::test_reingest_shrunk_document_clears_stale_text_chunks`, `::test_reingest_grown_document_adds_new_chunks_without_duplicates`, `::test_reingest_without_images_clears_stale_image_points`, `tests/offline/test_elasticsearch_writer.py:71`–`:72`, `tests/offline/test_state_store.py:53`/`:59` | `REPO_VERIFIED` (deterministic, in-memory stores) | Coverage runs against the in-process `QdrantClient` and a fake Elasticsearch client. No real-service artifact exists — see [Qdrant evidence](repository-truth-audit.md#qdrant-evidence-current-coverage-and-historical-execution). |

## What "coverage" means for each control

The distinction matters when reading the matrix, because the controls are not
the same kind of object.

- **Message-structure controls (A, C)** are verified by asserting the exact
  prompt text: that untrusted content stays inside its block, that marker
  occurrences cannot produce a second boundary, and that the real framing stays
  unique. They are deterministic and complete *as structure assertions*. They
  are not evidence about model behaviour, and no test here claims otherwise.
- **Authorization controls (D, E, F)** are verified by fail-closed assertions:
  an unauthorized or malformed input must be denied, and a valid input must
  still be allowed. The negative case is the security-relevant one, so each is
  paired with a positive case to prevent a fix that denies everything.
- **Lifecycle controls (G)** are verified against in-memory stores. The
  deletion guarantee is structural (the document never enters the new epoch),
  which is why it holds independently of whether stale rows are physically
  purged.

### Mutation verification for the tests added in this audit

A test that cannot fail when its control is removed is not regression coverage.
Each newly added or repaired test was checked by disabling the production
control it targets and confirming the test fails:

| Control disabled | Result |
|---|---|
| `common/auth.py` mask validator (`type` + range checks) | 18 failed in `tests/test_auth_identity_resolution.py` |
| `offline/text_ingestion.py` stale-chunk deletion | 2 failed in `tests/offline/test_reconciliation_fixes.py` |
| `core/pipeline.py` logical cache key `rm`/`dm` | 4 failed in `tests/test_cache.py` |

All production files were restored afterwards and verified clean.

## Bounded gaps

Recorded as gaps rather than closed by invention. Each is stated with the exact
boundary of what is missing.

### B. No ingestion-side poisoned-document control

There is no content sanitization, injection heuristic, quarantine, provenance
labeling, or trust scoring for ingested documents anywhere in `offline/`. What
exists is structural validation only:

- identity and shape: `offline/validation.py:13`–`:48` (uint32 masks, epoch,
  source-id charset and path traversal), `offline/text_ingestion.py:68`–`:77`;
- resource limits: `offline/document_processor.py:103`–`:110`,
  `offline/text_ingestion.py:146`–`:158`;
- epoch seal-gate integrity: `offline/validator.py:56`–`:113`;
- a human review gate on *feedback-derived* records only
  (`offline/feedback_loop.py:189`–`:209`, which exports only `accepted`
  records to training/eval JSONL — not to the document corpus).

Consequence, stated precisely: an attacker-authored document is parsed,
chunked, embedded, indexed and made retrievable, and its text reaches the LLM
inside the row-A untrusted block. Row A contains it at the prompt layer. Nothing
contains it before that.

Boundary of the recommendation: ingestion entry points are CLI and filesystem
(`run_offline.py:37`–`:70`); there is no HTTP upload route in `api/` or
`api-gateway/`, so the exposure depends entirely on who can write into the
knowledge-base directory. Closing this gap is a separate, scoped piece of work —
it needs a threat model for the ingestion operator, not a prompt change.

### G. Deletion branch in the incremental builder is unreachable

`offline/snapshot_builder.py:317`–`:324` guards its deletion work with
`source = source_by_id.get(source_id); if source is not None:`. `source_by_id`
is built from the currently discovered sources (`:288`), while
`changes.deleted` is by construction the set of source ids that are **no longer**
present (`offline/state_store.py:186`). The lookup therefore always returns
`None`, and the `replace_document(doc_id, to_epoch, [], [])` /
`delete_document(...)` calls inside the branch never execute.

This is **not** a behavioural gap and needs no fix for correctness: deletion
works by epoch isolation — the removed document is never written into the target
epoch, and `offline/validator.py:133`–`:145` fails the seal if any document
outside the expected set is present. `tests/offline/test_snapshot_builder.py:117`
asserts the observable outcome. The branch is defensive code that cannot run;
only `state_store.mark_deleted(source_id)` at `:335`–`:336` takes effect.

Recorded here so a future reader does not mistake the dead branch for the
mechanism that deletes a document.

### F. Dev-header identity branch has a weaker contract than the JWT branch

`common/auth.py:306`–`:315` builds masks with `int(...)` and applies neither the
strict `type(...) is int` check nor the `[0, 2**32-1]` range check that
`common/auth.py:127`–`:150` applies to JWT claims. A dev-mode deployment
therefore has two identity sources with different validation contracts, and only
the happy path is covered by tests.

This is bounded to `AUTH_DEV_MODE=true`, which is not a production posture.
Recorded as a contract asymmetry, not as a production vulnerability.

## Reproducing this audit

```bash
# The security-relevant suites
python -m pytest tests/test_llm_client.py tests/test_jwt_auth.py tests/test_auth_routes.py \
  tests/test_auth_identity_resolution.py tests/test_cache.py \
  tests/test_retrieval_authorization_contract.py tests/test_bitmask_rbac.py \
  tests/test_media_route.py tests/test_architecture_contract.py \
  tests/offline/ tests/test_offline_text_ingestion.py

# Full suite and repository consistency
python -m pytest tests/
python scripts/check_repo_consistency.py
```

## Wording constraint

Controls here are defense-in-depth layers evaluated against a fixed threat list.
They reduce a specific attack surface and are regression-tested as such. The
correct description is "the retrieved-chunk trust boundary is covered by
deterministic structural tests", never "prompt injection is solved" and never
"jailbreak-proof" — for the reasons recorded in row A and in
`models/llm_client.py:29`–`:32`.
