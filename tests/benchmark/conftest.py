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
