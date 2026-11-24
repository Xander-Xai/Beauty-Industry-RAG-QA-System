"""Safety and exact chunk binding controls; no real BGE calls or store writes."""

import math
from copy import deepcopy

import pytest

from benchmarks.source_approval import _index_content_fingerprint
from scripts.corpus.index_approved_dense_experiment import (
    normalize_cosine_vectors,
    prepare_chunks,
    validate_cosine_vectors,
    validate_targets,
)


def _config():
    return {
        "elasticsearch": {"index": "cosmetics_docs_exp_test", "host": "http://127.0.0.1:9200"},
        "qdrant": {"host": "localhost"},
        "embedding": {"text": {"collection": "rag_text_exp_test"}},
    }


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("elasticsearch", "index", "cosmetics_docs"),
        ("elasticsearch", "host", "https://external.invalid"),
        ("qdrant", "host", "external.invalid"),
        ("text", "collection", "rag_text_768"),
    ],
)
def test_production_or_remote_target_rejected(section, key, value):
    config = _config()
    target = config["embedding"]["text"] if section == "text" else config[section]
    target[key] = value
    with pytest.raises(ValueError):
        validate_targets(config, None, write=False)


def test_write_requires_exact_collection_confirmation():
    config = _config()
    assert validate_targets(config, None, write=False) == ("cosmetics_docs_exp_test", "rag_text_exp_test")
    with pytest.raises(ValueError, match="confirmation"):
        validate_targets(config, "other", write=True)
    assert validate_targets(config, "rag_text_exp_test", write=True)[1] == "rag_text_exp_test"


def _data():
    rows = [
        {
            "doc_id": "doc",
            "chunk_id": "chunk",
            "chunk_index": 0,
            "doc_version_epoch": "epoch",
            "source_path": "source",
            "content": "approved text",
            "role_mask": 0,
            "dept_mask": 0,
            "status": "active",
            "provenance": {"source_id": "fixture"},
        }
    ]
    records = [
        {
            "doc_id": "doc",
            "chunk_id": "chunk",
            "chunk_index": 0,
            "corpus_version": "epoch",
            "text": "approved text",
            "content_hash": "fixture",
        }
    ]
    approval = {"verified": True, "document_count": 1, "content_sha256": _index_content_fingerprint(rows)}
    return rows, records, approval


def test_chunk_binding_preserves_payload():
    rows, records, approval = _data()
    chunk = prepare_chunks(rows, records, approval, "epoch")[0]
    assert (chunk.doc_id, chunk.chunk_id, chunk.text, chunk.provenance) == (
        "doc",
        "chunk",
        "approved text",
        rows[0]["provenance"],
    )


@pytest.mark.parametrize("field", ["text", "chunk_index", "corpus_version", "chunk_id"])
def test_wrong_chunk_rejected(field):
    rows, records, approval = _data()
    changed = deepcopy(records)
    changed[0][field] = "different"
    with pytest.raises(ValueError):
        prepare_chunks(rows, changed, approval, "epoch")


def test_duplicate_and_unapproved_rejected():
    rows, records, approval = _data()
    with pytest.raises(ValueError):
        prepare_chunks(rows, records + records, approval, "epoch")
    with pytest.raises(ValueError):
        prepare_chunks(rows, records, {"verified": False}, "epoch")


@pytest.mark.parametrize(
    "vectors",
    [[], [[0.0, 0.0]], [[float("nan"), 1.0]], [[float("inf"), 0.0]], [[1.0]], [[1.0, 0.0], [1.0, 0.0]]],
)
def test_invalid_cosine_embedding_population_rejected(vectors):
    with pytest.raises(ValueError):
        validate_cosine_vectors(vectors, 1, 2)


def test_nonzero_finite_cosine_embeddings_accepted():
    validate_cosine_vectors([[0.0, 1.0], [-0.5, 0.5]], 2, 2)


def test_builder_normalizes_validated_vectors_before_storage():
    source = [[3.0, 4.0], [-4.0, 3.0]]
    validate_cosine_vectors(source, 2, 2)
    result = normalize_cosine_vectors(source)
    assert result == [[0.6, 0.8], [-0.8, 0.6]]
    assert all(math.isclose(math.hypot(*vector), 1.0) for vector in result)
    assert source == [[3.0, 4.0], [-4.0, 3.0]]
