"""Real production-retriever adapters for the retrieval benchmark CLI.

Why this exists
---------------
``benchmarks/runner.py`` defines a minimal :class:`~benchmarks.runner.Retriever`
protocol (``retrieve(query, top_k) -> list[RetrievedItem]``). Until now only
tests supplied one, so a CLI run always stopped with
``no real retrieval executor is wired for this configuration yet`` even when
every backend probe passed (issue #18, Blocker A).

This module supplies that executor. Each adapter calls the **production**
retrieval code path — ``retrieval.bm25_retriever.BM25Retriever``,
``retrieval.dense_retriever.DenseRetriever`` and the production
``retrieval_service.rerank.rrf_fusion.rrf_fusion`` — and converts its output into
:class:`~benchmarks.models.RetrievedItem` objects whose ``key`` is derived by
:func:`benchmarks.relevance.relevance_key`. Ground truth and retrieved chunks
therefore agree on what "the same passage" means; identity is never re-invented
here.

Fail-closed
-----------
Production retrievers swallow backend errors and return ``[]``. A down
Elasticsearch and a genuine no-match are indistinguishable from the return value
alone. Returning ``[]`` for every query would let the runner record an all-zero
``EXECUTED`` run — a false success that looks like a measurement. Every adapter
therefore re-validates the backends it depends on and raises
:class:`~benchmarks.backends.BenchmarkUnavailable` (a per-query failure the
runner records) rather than emitting an empty ranking. The runner turns a run in
which every query failed into ``BLOCKED``.

Authorisation
-------------
A benchmark measures retrieval quality, not access control, and the benchmark
protocol carries no user identity. The adapters query with the configured
``super_admin_mask`` for both the role and department masks, so the production
RBAC filters do not remove ground-truth candidates. This is the same identity the
production paths treat as full-access; it is recorded in the artifact via
``effective_config`` so a reader can see which identity was used.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from types import SimpleNamespace
from typing import Any, Protocol

from benchmarks import backends
from benchmarks.models import BenchmarkQuery, RetrievedItem
from benchmarks.relevance import relevance_key

EXECUTABLE_CONFIGS = (
    "bm25",
    "dense",
    "hybrid_rrf",
    "hybrid_rrf_biencoder",
    "hybrid_rrf_biencoder_crossencoder",
    "hybrid_rrf_crossencoder_a",
    "hybrid_rrf_crossencoder_b",
    "hybrid_rrf_crossencoder_dual",
    "hybrid_rrf_biencoder_crossencoder_a",
    "hybrid_rrf_biencoder_crossencoder_b",
)
HYBRID_RECALL_DEPTH = 100
BIENCODER_KEEP = 150

#: RRF path names, matching ``retrieval/parallel_recall.py`` exactly. They must
#: agree with the weight keys or a path's weight is silently ignored.
BM25_PATH = "bm25_es"
DENSE_PATH = "dense_bge"


class BenchmarkUnavailable(backends.BenchmarkUnavailable):
    """Raised when a real executor cannot run because a backend is unavailable."""


class _ProductionBM25(Protocol):
    def search(self, query: str, user_role_mask: int, user_dept_mask: int, top_k: int) -> list[dict]: ...


class _ProductionDense(Protocol):
    def encode(self, text: str) -> Any: ...

    def search(self, query_embedding: Any, qdrant_filter: Any, top_k: int) -> list[dict]: ...


_Availability = Sequence[Any]
_AvailabilityFn = Callable[[str], _Availability]


def _default_availability(config_name: str, golden_passages: Sequence[str] | None) -> _Availability:
    return backends.probe_config_backends(config_name, len(golden_passages or ()), golden_passages)


def _super_admin_masks() -> tuple[int, int]:
    """The configured full-access role/department masks, as integers."""
    from common.config import get_config

    mask = int(get_config().rbac.super_admin_mask)
    return mask, mask


def _check_available(config_name: str, availability: _Availability) -> None:
    """Raise when any required backend did not answer a real probe."""
    unavailable = [item for item in availability if not getattr(item, "available", False)]
    if unavailable:
        reasons = "; ".join(f"{item.name}={item.reason}" for item in unavailable)
        raise BenchmarkUnavailable(f"{config_name} backends unavailable: {reasons}")


def _hit_payload(hit: dict) -> dict[str, Any]:
    """Normalise a production hit into the payload ``relevance_key`` reads.

    Production returns ``doc_id`` at the top level and ``chunk_id`` nested under
    ``metadata``. ``relevance_key`` reads both off one mapping, so the nesting is
    flattened here. The key is never derived from the raw text: a chunk that
    shifted by one character must not silently fail to match.
    """
    metadata = hit.get("metadata") or {}
    return {
        "doc_id": hit.get("doc_id"),
        "chunk_id": metadata.get("chunk_id"),
        "text": hit.get("content") or "",
    }


def _items_from_hits(hits: Sequence[dict], source: str, top_k: int) -> list[RetrievedItem]:
    items: list[RetrievedItem] = []
    for rank, hit in enumerate(hits[:top_k], start=1):
        payload = _hit_payload(hit)
        key = relevance_key(payload)
        if not key:
            # A hit with no identity can never match ground truth and would only
            # inflate the ranking position of the items after it. Dropping it is
            # honest; keeping it would make a match appear at a worse rank.
            continue
        items.append(
            RetrievedItem(
                rank=rank,
                key=key,
                score=float(hit.get("score") or 0.0),
                source=source,
                text=str(payload.get("text") or ""),
            )
        )
    return items


class BM25Executor:
    """Benchmark ``Retriever`` over the production ``BM25Retriever``."""

    name = "bm25"
    #: Provenance marker read by ``benchmarks/runner.py``: this retriever calls the
    #: production code path, so a run that used it is **not** a synthetic fixture.
    #: A test double simply omits it and is therefore treated as synthetic.
    is_production = True

    def __init__(
        self,
        *,
        retriever: _ProductionBM25 | None = None,
        availability: _Availability | None = None,
        golden_passages: Sequence[str] | None = None,
    ) -> None:
        self._retriever = retriever
        self._availability = list(availability) if availability is not None else None
        self._golden_passages = list(golden_passages or ())

    def _ensure_available(self) -> None:
        if self._availability is None:
            self._availability = list(_default_availability(self.name, self._golden_passages))
        _check_available(self.name, self._availability)

    def _ensure_retriever(self) -> _ProductionBM25:
        if self._retriever is None:
            from retrieval.bm25_retriever import BM25Retriever

            self._retriever = BM25Retriever(strict=True)
        return self._retriever

    def retrieve(self, query: BenchmarkQuery, top_k: int) -> list[RetrievedItem]:
        self._ensure_available()
        retriever = self._ensure_retriever()
        # A retriever the production client disabled (unreachable/misconfigured)
        # must not be read as "no matches".
        if getattr(retriever, "enabled", True) is False:
            raise BenchmarkUnavailable(f"{self.name} retriever is disabled")
        role_mask, dept_mask = _super_admin_masks()
        hits = retriever.search(query.question, role_mask, dept_mask, top_k)
        return _items_from_hits(hits, self.name, top_k)


class DenseExecutor:
    """Benchmark ``Retriever`` over the production ``DenseRetriever`` + RRF path."""

    name = "dense"
    #: Provenance marker read by ``benchmarks/runner.py`` (see ``BM25Executor``).
    is_production = True

    def __init__(
        self,
        *,
        retriever: _ProductionDense | None = None,
        availability: _Availability | None = None,
        golden_passages: Sequence[str] | None = None,
    ) -> None:
        self._retriever = retriever
        self._availability = list(availability) if availability is not None else None
        self._golden_passages = list(golden_passages or ())

    def _ensure_available(self) -> None:
        if self._availability is None:
            self._availability = list(_default_availability(self.name, self._golden_passages))
        _check_available(self.name, self._availability)

    def _ensure_retriever(self) -> _ProductionDense:
        if self._retriever is None:
            from retrieval.dense_retriever import DenseRetriever

            self._retriever = DenseRetriever(strict=True)
        return self._retriever

    def _qdrant_filter(self) -> Any:
        from common.auth import build_qdrant_filter
        from common.config import get_config_dict

        epoch = str(get_config_dict().get("knowledge_version_epoch") or "")
        role_mask, dept_mask = _super_admin_masks()
        # Production passes a status/epoch filter here; RBAC is re-applied in
        # Python after fusion. The admin masks keep the status/epoch semantics
        # while removing no candidate for authorisation reasons.
        return build_qdrant_filter(role_mask, dept_mask, epoch)

    def retrieve(self, query: BenchmarkQuery, top_k: int) -> list[RetrievedItem]:
        self._ensure_available()
        retriever = self._ensure_retriever()
        embedding = retriever.encode(query.question)
        hits = retriever.search(embedding, self._qdrant_filter(), top_k)
        return _items_from_hits(hits, self.name, top_k)


class HybridRRFExecutor:
    """Benchmark ``Retriever`` over production BM25 + dense fused by ``rrf_fusion``.

    The fusion itself is the production function, called with the configured RRF
    constant and weights, so the benchmark measures the same fusion the request
    path runs rather than a re-implementation.

    Known measurement boundary: production RRF ranks *documents* (it keeps the
    best-ranked chunk per source per path), while the golden set's ground truth
    is chunk-level. A fused item therefore carries the surviving chunk's
    identity, and a document that is retrieved but whose surviving chunk is not
    the annotated one counts as a miss. That is faithful to production and is
    recorded here rather than smoothed over.
    """

    name = "hybrid_rrf"
    #: Provenance marker read by ``benchmarks/runner.py`` (see ``BM25Executor``).
    is_production = True

    def __init__(
        self,
        *,
        bm25: _ProductionBM25 | None = None,
        dense: _ProductionDense | None = None,
        availability: _Availability | None = None,
        golden_passages: Sequence[str] | None = None,
        k: int | None = None,
        weights: dict[str, float] | None = None,
    ) -> None:
        self._bm25 = bm25
        self._dense = dense
        self._availability = list(availability) if availability is not None else None
        self._golden_passages = list(golden_passages or ())
        self._k = k
        self._weights = weights

    def _ensure_available(self) -> None:
        if self._availability is None:
            self._availability = list(_default_availability(self.name, self._golden_passages))
        _check_available(self.name, self._availability)

    def _ensure_retrievers(self) -> tuple[_ProductionBM25, _ProductionDense]:
        if self._bm25 is None:
            from retrieval.bm25_retriever import BM25Retriever

            self._bm25 = BM25Retriever(strict=True)
        if self._dense is None:
            from retrieval.dense_retriever import DenseRetriever

            self._dense = DenseRetriever(strict=True)
        return self._bm25, self._dense

    def _rrf_settings(self) -> tuple[int, dict[str, float] | None]:
        if self._k is not None:
            return self._k, self._weights
        from common.config import get_config_dict

        rrf_config = (get_config_dict().get("retrieval") or {}).get("rrf") or {}
        return int(rrf_config.get("k", 60)), rrf_config.get("weights") or None

    def _fused(self, query: BenchmarkQuery, top_k: int) -> list:
        self._ensure_available()
        bm25, dense = self._ensure_retrievers()
        from common.models import RecallResult
        from retrieval_service.rerank.rrf_fusion import rrf_fusion

        role_mask, dept_mask = _super_admin_masks()
        depth = max(top_k, HYBRID_RECALL_DEPTH)
        bm25_hits = bm25.search(query.question, role_mask, dept_mask, depth)
        dense_hits = dense.search(dense.encode(query.question), _dense_filter(), depth)

        def _to_results(hits: Sequence[dict], source: str) -> list[RecallResult]:
            return [
                RecallResult(
                    doc_id=str(hit.get("doc_id") or ""),
                    content=str(hit.get("content") or ""),
                    score=float(hit.get("score") or 0.0),
                    source=source,
                    metadata=hit.get("metadata") or {},
                )
                for hit in hits
                if hit.get("doc_id")
            ]

        k, weights = self._rrf_settings()
        fused = rrf_fusion(
            {
                BM25_PATH: _to_results(bm25_hits, BM25_PATH),
                DENSE_PATH: _to_results(dense_hits, DENSE_PATH),
            },
            k=k,
            weights=weights,
        )
        return fused

    def retrieve(self, query: BenchmarkQuery, top_k: int) -> list[RetrievedItem]:
        fused = self._fused(query, top_k)
        items: list[RetrievedItem] = []
        for rank, result in enumerate(fused[:top_k], start=1):
            key = relevance_key(
                {
                    "doc_id": result.doc_id,
                    "chunk_id": (result.metadata or {}).get("chunk_id"),
                    "text": result.content,
                }
            )
            if not key:
                continue
            items.append(
                RetrievedItem(
                    rank=rank,
                    key=key,
                    score=float(result.score or 0.0),
                    source=self.name,
                    text=result.content,
                )
            )
        return items


class SingleCrossEncoderReranker:
    """Strict offline challenger: one model, bounded synchronous batches, no background worker."""

    def __init__(self, config_name: str, *, model=None):
        from common.config import get_config_dict

        self.model_key = "cross_encoder_" + config_name[-1]
        config = get_config_dict()
        max_len = int(config["retrieval"]["cross_encoder"]["max_seq_length"])
        self.batch_size = int(config["gpu1"]["rerank_batch_aggregator"]["max_batch_size"])
        if self.batch_size < 1:
            raise ValueError("invalid CrossEncoder batch size")
        if model is None:
            from sentence_transformers import CrossEncoder

            path = backends.rerank_model_paths(config_name)[self.model_key]
            model = CrossEncoder(path, max_length=max_len)
        self.model = model

    def rerank(self, query, candidates, top_k, *, ctx, strict):
        if not strict or not candidates:
            raise BenchmarkUnavailable("single CE requires strict nonempty candidates")
        pairs = [(query, row.content) for row in candidates]
        scores = []
        for offset in range(0, len(pairs), self.batch_size):
            batch = pairs[offset : offset + self.batch_size]
            raw = self.model.predict(batch, batch_size=len(batch))
            values = raw.tolist() if hasattr(raw, "tolist") else list(raw)
            if len(values) != len(batch):
                raise BenchmarkUnavailable("invalid single CrossEncoder scores")
            try:
                values = [float(value) for value in values]
            except (TypeError, ValueError) as exc:
                raise BenchmarkUnavailable("invalid single CrossEncoder scores") from exc
            if not all(math.isfinite(value) for value in values):
                raise BenchmarkUnavailable("invalid single CrossEncoder scores")
            scores.extend(values)
        score_field = "ce_score_a" if self.model_key.endswith("_a") else "ce_score_b"
        for row, score in zip(candidates, scores, strict=True):
            setattr(row, score_field, score)
        ctx.ce_stage_provenance = self.model_key
        return sorted(candidates, key=lambda row: getattr(row, score_field), reverse=True)[:top_k]


class RerankExecutor(HybridRRFExecutor):
    """Use the production rerankers; a model error is a failed query, never a ranking."""

    def __init__(self, *, cross: bool = False, biencoder=None, crossencoder=None, config_name=None, **kwargs):
        super().__init__(**kwargs)
        self.name = config_name or ("hybrid_rrf_biencoder_crossencoder" if cross else "hybrid_rrf_biencoder")
        self._cross = "_crossencoder" in self.name
        self._use_bi = "_biencoder" in self.name
        self._single = self.name[-1] if self.name.endswith(("_a", "_b")) else None
        self._biencoder = biencoder
        self._crossencoder = crossencoder
        self._single_reranker = None
        # Dependency-injected fixture models are not real-model evidence.
        self.is_production = biencoder is None and crossencoder is None and self._bm25 is None and self._dense is None

    def retrieve(self, query: BenchmarkQuery, top_k: int) -> list[RetrievedItem]:
        fused = self._fused(query, top_k)
        if not fused:
            raise BenchmarkUnavailable("no candidates to verify rerank execution")
        retained = max(top_k, BIENCODER_KEEP) if self._cross else top_k
        if self._use_bi:
            if self._biencoder is None:
                from retrieval.bi_encoder import BiEncoderReranker

                self._biencoder = BiEncoderReranker()
            ranked = self._biencoder.rerank(query.question, fused, top_k=retained, strict=True)
            if len(ranked) != min(retained, len(fused)):
                raise BenchmarkUnavailable("incomplete BiEncoder output")
        else:
            from retrieval.bi_encoder import _to_rerank_result

            ranked = [_to_rerank_result(row, bi_score=0.0) for row in fused[:retained]]
        score_field = "bi_score"
        if self._single:
            if self._single_reranker is None:
                self._single_reranker = SingleCrossEncoderReranker(self.name, model=self._crossencoder)
            ctx = SimpleNamespace()
            expected_count = min(top_k, len(ranked))
            ranked = self._single_reranker.rerank(query.question, ranked, top_k, ctx=ctx, strict=True)
            if (
                getattr(ctx, "ce_stage_provenance", None) != "cross_encoder_" + self._single
                or len(ranked) != expected_count
            ):
                raise BenchmarkUnavailable("single CrossEncoder execution not verified")
            score_field = "ce_score_" + self._single
        elif self._cross:
            if self._crossencoder is None:
                from retrieval.cross_encoder_ensemble import CrossEncoderEnsemble

                self._crossencoder = CrossEncoderEnsemble()
            ctx = SimpleNamespace()
            expected_count = min(top_k, len(ranked))
            ranked = self._crossencoder.rerank(query.question, ranked, top_k=top_k, ctx=ctx, strict=True)
            if getattr(ctx, "ce_stage_provenance", None) != "cross_encoder" or len(ranked) != expected_count:
                raise BenchmarkUnavailable("CrossEncoder execution not verified")
            score_field = "ce_score_ensemble"
        items = []
        candidates = {(row.doc_id, (row.metadata or {}).get("chunk_id"), row.content) for row in fused}
        for rank, row in enumerate(ranked[:top_k], 1):
            chunk_id = (row.metadata or {}).get("chunk_id")
            key = relevance_key({"doc_id": row.doc_id, "chunk_id": chunk_id, "text": row.content})
            score = float(getattr(row, score_field))
            if not key or not math.isfinite(score) or (row.doc_id, chunk_id, row.content) not in candidates:
                raise BenchmarkUnavailable("rerank result identity or score invalid")
            items.append(RetrievedItem(rank=rank, key=key, score=score, source=self.name, text=row.content))
        if len({item.key for item in items}) != len(items):
            raise BenchmarkUnavailable("duplicate rerank identity")
        return items


def _dense_filter() -> Any:
    from common.auth import build_qdrant_filter
    from common.config import get_config_dict

    epoch = str(get_config_dict().get("knowledge_version_epoch") or "")
    role_mask, dept_mask = _super_admin_masks()
    return build_qdrant_filter(role_mask, dept_mask, epoch)


def build_executor(config_name: str, golden_passages: Sequence[str] | None = None) -> Any:
    """Build the real executor for one configuration.

    Raises :class:`BenchmarkUnavailable` for a configuration without a real
    executor, so a rerank configuration cannot silently fall back to an
    unimplemented path.
    """
    if config_name == "bm25":
        return BM25Executor(golden_passages=golden_passages)
    if config_name == "dense":
        return DenseExecutor(golden_passages=golden_passages)
    if config_name == "hybrid_rrf":
        return HybridRRFExecutor(golden_passages=golden_passages)
    if config_name in EXECUTABLE_CONFIGS:
        return RerankExecutor(config_name=config_name, golden_passages=golden_passages)
    raise BenchmarkUnavailable(
        f"no real retrieval executor is implemented for configuration {config_name!r}; "
        f"executable configurations are {', '.join(EXECUTABLE_CONFIGS)}"
    )


def real_retriever_factory(
    golden_passages: Sequence[str] | None = None,
) -> Callable[[str], Any]:
    """Factory in the shape ``run_configuration`` expects.

    ``golden_passages`` is forwarded so the corpus correspondence probe inside
    each adapter's availability check measures the same passages the run scores.
    """

    def _factory(config_name: str) -> Any:
        return build_executor(config_name, golden_passages=golden_passages)

    return _factory
