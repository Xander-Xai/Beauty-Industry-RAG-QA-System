# Real Qdrant store validation (VAL-STORE-001)

This record captures a single-host run of the dense/visual recall path against a
**real Qdrant server**, not the in-process `QdrantClient(":memory:")` used by the
deterministic suite. It is the `LOCAL_REAL_VALIDATION` artifact that
`docs/deferred-runtime-validation.md` → `VAL-STORE-001` names as its promotion
requirement. It is not a production-cluster, HA or model-quality result.

## Provenance

| Field | Value |
|---|---|
| Base commit | `6d2576e` (branch `fix/engineering-audit-p0`; changes in this validation are uncommitted in the working tree) |
| Qdrant server | `qdrant/qdrant:v1.12.0`, container `rag-validation-qdrant`, `127.0.0.1:6333` |
| Qdrant client | `qdrant-client` **1.18.0** — see [Version compatibility](#version-compatibility) |
| Redis (cache-key check) | local `redis:7-alpine`, `127.0.0.1:6379`, db 15 (disposable) |
| Embedding source | `offline/text_ingestion.py::DeterministicTestEmbedder` (768d) and a constant 512d image vector — **not** BGE/CLIP |
| Script | `scripts/validation/validate_qdrant_store.py` |
| Gated test | `tests/integration/test_qdrant_store_runtime.py` (`RUN_RUNTIME_VALIDATION=1`) |

## Command

```text
docker run -d --name rag-validation-qdrant -p 127.0.0.1:6333:6333 qdrant/qdrant:v1.12.0
QDRANT_HOST=127.0.0.1 QDRANT_PORT=6333 python3 -u scripts/validation/validate_qdrant_store.py
```

## Captured result

**20 / 20 checks passed.** The counts below are not hand-written: the script
appends a structured record per check (`checks.json`) and derives
`checks_run` / `checks_passed` / `checks_failed` from that record with
`check_counts()`. `tests/validation/test_qdrant_validation_evidence.py` fails if
`metadata.json` and `checks.json` ever disagree, which is how an earlier
`21/21` claim — a count no run ever produced — is now impossible to repeat.

The run also printed one non-gating finding, recorded in `checks.json` under
`compatibility` and deliberately **excluded** from the 20:

```text
[INFO] client 1.18.0 vs server 1.12.0: OUTSIDE_GUARANTEED_WINDOW (minor delta 6; documented support: False)
```

```text
[PASS] connected to Qdrant at 127.0.0.1:6333
[PASS] text documents written to real Qdrant
[PASS] dense search returned 3/3 documents
[PASS] every dense hit carries the active epoch
[PASS] role A sees public + role-A docs
[PASS] role A cannot see role-B docs (RBAC filtered before fusion)
[PASS] role B sees only its own + public
[PASS] two identities produce different visible sets
[PASS] epoch A docs intact: ['doc-public', 'doc-role-a', 'doc-role-b']
[PASS] epoch B rewritten independently: ['doc-public']
[PASS] epoch-B filter returns only epoch-B points
[PASS] epoch-A and epoch-B point ids do not collide
[PASS] image search returned 1/1 image
[PASS] image hit comes from the image collection
[PASS] RRF merged text + image sources: ['doc-image', 'doc-public', 'doc-role-a', 'doc-role-b']
[PASS] dense path returns empty (logged + degraded) when Qdrant is unreachable
[PASS] cache key changes with the knowledge epoch
[PASS] cache key changes with the permission fingerprint
[PASS] epoch-B lookup misses epoch-A entry
[PASS] other-identity lookup misses (no cross-permission cache leak)
check record: artifacts/qdrant/2026-10-09-qdrant-v1.12.0/checks.json (20/20 passed)
QDRANT STORE VALIDATION PASSED
```

Reproduce with:

```text
QDRANT_HOST=127.0.0.1 QDRANT_PORT=6333 \
  python3 -u scripts/validation/validate_qdrant_store.py \
  --emit-checks artifacts/qdrant/2026-10-09-qdrant-v1.12.0/checks.json
```

Redis is optional: when it is unreachable the four cache checks collapse into one
`redis_check_skipped` record, so `checks_run` legitimately becomes **17**. The
count always describes what actually ran; it is never held at a constant.

## Version compatibility

Qdrant documents that **major and minor versions of the client and server are
expected to match**, and that backward compatibility is **tested across one minor
version only**. The pair validated here is:

| Component | Version | Minor delta | Verdict |
|---|---|---|---|
| `qdrant-client` | 1.18.0 | 6 | `OUTSIDE_GUARANTEED_WINDOW` |
| `qdrant/qdrant` server | 1.12.0 | — | — |

All 20 functional checks pass, so the *observed behaviour* is sound. But an
empirical pass is not a support guarantee: any wire or API change introduced in
client 1.13–1.18 is outside what Qdrant promises for a 1.12.0 server.

- **Supported combination:** `qdrant-client` 1.12.x against
  `qdrant/qdrant:v1.12.0` (matched minor).
- **Why it was not changed here:** aligning the client is a dependency change
  beyond the scope of this evidence fix, and the gap is recorded rather than
  silently absorbed. Anyone relying on this record for a production decision
  should pin the matched pair first.
- The check is automated: `qdrant_version_compatibility()` classifies the pair
  and the contract test pins the verdict, so a future silent version drift is
  visible in the artifact itself.

## What the four VAL-STORE-001 criteria observed

1. **Active-epoch scoping** — every dense hit carries `doc_version_epoch` equal
   to the active epoch; the epoch-B filter returns only epoch-B points.
2. **RBAC removes documents before fusion** — the Python `is_document_authorized`
   re-filter yields different visible sets for two identities; role A cannot see
   role-B documents even though the Qdrant pre-filter returned them.
3. **Text and image hits come from the expected collections** — dense hits come
   from the text collection, the image hit comes from the image collection, and
   the real `rrf_fusion` merges both source sets.
4. **A rebuilt epoch does not collide** — epoch-A and epoch-B point ids are
   disjoint and both epochs coexist.

The run also observed Qdrant-unavailable degradation: the dense path logs the
failure and returns an empty result instead of crashing.

## Boundary

- **Deterministic vectors, not real models.** The adapters, epoch point ids,
  payload filters, collection separation, RBAC re-filter and RRF merge are
  validated against the real engine. BGE/CLIP/PaddleOCR model quality is not,
  and this record never claims it — see
  [the evidence map](../evidence-map.md#classification-vocabulary).
- **Single host, single node.** No Qdrant cluster, replica, HA, TLS or throughput
  measurement. Those stay `PENDING`.
- **Cache check is a key-derivation check.** It confirms the epoch and permission
  fingerprint change the cache key and that a real Redis round-trip does not leak
  across identities; it does not measure cache hit rate under load.

## Related

- [Deferred runtime validation index](../deferred-runtime-validation.md) — this
  record owns `VAL-STORE-001`.
- [Evidence map](../evidence-map.md) — the canonical evidence vocabulary.
- [Production readiness](../production-readiness.md) — the per-capability bar.
