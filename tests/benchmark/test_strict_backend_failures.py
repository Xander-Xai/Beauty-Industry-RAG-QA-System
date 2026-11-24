"""Strict benchmark retrieval must not confuse outage with genuine zero hits."""

from unittest.mock import Mock

import pytest

from retrieval.bm25_retriever import BM25Retriever
from retrieval.dense_retriever import DenseRetriever


def bm25(strict):
    retriever = BM25Retriever(strict=strict)
    retriever.enabled = True
    retriever._es_client = Mock()
    retriever._epoch_binding = {"bound": True, "document_count": 3}
    retriever._build_es_query = Mock(return_value={})
    return retriever


@pytest.mark.parametrize(
    "response",
    [
        {"timed_out": True, "_shards": {"failed": 0}, "hits": {"hits": []}},
        {"timed_out": False, "_shards": {"failed": 1}, "hits": {"hits": []}},
    ],
)
def test_strict_bm25_rejects_partial_results(response):
    retriever = bm25(True)
    retriever._es_client.search.return_value = response
    with pytest.raises(RuntimeError, match="incomplete"):
        retriever.search("q", 1, 1)


@pytest.mark.parametrize("strict", [False, True])
def test_bm25_outage_is_only_an_empty_result_in_online_mode(strict):
    retriever = bm25(strict)
    retriever._es_client.search.side_effect = OSError("private backend detail")
    if strict:
        with pytest.raises(RuntimeError, match="BM25 search failed") as exc:
            retriever.search("q", 1, 1)
        assert "private backend detail" not in str(exc.value)
    else:
        assert retriever.search("q", 1, 1) == []


def test_strict_bm25_accepts_genuine_zero_hits():
    retriever = bm25(True)
    retriever._es_client.search.return_value = {
        "timed_out": False,
        "_shards": {"failed": 0},
        "hits": {"hits": []},
    }
    assert retriever.search("q", 1, 1) == []


def test_strict_bm25_rejects_unverified_epoch():
    retriever = bm25(True)
    retriever._epoch_binding = {"bound": True, "document_count": 0}
    with pytest.raises(RuntimeError, match="not independently verified"):
        retriever.search("q", 1, 1)
    retriever._es_client.search.assert_not_called()


@pytest.mark.parametrize("strict", [False, True])
def test_dense_outage_is_only_an_empty_result_in_online_mode(strict):
    retriever = DenseRetriever(strict=strict)
    retriever._embedding_service = Mock()
    retriever._embedding_service.search_qdrant_text.side_effect = OSError("private backend detail")
    if strict:
        with pytest.raises(RuntimeError, match="Dense search failed") as exc:
            retriever.search(None)
        assert "private backend detail" not in str(exc.value)
    else:
        assert retriever.search(None) == []


def test_strict_dense_accepts_genuine_zero_hits():
    retriever = DenseRetriever(strict=True)
    retriever._embedding_service = Mock()
    retriever._embedding_service.search_qdrant_text.return_value = []
    assert retriever.search(None) == []
