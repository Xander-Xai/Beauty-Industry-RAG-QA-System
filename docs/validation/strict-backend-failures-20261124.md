# Strict retrieval failure semantics (2026-11-24)

Earlier-employer Beauty RAG reference follow-up, refs #18 and #125. Real benchmark adapters use strict BM25 and Dense retrievers. Backend failures, failed ES epoch binding, unverified epoch probes, ES timed-out/failed-shard responses and dense search exceptions are recorded as failed queries rather than successful empty rankings. A successful empty response remains a legitimate miss. Hybrid and rerank arms inherit the same strict retrievers. Normal online retriever defaults retain their existing degradation behavior. Injected test doubles remain supported.

Validation: `pytest -q tests/benchmark/test_strict_backend_failures.py tests/benchmark/test_executors.py`. Requires exact-head CI; no actual ES/Qdrant/model measurements are claimed. This does not certify index immutability or live cluster resilience.
