"""Relevance matching must stay deterministic and conservative."""

from __future__ import annotations

import pytest

from benchmarks.dataset import DatasetError, detect_relevance_level, load_queries
from benchmarks.models import RetrievedItem
from benchmarks.relevance import (
    dedupe_preserving_rank,
    normalize_text,
    relevant_items_from_texts,
    stable_id_from,
    text_key,
)


def test_normalization_is_whitespace_and_unicode_only():
    assert normalize_text("  a  b  ") == "a b"
    assert normalize_text("a\t\nb") == "a b"
    # NFKC folds compatibility characters; no case folding, no punctuation removal
    assert normalize_text("ＡＢＣ") == "ABC"
    assert normalize_text("Hello, World.") == "Hello, World."


def test_text_key_is_stable_across_whitespace():
    assert text_key("alpha  passage") == text_key("alpha passage")


def test_stable_id_preferred_when_present():
    assert stable_id_from({"doc_id": "d1", "chunk_id": "c1"}) == "d1"
    assert stable_id_from({"chunk_id": "c1"}) == "c1"
    assert stable_id_from({}) is None


def test_duplicate_ground_truth_is_collapsed():
    items = relevant_items_from_texts(["x passage", "x passage", "y passage"])
    assert [item.key for item in items] == [text_key("x passage"), text_key("y passage")]


def test_empty_ground_truth_is_rejected():
    with pytest.raises(ValueError):
        relevant_items_from_texts(["", "   "])


def test_dedupe_preserving_rank_renumbers():
    items = [
        RetrievedItem(rank=1, key="a", score=1.0, source="s"),
        RetrievedItem(rank=2, key="a", score=0.9, source="s"),
        RetrievedItem(rank=3, key="b", score=0.8, source="s"),
    ]
    deduped = dedupe_preserving_rank(items)
    assert [item.key for item in deduped] == ["a", "b"]
    assert [item.rank for item in deduped] == [1, 2]


def test_relevance_level_is_level2_for_text_only_dataset(mini_dataset):
    rows = [line for line in mini_dataset.read_text().splitlines() if line.strip()]
    import json

    parsed = [json.loads(line) for line in rows]
    assert detect_relevance_level(parsed) == "level2_normalized_exact_text"


def test_load_queries_assigns_deterministic_sample_ids(mini_dataset):
    first = load_queries(mini_dataset)
    second = load_queries(mini_dataset)
    assert [q.sample_id for q in first] == ["0000", "0001", "0002"]
    assert [q.sample_id for q in first] == [q.sample_id for q in second]


def test_load_queries_rejects_sample_without_contexts(tmp_path):
    import json

    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps({"question": "q", "contexts": []}) + "\n", encoding="utf-8")
    with pytest.raises(DatasetError):
        load_queries(path)
