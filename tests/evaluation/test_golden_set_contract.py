"""Golden-set v2 contract tests.

The point of these tests is fail-closed behaviour: a degraded dataset (missing
label, chunk-boundary shift, knowledge-epoch change, wrong label, unresolvable
identifier, fabricated id) must be marked INVALID and must make
:func:`require_attributable` raise before any percentage is computed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.golden_set_contract import (
    CONTRACT_VERSION,
    STATUS_INVALID,
    STATUS_VALID,
    DatasetContractError,
    elasticsearch_resolver,
    load_contract_rows,
    qdrant_resolver,
    require_attributable,
    validate_dataset,
    validate_row,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_GOLDEN_SET = REPO_ROOT / "tests" / "evaluation" / "golden_set.jsonl"


def _valid_row(**overrides):
    row = {
        "sample_id": "0001",
        "question": "化妆品备案需要哪些材料？",
        "business_type": "regulation",
        "difficulty": "easy",
        "visual_required": False,
        "complexity_label": "simple",
        "corpus_version": "corpus_2026_10",
        "annotations": [
            {"doc_id": "doc-1", "chunk_id": "doc-1#0", "text": "备案所需材料包括产品配方表。"},
        ],
        "annotation": {
            "annotator": "reviewer-a",
            "method": "manual-read-and-map",
            "annotated_at": "2026-10-09",
            "reviewed_by": "reviewer-b",
        },
    }
    row.update(overrides)
    return row


_ALL_KNOWING = lambda doc_id, chunk_id, corpus_version: True  # noqa: E731


def test_fully_specified_row_is_valid():
    verdict = validate_row(_valid_row(), resolver=_ALL_KNOWING)
    assert verdict.status == STATUS_VALID
    assert verdict.reasons == ()


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"visual_required": None}, "missing_visual_required_label"),
        ({"complexity_label": "unknown"}, "missing_complexity_label"),
        ({"corpus_version": ""}, "missing_or_invalid_corpus_version"),
        ({"annotations": []}, "missing_annotations"),
        ({"annotation": None}, "missing_annotation_provenance"),
        ({"annotation": {"annotator": "a", "method": "m", "annotated_at": "d"}}, "missing_annotation_reviewed_by"),
        ({"business_type": "skincare"}, "invalid_business_type"),
    ],
)
def test_each_contract_clause_fails_closed(overrides, expected):
    verdict = validate_row(_valid_row(**overrides))
    assert verdict.status == STATUS_INVALID
    assert expected in verdict.reasons


def test_annotation_missing_identifier_is_invalid():
    row = _valid_row(annotations=[{"doc_id": "doc-1", "text": "证据"}])
    verdict = validate_row(row)
    assert verdict.status == STATUS_INVALID
    assert "annotation_0_missing_chunk_id" in verdict.reasons


def test_unresolvable_identifier_is_invalid():
    never = lambda doc_id, chunk_id, corpus_version: False  # noqa: E731
    verdict = validate_row(_valid_row(), resolver=never)
    assert verdict.status == STATUS_INVALID
    assert any(reason.startswith("annotation_0_unresolved:") for reason in verdict.reasons)


def test_chunk_boundary_change_makes_a_row_invalid():
    # The annotation references the old chunk id; the re-chunked index knows a
    # different id. That is an alignment failure, and it must not be scored as a
    # retrieval failure.
    row = _valid_row(annotations=[{"doc_id": "doc-1", "chunk_id": "doc-1#0", "text": "证据"}])
    reindexed = lambda doc_id, chunk_id, corpus_version: chunk_id == "doc-1#1"  # noqa: E731
    assert validate_row(row, resolver=reindexed).status == STATUS_INVALID


def test_knowledge_epoch_change_makes_a_row_invalid():
    row = _valid_row(corpus_version="corpus_2026_10")
    other_epoch = lambda doc_id, chunk_id, corpus_version: corpus_version == "corpus_2026_11"  # noqa: E731
    verdict = validate_row(row, resolver=other_epoch)
    assert verdict.status == STATUS_INVALID
    assert any("unresolved" in reason for reason in verdict.reasons)


def test_fabricated_doc_id_is_invalid_against_a_real_index_lookup():
    # A resolver that only knows the genuinely indexed ids rejects a fabricated one.
    indexed = {("doc-1", "doc-1#0", "corpus_2026_10")}
    resolver = lambda doc_id, chunk_id, corpus_version: (doc_id, chunk_id, corpus_version) in indexed  # noqa: E731
    assert validate_row(_valid_row(), resolver=resolver).status == STATUS_VALID
    fabricated = _valid_row(annotations=[{"doc_id": "doc-999", "chunk_id": "doc-999#0", "text": "x"}])
    assert validate_row(fabricated, resolver=resolver).status == STATUS_INVALID


def test_require_attributable_raises_when_nothing_is_valid():
    rows = [_valid_row(sample_id="a", visual_required=None), _valid_row(sample_id="b", complexity_label="?")]
    report = validate_dataset(rows)
    assert report.valid_count == 0
    with pytest.raises(DatasetContractError, match="0/2"):
        require_attributable(report)


def test_require_attributable_raises_below_threshold():
    rows = [_valid_row(sample_id="a"), _valid_row(sample_id="b", visual_required=None)]
    report = validate_dataset(rows, resolver=_ALL_KNOWING)
    assert report.valid_fraction == pytest.approx(0.5)
    with pytest.raises(DatasetContractError, match="refusing to score"):
        require_attributable(report, min_valid_fraction=0.9)
    # At a 0.5 threshold the same report is attributable.
    require_attributable(report, min_valid_fraction=0.5)


def test_committed_golden_set_is_not_yet_attributable():
    """The shipped dataset has no stable ids/labels, so it must be 0/301 valid.

    This pins the measured gap from issue #86: if a future annotation pass fills
    the contract, this test flips and must be updated deliberately.
    """
    rows = load_contract_rows(COMMITTED_GOLDEN_SET)
    report = validate_dataset(rows)
    assert CONTRACT_VERSION in report.contract_version
    assert report.valid_count == 0
    assert report.invalid_count == len(rows)
    with pytest.raises(DatasetContractError):
        require_attributable(report)


def test_qdrant_resolver_queries_by_doc_chunk_and_epoch():
    class _Point:
        pass

    class _Client:
        def __init__(self):
            self.calls = []

        def scroll(self, *, collection_name, scroll_filter, limit, with_payload):
            self.calls.append((collection_name, scroll_filter, limit))
            # Only the exact triple resolves.
            return ([_Point()], None)

    client = _Client()
    resolver = qdrant_resolver(client, "rag_text_768")
    assert resolver("doc-1", "doc-1#0", "corpus_2026_10") is True
    assert client.calls, "resolver must issue a scroll existence query"
    conditions = client.calls[0][1].must
    keys = {condition.key for condition in conditions}
    assert {"doc_id", "chunk_id", "doc_version_epoch"} <= keys


def test_elasticsearch_resolver_uses_an_existence_count():
    class _ES:
        def count(self, *, index, query):
            terms = query["bool"]["must"]
            fields = {next(iter(clause["term"])) for clause in terms}
            assert fields == {"doc_id", "chunk_id", "doc_version_epoch"}
            return {"count": 1}

    assert elasticsearch_resolver(_ES(), "cosmetics_docs")("d", "c", "epoch") is True


def test_contract_json_is_serialisable():
    from benchmarks.golden_set_contract import report_to_json

    report = validate_dataset([_valid_row()], resolver=_ALL_KNOWING)
    payload = json.loads(report_to_json(report))
    assert payload["valid_count"] == 1
    assert payload["contract_version"] == CONTRACT_VERSION
