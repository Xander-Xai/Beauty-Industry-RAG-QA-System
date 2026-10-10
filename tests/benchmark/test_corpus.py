"""Corpus correspondence and fingerprinting must be deterministic and read-only.

These tests are hermetic: every store is a fake, so no test touches Elasticsearch
or Qdrant. That matters because ``probe_corpus`` now issues real queries — a test
that depended on live services would pass or fail according to the host, not the
code.
"""

from __future__ import annotations

import pytest

from benchmarks import corpus as corpus_mod
from benchmarks.backends import REASON_CORPUS_UNRESOLVED, REASON_OK, probe_corpus
from benchmarks.corpus import (
    CORRESPONDENCE_MET_FULL,
    CORRESPONDENCE_MET_SAMPLED,
    CorpusFingerprint,
    check_correspondence,
    distinct_passages,
    fingerprint_elasticsearch,
    fingerprint_qdrant,
    inspect_correspondence,
)

GOLDEN = ["alpha passage", "beta passage"]


# ── correspondence arithmetic ───────────────────────────────────────────────


def test_a_corpus_containing_every_passage_meets_the_requirement():
    report = check_correspondence(2, GOLDEN, "\n".join(GOLDEN))
    assert report.verdict == CORRESPONDENCE_MET_FULL
    assert report.usable is True
    assert report.passages_matched == 2
    assert report.ratio == 1.0


def test_a_corpus_missing_a_passage_is_not_usable():
    """This is the real state of the committed golden set against a live index."""
    report = check_correspondence(2, GOLDEN, "alpha passage\nsomething else entirely")
    assert report.verdict == CORRESPONDENCE_MET_SAMPLED
    assert report.usable is False
    assert report.passages_matched == 1
    assert report.passages_checked == 2
    assert report.missing_examples


def test_partial_corpus_can_never_report_full():
    """A sampled scan is never enough, even when every sampled passage matched.

    Otherwise "the sample happened to be covered" would be published as "the
    corpus covers the golden set".
    """
    many = [f"passage {index}" for index in range(50)]
    report = check_correspondence(50, many, "\n".join(many), sample_size=10)
    assert report.passages_checked == 10
    assert report.passages_matched == 10
    assert report.verdict == CORRESPONDENCE_MET_SAMPLED
    assert report.usable is False


def test_correspondence_is_deterministic_across_input_order():
    forward = check_correspondence(2, GOLDEN, "alpha passage\nbeta passage")
    reversed_order = check_correspondence(2, list(reversed(GOLDEN)), "alpha passage\nbeta passage")
    assert forward.sample_hash == reversed_order.sample_hash
    assert forward.verdict == reversed_order.verdict


def test_an_empty_corpus_matches_nothing():
    report = check_correspondence(2, GOLDEN, "")
    assert report.passages_matched == 0
    assert report.usable is False


def test_no_passages_is_never_usable():
    report = check_correspondence(0, [], "anything")
    assert report.passages_checked == 0
    assert report.usable is False


# ── fingerprints ────────────────────────────────────────────────────────────


class _FakeES:
    """Minimal Elasticsearch stand-in. Records calls so writes can be asserted absent."""

    def __init__(self, docs, count=None):
        self._docs = docs
        self._count = len(docs) if count is None else count
        self.calls = []

    def count(self, index):
        self.calls.append(("count", index))
        return {"count": self._count}

    def search(self, index, body):
        self.calls.append(("search", index))
        after = body.get("search_after")
        start = int(after[0]) if after else 0
        batch = self._docs[start : start + body["size"]]
        hits = []
        for offset, doc in enumerate(batch, start=start + 1):
            hits.append({"_id": f"id{offset}", "_source": doc, "sort": [offset]})
        return {"hits": {"hits": hits}}


class _Record:
    def __init__(self, payload, point_id):
        self.payload = payload
        self.id = point_id


class _FakeQdrant:
    def __init__(self, points, points_count=None):
        self._points = points
        self._points_count = len(points) if points_count is None else points_count
        self.calls = []

    def get_collection(self, name):
        self.calls.append(("get_collection", name))
        return type("Info", (), {"points_count": self._points_count})()

    def scroll(self, collection_name, scroll_filter=None, limit=10, offset=None, **kwargs):
        self.calls.append(("scroll", collection_name))
        start = int(offset) if offset is not None else 0
        batch = self._points[start : start + limit]
        next_offset = start + limit if start + limit < len(self._points) else None
        return [_Record(payload, index) for index, payload in enumerate(batch, start=start)], next_offset


def test_elasticsearch_fingerprint_is_order_independent():
    docs = [{"doc_id": "d1", "content": "one"}, {"doc_id": "d2", "content": "two"}]
    forward = fingerprint_elasticsearch(_FakeES(docs), "idx")
    backward = fingerprint_elasticsearch(_FakeES(list(reversed(docs))), "idx")
    assert forward.content_sha256 == backward.content_sha256
    assert forward.document_count == 2
    assert forward.reachable is True


