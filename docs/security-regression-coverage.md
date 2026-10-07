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
[Evidence map](evidence-map.md#classification-vocabulary).
This document introduces no status word of its own.

## Coverage matrix

| Threat | Control | Production path | Regression test | Evidence status | Remaining gap |
|---|---|---|---|---|---|
| **A.** Indirect instruction injection inside retrieved chunks | Structural trust boundary: untrusted evidence is confined between reserved delimiters, the user instruction is placed outside and after it, and a system-message policy names the block as data-not-instructions | `models/llm_client.py:434` (escape), `:438`–`:444` (framing), `:64` (preamble), `:99`–`:140` + `:473`/`:479`/`:496` (policy on all three system-prompt branches) | `tests/test_llm_client.py::TestRetrievalTrustBoundary` (`test_poisoned_evidence_is_kept_inside_the_untrusted_block`), `::TestReservedMarkerEscape` | `REPO_VERIFIED` (deterministic, structure-level) | Message-structure only. `models/llm_client.py:29`–`:32` states this does not resolve prompt injection and cannot show the model will not follow instructions found in retrieved content. No real-model behaviour is measured here. |
| **B.** Poisoned ingested document (attacker/submitter-controlled content reaching the index) | **Bounded provenance and quarantine control.** One canonical trust record per source is persisted on every point and document, and the seal gate refuses any epoch whose stored provenance is quarantined or unreadable. See row **H** for the control itself | `offline/source_trust.py` (schema + approval ledger); enforced at `offline/validator.py:152` (`_validate_source_trust`), `offline/text_ingestion.py:625` and `offline/qdrant_writer.py:122` (writers), `offline/elasticsearch_writer.py:143` (BM25); approval path `run_offline.py:294` | `tests/offline/test_source_trust_contract.py` | `REPO_VERIFIED` (deterministic, structure-level) | Ingestion still does not inspect document **content**: provenance bounds *where content may come from*, not whether it is safe, and an approved source can still carry adversarial text. Row A remains the only structural guard at the prompt layer. See row H and [Bounded gaps](#bounded-gaps). |
| **C.** Forged `retrieved_context` / `user_query` / `continuation` boundary markers | Every reserved marker is defined once, and every untrusted channel is escaped before framing: current query, evidence, replayed history (user + assistant), and the continuation assistant prefix | `models/llm_client.py:52` (marker tuple), `:69`–`:96` (escaper), applied at `:332`, `:411`, `:418`, `:434`, `:435` | `tests/test_llm_client.py::TestUserQueryFramingCollision`, `::TestHistoryMarkerEncoding`, `::TestContinuationPrefixEncoding`, `::TestContinuationInstructionBoundary`, `::TestSecurityPolicyTagCoupling` | `REPO_VERIFIED` (deterministic) | Encoding is exact-literal. Case or whitespace near-misses (`<Retrieved_Context>`, `<retrieved_context >`) are not encoded; they are also not true boundaries, so framing uniqueness is preserved, but no test pins that near-miss behaviour. |
| **D.** Cross-role retrieval leakage | Post-recall authorization choke point re-applied per channel, after fusion, after fallback, and on the cached async-CLIP path; missing or malformed document metadata is denied; the BM25 path pushes the same filters into Elasticsearch | `retrieval/parallel_recall.py:71`–`:85`, `:223` (final guard), `:450`, `:483`; `common/auth.py:234`–`:249`; `retrieval/bm25_retriever.py:135`–`:192`; `api/routes.py:451` (media route) | `tests/test_retrieval_authorization_contract.py` (per-channel filtering, fallback scoping, cached-CLIP re-check, metadata fail-closed, BM25 filter shape), `tests/test_architecture_contract.py:58`/`:107`/`:136`/`:167`, `tests/test_media_route.py` (incl. `TestMediaEndpointFailClosed`), `tests/test_bitmask_rbac.py` | `REPO_VERIFIED` (deterministic) | Elasticsearch coverage inspects the generated query, not a live ES result set; the only store actually exercised is in-memory Qdrant. The `role_mask == 0` combined with a non-matching `dept_mask` exclusion **is** covered, at `tests/test_retrieval_authorization_contract.py:28`. |
| **E.** Cross-role Redis L2 cache leakage | Physical L2 address is partitioned by role and dept, and the partition is applied by the cache object itself rather than by caller discipline. Permission scope is validated **before** any cache I/O | `cache/redis_cache.py:162`–`:179` (`rag:l2:rm:<role>:dm:<dept>:<key>`), `:134`–`:159` (validator), called first in `get`/`set` at `:197`/`:233`; logical key `core/pipeline.py:614`–`:644` | `tests/test_cache.py::TestL2PermissionPartitioning`, `::TestPermissionScopeValidation` (incl. zero-Redis-call assertions), `::TestPipelineLogicalCacheKey` | `REPO_VERIFIED` (deterministic) | L1 in-process cache serves only the public partition by design (`cache/redis_cache.py:199`, `:235`). `invalidate_by_epoch` clears L1 only and relies on TTL for L2 (`cache/redis_cache.py:257`); no test asserts old-epoch entries can never be served. |
| **F.** Malformed JWT permission claims, and malformed `AUTH_DEV_MODE` permission headers | Fail-closed validation at identity ingress: `type(value) is int` plus a `[0, 2**32-1]` range check, applied before `UserIdentity` construction so Pydantic coercion cannot launder a mask. A present-but-null claim is treated as malformed, not absent. Issuer refuses to let `extra_claims` shadow the claims it owns. The `AUTH_DEV_MODE` header path runs the same validator rather than a second rule set | `common/auth.py:127`–`:155` (the one validator), `:158`–`:187` (JWT identity), `:191`–`:215` (header decode into that validator), `:218`–`:230` (dev-header identity), `:344`–`:355` + `:357`–`:369` (the two ingress fail-closed returns); `auth/jwt_auth.py:100` + `:127`–`:133` (issuer reserved claims) | `tests/test_auth_identity_resolution.py` (14 malformed-claim cases + HS256 fallback + named-role fallback; dev-header: 7 valid masks × 2 headers, 16 malformed values × 2 headers, absent-header default, valid-beside-malformed, happy-path non-regression, no-Pydantic-laundering, ingress-calls-the-canonical-validator), `tests/test_auth_routes.py` (`test_float_admin_mask_does_not_escalate`, `test_malformed_dept_claim_is_rejected_on_admin_route`, `test_admin_authorization_uses_validated_identity`), `tests/test_jwt_auth.py` (issuer reserved-claim set), `tests/test_demo_corpus_rbac_consistency.py` (walkthrough caption must not claim a check the code does not make) | `REPO_VERIFIED` (deterministic) | `verify_token` (`auth/jwt_auth.py:166`) validates signature, expiry and `type` only — it performs no claim validation by design, delegating that to receivers. The header path decodes a decimal string before validation, so its accepted lexical forms are those of `int(x, 10)`; the *value* contract is the shared one, and no header form is allowed to widen it. `AUTH_DEV_MODE=true` is not a production posture and is not claimed as one. |
| **G.** Document deletion / stale-chunk behavior | Deletion is achieved by epoch isolation: a removed source is never written into the target epoch, and the pre-seal validator fails if any document outside the expected set is present. Shrinking a document supersedes the previous chunk set in the same epoch | `offline/snapshot_builder.py:317`–`:324` (deletion), `offline/validator.py:133`–`:145` (unexpected-document gate), `offline/state_store.py:142`/`:186` (`mark_deleted`, `diff`); stale-chunk removal `offline/text_ingestion.py:416`–`:418`, `offline/qdrant_writer.py:222`–`:224`, `offline/elasticsearch_writer.py:146`–`:150` | `tests/offline/test_snapshot_builder.py:103`/`:117` (deleted doc absent from target epoch), `tests/offline/test_reconciliation_fixes.py::test_validator_rejects_unexpected_documents`, `::test_reingest_shrunk_document_clears_stale_text_chunks`, `::test_reingest_grown_document_adds_new_chunks_without_duplicates`, `::test_reingest_without_images_clears_stale_image_points`, `tests/offline/test_elasticsearch_writer.py:71`–`:72`, `tests/offline/test_state_store.py:53`/`:59` | `REPO_VERIFIED` (deterministic, in-memory stores) | Coverage runs against the in-process `QdrantClient` and a fake Elasticsearch client. No real-service artifact exists — see [Qdrant evidence](repository-truth-audit.md#qdrant-evidence-current-coverage-and-historical-execution). |
| **H.** Unapproved imported content entering an activatable snapshot | Two stored axes and one derived class. `source_trust` is a bounded provenance claim (`MANAGED_INTERNAL` / `UNTRUSTED`) resolved from managed configuration, never inferred from content; `approval_status` is the explicit human decision (`NOT_REQUIRED` / `PENDING_REVIEW` / `APPROVED` / `REJECTED`); `effective_trust_class` collapses them to one bounded value the gate reads (`MANAGED_INTERNAL` / `APPROVED_EXTERNAL` / `UNTRUSTED`). Untrusted content may be parsed and staged — a staging epoch is not queryable — but the snapshot validator refuses to **seal** it. Activation is a separate manual step (`knowledge_version_epoch`), and the query path filters by the active epoch without re-verifying the seal, so this guarantee holds for the normal seal-then-activate lifecycle rather than being enforced at query time. Approval requires an explicit flag and a named `--actor`, is bound to the content hash it was granted for, is written to a SQLite ledger, and emits an audit event. The Elasticsearch path runs the same gate, so BM25 is not a bypass | `offline/source_trust.py:61` (vocabulary), `:91` (`TRUST_APPROVAL_MATRIX`), `:219` (`effective_trust_class`), `:263` (`provenance_verdict`, the one fail-closed check), `:408` (`resolve_source_trust`, path rules), `:460` (`TrustRegistry`), `:499` (`decide`, requires an actor + content hash), `:566` (`resolve`, hash-bound), `:757` (`enforce_writable_provenance`); persisted at `offline/document_processor.py:426`, `offline/text_ingestion.py:185`, `offline/qdrant_writer.py:122`, `offline/elasticsearch_writer.py:143` (mapping), `offline/state_store.py:36`/`:186`; gated at `offline/validator.py:119`/`:152`; refused at `offline/snapshot_builder.py:141`; manifest `offline/snapshot_builder.py:454`; audit actions `common/audit.py` (`knowledge.source.trust_decision`, `knowledge.source.quarantine`) | `tests/offline/test_source_trust_contract.py` — `::TestCanonicalSchema`, `::TestTrustedManagedSourceIsUnchanged`, `::TestUntrustedUnapprovedIsQuarantined` (incl. carry-forward and the BM25 path), `::TestExplicitApprovalUnblocksTheSource`, `::TestRejectedSourceCannotEnter`, `::TestMissingProvenanceFailsClosed`, `::TestNoSilentAutoPromotion`, `::TestExistingLifecycleIsNotRegressed`, `::TestConfigurationResolution`, plus `tests/offline/test_cli.py` (`review-source`) | `REPO_VERIFIED` (deterministic, in-memory stores) | This is a **provenance and quarantine control**, not a content classifier. It reads no document text, so it cannot show that an approved source is benign; the trust claim comes from path-glob configuration, which is only as trustworthy as the configuration and the filesystem the operator controls. There is no re-review trigger on epoch rollover, so an approval granted once keeps applying to the same bytes across rebuilds until the bytes change. Human review of an actual document, adversarial-model behaviour and prompt-injection resistance are all outside what any test here measures. |

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
  paired with a positive case to prevent a fix that denies everything. For row F
  the pair is doubled across two credential sources — a signed JWT claim and an
  `AUTH_DEV_MODE` header — because the property under test is that both reach the
  same verdict through one validator, not that each has a rule set of its own.
  The dev-header cases are asserted over the HTTP boundary, so a rejection that
  arrives as HTTP 500 instead of a decision fails them.
- **Lifecycle controls (G)** are verified against in-memory stores. The
  deletion guarantee is structural (the document never enters the new epoch),
  which is why it holds independently of whether stale rows are physically
  purged.
- **Provenance/quarantine controls (B, H)** are verified as *eligibility*
  assertions, not as content assertions: the same bytes are admitted when the
  source is managed and refused when it is unapproved, and the refusal is shown
  to be reachable through each path that could otherwise promote the content —
  a fresh write, a carry-forward, the BM25 index, and the seal. That last
  combination is what makes it a containment claim: the seal gate is the only
  thing that makes an epoch activatable, so a refusal there is the control. The
  boundary is equally important: nothing in these tests reads document text, so
  they carry no claim about whether an approved source is benign.

### Mutation verification for the tests added in this audit

A test that cannot fail when its control is removed is not regression coverage.
Each newly added or repaired test was checked by disabling the production
control it targets and confirming the test fails. The recorded number is the
count of *failing* tests in the targeted file or directory for that mutation;
the pass count is deliberately omitted because it only restates the suite size
at the moment of the run and drifts as tests are added.

| Control disabled | Result |
|---|---|
| `common/auth.py` mask validator (`type` + range checks) | 18 failed in `tests/test_auth_identity_resolution.py` |
| `common/auth.py` shared range check only (`0 <= value <= 0xFFFFFFFF`) | 26 failed in `tests/test_auth_identity_resolution.py` (JWT and dev-header paths together) |
| `common/auth.py` dev-header ingress back to bare `int(...)` coercion | 31 failed in `tests/test_auth_identity_resolution.py` |
| `common/auth.py` dev-header fail-closed return replaced by a raise | 33 failed in `tests/test_auth_identity_resolution.py` |
| `common/auth.py` `type(value) is int` weakened to `isinstance` | 5 failed in `tests/test_auth_identity_resolution.py` |
| `common/auth.py` dev-header branch reverted while the walkthrough caption kept claiming the shared uint32 contract | 2 failed in `tests/test_demo_corpus_rbac_consistency.py` |
| `offline/text_ingestion.py` stale-chunk deletion | 2 failed in `tests/offline/test_reconciliation_fixes.py` |
| `core/pipeline.py` logical cache key `rm`/`dm` | 4 failed in `tests/test_cache.py` |
| `offline/validator.py` seal trust gate removed from **both** paths (Qdrant points and Elasticsearch documents) | 1 failed in `tests/offline/` |
| `offline/source_trust.py` `provenance_verdict` reduced to a passthrough (nothing is ever blocked) | 13 failed in `tests/offline/` |
| `offline/source_trust.py` writer-side fail-closed on unusable provenance removed | 1 failed in `tests/offline/` |
| `offline/source_trust.py` `TrustRegistry.resolve` auto-approves every untrusted source | 1 failed in `tests/offline/` |
| `offline/source_trust.py` approval no longer bound to its content hash | 1 failed in `tests/offline/` |
| `offline/source_trust.py` rejected sources staged instead of refused | 1 failed in `tests/offline/` |
| `offline/source_trust.py` `may_be_staged` widened so a rejected source looks stageable | 1 failed in `tests/offline/` |
| `offline/source_trust.py` `decide` no longer requires an attributable reviewer | 1 failed in `tests/offline/` |
| `offline/snapshot_builder.py` builder stops refusing a rejected source | 1 failed in `tests/offline/` |
| `offline/snapshot_builder.py` provenance no longer persisted onto chunks | 1 failed in `tests/offline/` |
| `offline/snapshot_builder.py` epoch trust manifest no longer persisted | 1 failed in `tests/offline/` |
| `offline/validator.py` seal gate stops recording which source blocked it | 1 failed in `tests/offline/` |
| `offline/state_store.py` change detection ignores a recorded trust decision | 1 failed in `tests/offline/` |

All production files were restored afterwards and verified clean.

The first row of that group is the one the issue asks for directly: with the
seal/activation gate removed, an unapproved source and a point with no
provenance at all both become sealable, and the suite fails.

## Bounded gaps

Recorded as gaps rather than closed by invention. Each is stated with the exact
boundary of what is missing.

### B. Ingestion bounds provenance, not content

**Status: addressed on the provenance side only.** What now exists is the row-H
contract: every ingested source carries a canonical trust record, unapproved
imported content is quarantined out of activatable snapshots, approval is
explicit and attributed, and missing provenance fails closed. Everything that
would require looking at document *content* is unchanged and remains open.

What is **not** here, precisely:

- no content sanitization, injection heuristic, or document scoring anywhere in
  `offline/`. The trust claim is derived from a path glob
  (`offline/source_discovery.py:85`), so it says where a file came from, never
  what is in it;
- no review of the document itself. `run_offline.py review-source` records a
  human decision against a content hash; it does not show the reviewer anything
  about the document beyond the hash and the path, and the operator is expected
  to inspect the file themselves;
- no re-review trigger. An approval is bound to a content hash, so editing the
  file returns it to `PENDING_REVIEW`, but nothing re-examines a source when the
  *configuration* that classified it changes, or when an epoch is rebuilt
  repeatedly from the same approved bytes;
- no false-positive rate and no measured recall cost, because no classifier runs.
  The cost of this design is that every genuine import needs one human decision,
  and that number has not been measured.

Consequence, stated precisely: an approved document can still contain adversarial
text. It is parsed, chunked, embedded, indexed, made retrievable and reaches the
LLM inside the row-A untrusted block. Row A contains it at the prompt layer and
row H bounds how it got into the index, but nothing inspects its content on the
way in.

Boundary of the recommendation: ingestion entry points are CLI and filesystem
(`run_offline.py:37`–`:70`); there is no HTTP upload route in `api/` or
`api-gateway/`, so the exposure depends entirely on who can write into the
knowledge-base directory. Because there is no upload route, a content-scanning
control would have a narrow attack surface to justify — which is why the
provenance route was taken and why adding one is a separate, scoped decision
rather than an omission from this one.

This remains the only place where a false negative could be made durable: the
ingestion decision is persisted in the index and in the approval ledger, so
content that was admitted wrongly stays admitted until its bytes change.

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

### F. Dev-header identity branch had a weaker contract than the JWT branch — closed

`common/auth.py:357`–`:369` used to build its masks with `int(...)`, applying
neither the strict `type(...) is int` check nor the `[0, 2**32-1]` range check
that the JWT path applies. Two concrete consequences, both now closed:

- a mask outside the canonical range was accepted, so `X-Role-Mask: -1`
  authenticated with `-1`, and `-1 & mask` overlaps every document mask;
- a header that was not an integer (`"1.0"`, `"abc"`, `""`) raised an unhandled
  `ValueError` and returned HTTP 500 rather than an authorization decision.

The branch now decodes the header and hands the result to
`_validate_permission_mask_claim` (`common/auth.py:191`–`:215`) — the same
function the JWT path calls at `:174`/`:180`. There is one validator and one
rule set; a second copied range check would leave
`test_dev_header_ingress_calls_the_canonical_validator` uncalled and failing.
A rejected header returns the anonymous zero-mask identity, the same
fail-closed outcome the JWT branch already used.

`X-User-ID` is a label and grants nothing on its own; an absent mask header
keeps its pre-existing default of mask 0, which is itself a valid uint32 and
already clears no restricted document. RBAC bitmask semantics, the role/dept
AND/OR rules and the JWT path are untouched.

What this does **not** claim:

- **Not production identity validation.** `AUTH_DEV_MODE=true` trusts
  caller-supplied headers by design. This closes a contract asymmetry between
  two identity sources; it does not make dev mode a safe deployment posture, and
  nothing here was exercised against a running service.
- **No new authority.** `-1` could already authenticate; the fix removes masks
  the canonical range never allowed. It grants no access that was not already
  reachable, and it closes no privilege-escalation path beyond that.
- **Lexical forms are still a header-transport detail.** `"+7"` decodes to the
  valid mask 7. This is decoding, not validation; the value contract is the
  shared uint32 one and no accepted header form can widen it.
- Row F's `verify_token` gap is unchanged: it validates signature, expiry and
  `type` only, by design.

Evidence: 26 / 31 / 33 / 5 tests in `tests/test_auth_identity_resolution.py`
failed under the four mutations recorded below, and 2 in
`tests/test_demo_corpus_rbac_consistency.py` failed when the walkthrough caption
was left claiming a contract the code did not enforce.

## Reproducing this audit

```bash
# The security-relevant suites
python -m pytest tests/test_llm_client.py tests/test_jwt_auth.py tests/test_auth_routes.py \
  tests/test_auth_identity_resolution.py tests/test_cache.py \
  tests/test_retrieval_authorization_contract.py tests/test_bitmask_rbac.py \
  tests/test_media_route.py tests/test_architecture_contract.py \
  tests/offline/test_source_trust_contract.py \
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

Row H binds the same constraint with an extra clause. It is a **provenance and
quarantine control**, so the correct description is "an unapproved imported
source cannot enter an activatable snapshot, and admitting one requires an
attributed human decision bound to the reviewed content hash". It is never
"ingestion attacks are detected" — nothing reads the document — never "poisoned
documents are blocked", because an *approved* document is admitted regardless of
its content, and never anything about prompt-injection resistance. The honest
one-line summary is: this reduces the ingestion-poisoning surface and makes the
review decision durable and attributable; it does not eliminate prompt
injection, which stays `PENDING` real adversarial-model validation and out of
scope for this repository.

The negative assertion is pinned too, not just asserted in prose:
`tests/offline/test_source_trust_contract.py::test_this_control_is_a_provenance_gate_not_a_content_classifier`
ingests one fixed piece of injection-flavoured text twice, once as
`MANAGED_INTERNAL` (seals) and once as `UNTRUSTED` (refused), which is only
consistent with a control that never inspects the string. No test in this
repository searches for phrases like "ignore previous instructions" as a
security mechanism.
