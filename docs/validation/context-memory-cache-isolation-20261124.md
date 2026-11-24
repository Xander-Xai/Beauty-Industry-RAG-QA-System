# Contextual RAG cache isolation — 2026-11-24

## Confirmed pre-fix issue

`OnlineRAGPipeline.process` read its answer cache **before** Query Rewrite
assigned `ctx.rewrite_result`. A typical incoming request initially had
`rewrite_result=None`, so the pre-rewrite read assumed
`requires_context=False`. Later successful writes used the post-rewrite
`requires_context` flag and optionally scoped by **session ID alone**.

A caller-chosen or duplicated session ID with identical role/dept masks
could therefore produce a cross-principal cache hit for a contextual answer.
Owner-scoped `SessionState` and document-level permission filtering did not
protect this separate cache key.

## This patch

1. Rewrite/complexity complete before any answer-cache read.
2. Cache lookup uses the rewritten query and final `requires_context`.
3. Context-dependent answers are scoped to **owner ID + session ID + existing
   role/dept mask, knowledge epoch and prompt/schema version**.
4. No authenticated owner or session: skip contextual cache **read and write**,
   rather than falling back to a globally shared key.
5. Rewrite fallback: preserve the existing no-cache policy.
6. Keep context-independent queries shareable within their permission scope;
   do not weaken the existing Evidence Gate, Answer Gate or cache-epoch key.

## Repro and acceptance

```bash
pytest -q tests/test_context_memory_cache_scope.py
pytest -q tests/test_pipeline_ordering.py tests/test_pipeline_context.py
```

The new tests use fake LLM, fake cache and the existing pipeline mocks:
- same session, query and role/dept, different owner → distinct answer;
- same owner and session → cached answer can be reused;
- old globally cached raw-query answer cannot short-circuit Rewrite;
- anonymous contextual request cannot hit/write shared cache;
- rewrite failure skips cache read and write.

## Boundary

This is **not** long-term user memory or a live multi-tenant privacy audit.
`requires_context` still comes from the rewriter: incorrect context
classification needs a separate negative-case evaluation and conservative
fallback. No live/real permission data or provider tokens are used in tests.
