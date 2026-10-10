"""Shared fixtures for the retrieval benchmark tests.

The fixture retriever here is a *test double*. It exists only to prove that the
metric, aggregation, artifact and provenance code is correct. Its rankings are
hand-written and are never a retrieval-quality result; artifacts produced from it
are flagged ``synthetic_retriever: true`` so this cannot be mistaken for a
benchmark.
"""

from __future__ import annotations

import json

import pytest

from benchmarks.models import BenchmarkQuery, RetrievedItem
from benchmarks.relevance import relevant_items_from_texts


@pytest.fixture(autouse=True)
def stub_corpus_probe(monkeypatch, request):
    """Keep corpus probing out of the test process by default.

    ``probe_corpus`` and the CLI's corpus-evidence collection now issue real
    Elasticsearch/Qdrant queries. That is the correct production behaviour, but a
    test whose outcome depends on whether the host happens to have a live index is
    not testing anything: it would pass or fail according to the environment.

    The corpus logic itself is covered hermetically in ``test_corpus.py``, which
    supplies fake stores. Here the probe is stubbed to the *available* state so a
    test that injects a retriever actually reaches execution — the thing those
    tests are about. A test that cares about blocking injects
    ``force_block_reason`` or blocks a backend itself.
    """
    if "real_corpus_probe" in request.keywords:
        return
    from benchmarks import backends
    from benchmarks.models import BackendAvailability

    monkeypatch.setattr(
        backends,
        "probe_corpus",
        lambda count, golden_passages=None: BackendAvailability("corpus", True, backends.REASON_OK, "stubbed in tests"),
    )
    monkeypatch.setattr(
        backends,
        "inspect_correspondence",
        lambda *args, **kwargs: _stub_inspection(),
    )
    try:
        from benchmarks import retrieval_benchmark as cli
    except Exception:  # pragma: no cover - CLI always imports in practice
        return
    monkeypatch.setattr(cli, "corpus_evidence_for", lambda passages, count: _stub_inspection().as_dict())


def _stub_inspection():
    from benchmarks.corpus import (
        CORRESPONDENCE_MET_FULL,
        CorpusFingerprint,
        CorpusInspection,
        CorrespondenceReport,
    )

    fingerprint = CorpusFingerprint("stub", "stub", 0, 0, "0" * 64, True)
    report = CorrespondenceReport(
        passages_total=0,
        passages_distinct=0,
        passages_checked=0,
        passages_matched=0,
        ratio=0.0,
        verdict=CORRESPONDENCE_MET_FULL,
        sample_hash="0" * 64,
        corpus=fingerprint,
    )
    return CorpusInspection(fingerprints=(fingerprint,), report=report)


class FixtureRetriever:
    """Deterministic ranking built from a per-sample script.

    ``script`` maps a sample id to the ordered passage texts that should be
    "retrieved". Anything not listed is returned as a distractor.
    """

    name = "fixture"

    def __init__(self, script: dict[str, list[str]], distractor: str = "无关内容") -> None:
        self.script = script
        self.distractor = distractor

    def retrieve(self, query: BenchmarkQuery, top_k: int) -> list[RetrievedItem]:
        from benchmarks.relevance import text_key

        texts = list(self.script.get(query.sample_id, []))
        texts.append(self.distractor)
        items = []
        for rank, text in enumerate(texts[:top_k], start=1):
            items.append(RetrievedItem(rank=rank, key=text_key(text), score=1.0 / rank, source="fixture", text=text))
        return items


@pytest.fixture
def perfect_ranking() -> list[str]:
    return ["alpha passage", "beta passage"]


@pytest.fixture
def make_query():
    def _make(sample_id: str, contexts: list[str], business_type: str = "regulation", difficulty: str = "easy"):
        return BenchmarkQuery(
            sample_id=sample_id,
            question=f"question {sample_id}",
            business_type=business_type,
            difficulty=difficulty,
            relevant_items=tuple(relevant_items_from_texts(contexts)),
        )

    return _make


@pytest.fixture
def mini_dataset(tmp_path):
    """A tiny on-disk golden set with the same shape as the real one."""
    rows = [
        {
            "question": "q1",
            "answer": "a1",
            "ground_truth": "g1",
            "contexts": ["alpha passage", "beta passage"],
            "business_type": "regulation",
            "difficulty": "easy",
        },
        {
            "question": "q2",
            "answer": "a2",
            "ground_truth": "g2",
            "contexts": ["gamma passage"],
            "business_type": "ingredient",
            "difficulty": "hard",
        },
        {
            "question": "q3",
            "answer": "a3",
            "ground_truth": "g3",
            "contexts": ["delta passage"],
            "business_type": "ingredient",
            "difficulty": "medium",
        },
    ]
    path = tmp_path / "mini.jsonl"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    return path
