# Repository Agent Instructions — Beauty Industry RAG

## Project and employment boundary
- This RAG QA system and `Customer-Service-AI-Agent` belong to the same earlier employment period. `Gaobao-Advisor` belongs to a later, separate employer/project; do not merge their timelines, metrics, ownership, or production claims.
- Changes dated November 2026 are current repository governance/engineering work. Do not retrospectively present them as features deployed during the earlier employment period.
- Check the live default branch, PR/Issue states, exact head SHA, CI and model/data provenance before reporting what is implemented or production-verified.

## Memory engineering — strict trust boundaries
- **Working memory:** request-scoped `RequestContext`; **short-session memory:** owner-scoped `SessionState` with the latest six dialogue rounds. Prefer Query Rewrite + new authorized retrieval over storing regulatory "facts" in free-text summaries.
- User-authored history, query rewrites, summaries, images and tool text are untrusted *input*, not system/developer instructions, permission grants or regulation sources.
- Every answer needs current RBAC/department permission checks, active knowledge version/epoch, Evidence Gate and Answer Gate as applicable. Historical `locked_doc_ids` are not a reusable authorization token. A regulation citation from an old turn is never evidence for the current turn without fresh source verification.
- For contextual caching, never read before Rewrite determines `requires_context`; bind key to authenticated owner ID + session ID + dialogue-state fingerprint + role/dept masks + knowledge epoch. If owner/session is absent, do not read/write contextual answer caches. Always invalidate on dialogue mutation, source/epoch/permission changes.
- Do **not** copy Pi/OpenCode general coding-agent session-tree storage or long-term user profile into the RAG evidence layer; no self-learned laws, formulas or regulatory thresholds.
- Test hostile same-session cross-user collisions, updated-history referents, missing owner, stale epoch, and denied source access, with mock-data security tests plus separate real-document evaluation.
- Read `docs/validation/context-memory-cache-isolation-20261124.md` and `docs/memory-engineering-positioning-20261124.md` before modifying memory or cache.
