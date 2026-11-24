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

## Reporting current project status

1. Read the live repository metadata for the default branch (currently `main`). Resolve its remote head SHA, then read the README, [implementation reference](docs/architecture-baseline.md), relevant source, tests and [evidence](docs/evidence-map.md) at that exact SHA. Report the branch, SHA and check time. A local checkout may lag the remote.
2. Read current PR and Issue metadata from GitHub when reporting their state. Use the PR's actual base, draft flag and merged status. Verify that a claimed merge is present in the default branch before counting it as delivered. Use PR bodies, archived reports and conversation notes for historical context.
3. Check Actions for the exact SHA, and state what ran, passed, failed, was skipped or is pending. Report release/tag and deployment evidence separately. When a source is unavailable, say which claim could not be verified.
4. State the environment and dataset behind each finding. Distinguish implemented code, test evidence, measured results and production validation. Keep public-repository evidence separate from personal or employment history.
5. When changing a feature or interface, update its current documentation and existing generated contracts in the same PR. Keep dated audits labelled with the snapshot date.

## Architecture change gate
- Follow [architecture decision freeze](docs/architecture-baseline.md#architecture-decision-freeze--2026-11-25). Keep the modular monolith canonical; do not add Agent frameworks or duplicate service paths. Reranker simplification requires matched-corpus single-CE/dual-CE and BiEncoder ablation, safety-gate regression and measured latency; no evidence downgrade or untested deletion. Cache lookup follows Rewrite and owner/permission/epoch checks.
