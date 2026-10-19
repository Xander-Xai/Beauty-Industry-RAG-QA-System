"""Unit tests for the real production-retriever executors (issue #18, Blocker A).

These tests inject hand-written doubles standing in for the production
``BM25Retriever`` / ``DenseRetriever`` so the adapter contract is exercised
without Elasticsearch, Qdrant or model weights. The ranking they return is a
fixture: it proves the *adapter*, never retrieval quality.

Two properties matter most and are asserted explicitly:

* a hit's identity is the composite ``doc_id::chunk_id`` derived by
  ``relevance_key`` — never the raw text — so a chunk-boundary shift cannot
  silently zero a match; and
* an unavailable backend raises ``BenchmarkUnavailable`` instead of returning an
  empty ranking, because an all-empty ranking would let the runner record a
  false-success all-zero ``EXECUTED`` run.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from benchmarks.executors import (
    BenchmarkUnavailable,
    BM25Executor,
    DenseExecutor,
    HybridRRFExecutor,
    _items_from_hits,
    build_executor,
)
from benchmarks.models import BackendAvailability, BenchmarkQuery, RelevantItem, RetrievedItem


def _available(name: str) -> list[BackendAvailability]:
    return [BackendAvailability(name, True, "available", "test double")]


def _unavailable(name: str) -> list[BackendAvailability]:
    return [BackendAvailability(name, False, "service_unreachable", "test double")]


def _hit(doc_id: str, chunk_id: str, content: str, score: float = 1.0) -> dict:
    return {"doc_id": doc_id, "content": content, "score": score, "metadata": {"chunk_id": chunk_id}}


class FakeBM25:
    enabled = True

    def __init__(self, hits: list[dict]) -> None:
        self._hits = hits
        self.calls: list[tuple] = []

    def search(self, query: str, user_role_mask: int, user_dept_mask: int, top_k: int) -> list[dict]:
        self.calls.append((query, user_role_mask, user_dept_mask, top_k))
        return self._hits[:top_k]


class FakeDense:
    def __init__(self, hits: list[dict]) -> None:
        self._hits = hits
        self.encoded: list[str] = []
        self.search_calls: list[tuple] = []

    def encode(self, text: str):
        self.encoded.append(text)
        return np.zeros((1, 768))

    def search(self, query_embedding, qdrant_filter, top_k: int) -> list[dict]:
        self.search_calls.append((qdrant_filter, top_k))
        return self._hits[:top_k]


def _query(sample_id: str = "0000", question: str = "q") -> BenchmarkQuery:
    return BenchmarkQuery(
        sample_id=sample_id,
        question=question,
        business_type="regulation",
        difficulty="easy",
        relevant_items=tuple(),
    )


# ── identity ────────────────────────────────────────────────────────────────


def test_hit_payload_uses_composite_identity_not_text():
    hits = [_hit("doc-1", "doc-1-chunk-3", "some passage text")]
    items = _items_from_hits(hits, "bm25", top_k=10)
    assert len(items) == 1
    # Identity is doc_id::chunk_id. Two chunks of one document stay distinct, and
    # a text edit that does not move the chunk would still match.
    assert items[0].key == "doc-1::doc-1-chunk-3"
    assert items[0].text == "some passage text"


def test_hit_without_identity_is_dropped():
    hits = [{"doc_id": "", "content": "x", "score": 1.0, "metadata": {}}]
    assert _items_from_hits(hits, "bm25", top_k=10) == []


# ── fail-closed ─────────────────────────────────────────────────────────────


def test_bm25_unavailable_backend_raises_not_empty():
    executor = BM25Executor(retriever=FakeBM25([_hit("d", "c", "t")]), availability=_unavailable("bm25"))
    with pytest.raises(BenchmarkUnavailable, match="unavailable"):
        executor.retrieve(_query(), top_k=10)


def test_dense_unavailable_backend_raises_not_empty():
    executor = DenseExecutor(retriever=FakeDense([_hit("d", "c", "t")]), availability=_unavailable("dense"))
    with pytest.raises(BenchmarkUnavailable):
        executor.retrieve(_query(), top_k=10)


def test_hybrid_unavailable_backend_raises_not_empty():
    executor = HybridRRFExecutor(
        bm25=FakeBM25([_hit("d", "c", "t")]),
        dense=FakeDense([_hit("d", "c", "t")]),
        availability=_unavailable("hybrid_rrf"),
    )
    with pytest.raises(BenchmarkUnavailable):
        executor.retrieve(_query(), top_k=10)


def test_disabled_bm25_retriever_raises_not_empty():
    retriever = FakeBM25([_hit("d", "c", "t")])
    retriever.enabled = False
    executor = BM25Executor(retriever=retriever, availability=_available("bm25"))
    with pytest.raises(BenchmarkUnavailable, match="disabled"):
        executor.retrieve(_query(), top_k=10)


# ── production passthrough ──────────────────────────────────────────────────


def test_bm25_uses_full_access_masks_and_returns_keyed_items():
    retriever = FakeBM25([_hit("doc-1", "c0", "a", 3.0), _hit("doc-2", "c1", "b", 2.0)])
    executor = BM25Executor(retriever=retriever, availability=_available("bm25"))
    items = executor.retrieve(_query(question="hello"), top_k=10)

    assert [item.key for item in items] == ["doc-1::c0", "doc-2::c1"]
    assert [item.source for item in items] == ["bm25", "bm25"]
    query_text, role_mask, dept_mask, top_k = retriever.calls[0]
    assert query_text == "hello"
    # Both masks are the configured super-admin mask: a benchmark measures
    # retrieval quality, not RBAC.
    assert role_mask == dept_mask == 0xFFFFFFFF
    assert top_k == 10


def test_dense_encodes_the_question_before_searching():
    retriever = FakeDense([_hit("doc-9", "c5", "x", 0.9)])
    executor = DenseExecutor(retriever=retriever, availability=_available("dense"))
    items = executor.retrieve(_query(question="encode me"), top_k=10)

    assert retriever.encoded == ["encode me"]
    assert [item.key for item in items] == ["doc-9::c5"]
    assert items[0].source == "dense"


def test_hybrid_fuses_both_paths_with_production_rrf():
    bm25 = FakeBM25([_hit("doc-1", "c0", "a"), _hit("doc-2", "c1", "b")])
    dense = FakeDense([_hit("doc-2", "c9", "b"), _hit("doc-3", "c2", "c")])
    executor = HybridRRFExecutor(
        bm25=bm25,
        dense=dense,
        availability=_available("hybrid_rrf"),
        k=60,
        weights={"w_text": 1.0},
    )
    items = executor.retrieve(_query(question="q"), top_k=10)

    # doc-2 appears in both paths, so it must fuse to the top by RRF score.
    assert items[0].key.startswith("doc-2::")
    assert {item.key.split("::")[0] for item in items} == {"doc-1", "doc-2", "doc-3"}
    assert all(item.source == "hybrid_rrf" for item in items)


def test_build_executor_rejects_unimplemented_config():
    with pytest.raises(BenchmarkUnavailable, match="no real retrieval executor"):
        build_executor("hybrid_rrf_biencoder_crossencoder")


# ── CLI wiring: a real executor is only wired when the stack answered ────────


def _run_dir(out):
    return sorted(path for path in out.iterdir() if path.is_dir())[-1]


def test_cli_executor_real_stays_blocked_when_backends_unavailable(tmp_path, monkeypatch):
    from benchmarks import backends
    from benchmarks import retrieval_benchmark as cli

    monkeypatch.setattr(cli, "git_provenance", lambda root: ("abc123", False))
    # Force every configuration unavailable, so the outcome is deterministic on a
    # host that has (or lacks) a live stack.
    monkeypatch.setattr(
        backends,
        "evaluate_config",
        lambda name, count, probed=None, golden_passages=None: backends.ConfigAvailability(
            name, False, ("bm25=service_unreachable",), "test double"
        ),
    )
    exit_code = cli.main(
        [
            "--config",
            "bm25",
            "--executor",
            "real",
            "--limit",
            "2",
            "--dataset",
            "tests/evaluation/golden_set.jsonl",
            "--output-dir",
            str(tmp_path),
            "--allow-dirty",
        ]
    )
    assert exit_code == 0
    metadata = json.loads((_run_dir(tmp_path) / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["executor"] == "real"
    # No real executor was wired, because no backend passed its probe.
    assert metadata["real_executor_configs"] == []
    assert metadata["results_are_benchmark"] is False
    summary = json.loads((_run_dir(tmp_path) / "retrieval_metrics.json").read_text(encoding="utf-8"))
    assert summary["executed_configs"] == []
    assert "unavailable backends" in summary["configs"]["bm25"]["reason"]


def test_cli_default_executor_is_none(tmp_path, monkeypatch):
    from benchmarks import retrieval_benchmark as cli

    monkeypatch.setattr(cli, "git_provenance", lambda root: ("abc123", False))
    cli.main(
        [
            "--config",
            "bm25",
            "--limit",
            "1",
            "--dataset",
            "tests/evaluation/golden_set.jsonl",
            "--output-dir",
            str(tmp_path),
            "--allow-dirty",
        ]
    )
    metadata = json.loads((_run_dir(tmp_path) / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["executor"] == "none"
    assert metadata["real_executor_configs"] == []
    assert metadata["results_are_benchmark"] is False


# ── a declared-real factory is not mislabelled synthetic ─────────────────────


class _OneHitRetriever:
    name = "bm25"

    def retrieve(self, query, top_k):
        return [RetrievedItem(rank=1, key=query.relevant_items[0].key, score=1.0, source="bm25", text="")]


def _one_query():
    item = RelevantItem(key="doc-1::chunk-1", text="passage")
    return [
        BenchmarkQuery(
            sample_id="0000",
            question="q",
            business_type="general",
            difficulty="easy",
            relevant_items=(item,),
        )
    ]


def test_a_declared_real_factory_run_is_not_synthetic():
    """``--executor real`` must not be labelled "synthetic fixture retriever".

    The runner assumed every injected factory was a test fixture, so a genuine
    ``--executor real`` run would carry ``synthetic_retriever: true`` and
    ``results_are_benchmark: false`` even with a real corpus — unpublishable by label.
    """
    from benchmarks.runner import run_configuration

    run = run_configuration(
        "bm25",
        _one_query(),
        retriever_factory=lambda name: _OneHitRetriever(),
        synthetic_retriever=False,
    )

    assert run.executed
    assert run.synthetic is False
    assert run.outcome.reason != "synthetic fixture retriever (not a benchmark)"


def test_an_injected_fixture_factory_stays_synthetic_by_default():
    from benchmarks.runner import run_configuration

    run = run_configuration("bm25", _one_query(), retriever_factory=lambda name: _OneHitRetriever())

    assert run.synthetic is True
    assert run.outcome.reason == "synthetic fixture retriever (not a benchmark)"
