# Beauty Industry RAG — Memory without turning history into regulatory evidence

> Dated engineering hardening document (2026-11-24). It must not be presented
> as proof that later patches were live at the earlier employer.

## Company and timeline
This RAG repository and `Customer-Service-AI-Agent` are projects from the
**same earlier employer**. `Gaobao-Advisor` is a newer project from a
**different employer**. Keep performance evidence and chronology separate.

## Why Memory is deliberately lightweight here

| Type | Correct source | State / target |
|---|---|---|
| Working memory | `RequestContext` | Current query, authenticated identity/masks, rewrite, evidence, request-scoped answer checks |
| Short session memory | owner-scoped `SessionState` | Recent six rounds for resolving "it"/"that ingredient" in Query Rewrite |
| Evidence memory | independently retrieved approved current docs + metadata | Every query revalidates permission, active knowledge epoch and evidence, **not old summary facts** |
| Procedural | `AGENTS.md`, code and regression tests | Permission/epoch/owner/cache isolation rules; not an LLM-managed factual store |

**Not assigned:** general purpose Pi/OpenCode coding-session branching, long
personal-profile memory, automatic learning of regulatory thresholds. Adding
such mechanisms here could let low-trust history contaminate the source of
truth and would not improve this project's RAG specialization.

## Confirmed repair and tests

[PR #126](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/pull/126)
addresses two source-verified defects: answer cache lookup happened before
Rewrite established `requires_context`; contextual cache keys contained
`session_id` but not owner. Repair: read after rewrite; bind contextual
cache to owner, session and latest query/answer history fingerprint, alongside
role/dept mask and knowledge epoch. Unauthenticated/unscoped contextual
answers bypass cache rather than falling back to globally shared entries.

Regression scenarios: same session ID across two users; same user with a new
referent; identical context retry; missing owner; rewrite failure; altered
epoch and source authorization. The newest mock tests do not prove production
isolation or factual quality on actual company records.

## Next memory-only acceptance work

- Multi-turn reference accuracy using a small, permission-safe, human-verified
  set; include an explicit "not enough information" case when previous referent
  is missing or no longer authorized.
- Simulate user role change, source revocation and knowledge-epoch rollover
  between turns. History may aid query formulation, never bypass retrieval
  gates or source lifecycle checks.
- Check Redis session TTL, owner scoping, key rotation/erasure, cache lineage.
- Compare answer evidence support and false-positive citations with and
  without short-session context. Report measured values only.

Pi (conceptual reference): https://docs.pi.dev/latest/compaction
OpenCode project instructions: https://opencode.ai/docs/instructions/