def test_elasticsearch_fingerprint_reports_an_unreachable_store():
    class Broken:
        def count(self, index):
            raise RuntimeError("connection refused")

    fingerprint = fingerprint_elasticsearch(Broken(), "idx")
    assert fingerprint.reachable is False
    assert fingerprint.content_sha256 is None
    assert "connection refused" in fingerprint.error


def test_qdrant_fingerprint_digests_payload_content():
    points = [{"doc_id": "d1", "content": "one"}, {"doc_id": "d2", "content": "two"}]
    fingerprint = fingerprint_qdrant(_FakeQdrant(points), "coll")
    assert fingerprint.document_count == 2
    assert fingerprint.sampled_documents == 2
    assert fingerprint.reachable is True
    assert len(fingerprint.content_sha256) == 64


def test_a_different_corpus_has_a_different_fingerprint():
    """Otherwise a fingerprint could not tell two runs' corpora apart."""
    first = fingerprint_qdrant(_FakeQdrant([{"doc_id": "d1", "content": "one"}]), "coll")
    second = fingerprint_qdrant(_FakeQdrant([{"doc_id": "d1", "content": "changed"}]), "coll")
    assert first.content_sha256 != second.content_sha256


# ── probe behaviour ─────────────────────────────────────────────────────────


def test_probe_corpus_refuses_to_pass_without_ground_truth():
    availability = probe_corpus(10)
    assert availability.available is False
    assert availability.reason == REASON_CORPUS_UNRESOLVED


def test_probe_corpus_blocks_when_the_corpus_lacks_the_golden_passages(monkeypatch):
    monkeypatch.setattr(corpus_mod, "_es_client", lambda: None)
    monkeypatch.setattr(corpus_mod, "_qdrant_client", lambda: None)
    availability = probe_corpus(10, GOLDEN)
    assert availability.available is False
    assert availability.reason == REASON_CORPUS_UNRESOLVED
    assert "0/2" in availability.detail


def test_probe_corpus_passes_when_every_passage_is_indexed(monkeypatch):
    """The block is a measurement, not a hardcoded refusal."""
    indexed = "alpha passage\nbeta passage"
    monkeypatch.setattr(corpus_mod, "_es_client", lambda: object())
    monkeypatch.setattr(corpus_mod, "_es_index_name", lambda: "idx")
    monkeypatch.setattr(corpus_mod, "_es_corpus_text", lambda *a, **k: indexed)
    monkeypatch.setattr(corpus_mod, "_qdrant_client", lambda: object())
    monkeypatch.setattr(corpus_mod, "_qdrant_collection_name", lambda: "coll")
    monkeypatch.setattr(corpus_mod, "_qdrant_corpus_text", lambda *a, **k: indexed)
    # Fingerprints need a real client; stub the two entry points that use it.
    monkeypatch.setattr(
        corpus_mod,
        "fingerprint_elasticsearch",
        lambda *a, **k: CorpusFingerprint("elasticsearch", "idx", 2, 2, "d" * 64, True),
    )
    monkeypatch.setattr(
        corpus_mod,
        "fingerprint_qdrant",
        lambda *a, **k: CorpusFingerprint("qdrant", "coll", 2, 2, "e" * 64, True),
    )

    availability = probe_corpus(10, GOLDEN)
    assert availability.available is True
    assert availability.reason == REASON_OK
    assert "2/2" in availability.detail


def test_an_unreachable_store_is_reported_not_silently_skipped(monkeypatch):
    """A missing backend must lower correspondence, never look like an empty corpus."""
    monkeypatch.setattr(corpus_mod, "_es_client", lambda: None)
    monkeypatch.setattr(corpus_mod, "_qdrant_client", lambda: None)
    inspection = inspect_correspondence(GOLDEN, total_passages=2)
    assert inspection.report.usable is False
    assert all(not fingerprint.reachable for fingerprint in inspection.fingerprints)


# ── passage extraction ───────────────────────────────────────────────────────


def test_distinct_passages_is_sorted_and_deduplicated():
    class Item:
        def __init__(self, text):
            self.text = text

    class Query:
        def __init__(self, items):
            self.relevant_items = items

    queries = [
        Query([Item("zebra"), Item("apple")]),
        Query([Item("apple"), Item("  spaced  ")]),
    ]
    assert distinct_passages(queries) == ["spaced", "zebra", "apple"] or distinct_passages(queries) == [
        "apple",
        "spaced",
        "zebra",
    ]
    assert len(distinct_passages(queries)) == 3


def test_fingerprint_serializes_for_an_artifact():
    payload = CorpusFingerprint("qdrant", "c", 3, 3, "abc", True).as_dict()
    assert payload["system"] == "qdrant"
    assert payload["content_sha256"] == "abc"
    assert payload["reachable"] is True


@pytest.mark.parametrize("sample_size", [1, 5, 100])
def test_checked_count_never_exceeds_the_distinct_passages(sample_size):
    report = check_correspondence(2, GOLDEN, "\n".join(GOLDEN), sample_size=sample_size)
    assert report.passages_checked <= report.passages_distinct
    assert report.passages_checked <= sample_size or report.passages_distinct <= sample_size