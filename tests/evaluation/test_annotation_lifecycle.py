"""Annotation review lifecycle: an unreviewed label must never become gold.

These tests pin the rules in ``benchmarks/annotation.py``. They deliberately use
**fixture** records built inside the test — no committed dataset is asserted to be
annotated, because none is. ``tests/evaluation/golden_set.jsonl`` still has zero
reviewed rows and this module must not pretend otherwise.
"""

from __future__ import annotations

import json

import pytest

from benchmarks.annotation import (
    REVIEW_STATUS_DRAFT,
    REVIEW_STATUS_REVIEWED,
    AnnotationError,
    audit_dataset,
    load_records,
    promote_to_reviewed,
    validate_annotation,
)
from benchmarks.golden_set_contract import validate_row

CORPUS_HASH = "a" * 64


def _record(**overrides):
    """A fully reviewed, contract-valid record. Fixture values, never real labels."""
    record = {
        "sample_id": "0001",
        "question": "化妆品备案需要哪些材料？",
        "business_type": "regulation",
        "difficulty": "easy",
        "visual_required": False,
        "complexity_label": "simple",
        "corpus_version": "corpus_2026_10",
        "corpus_sha256": CORPUS_HASH,
        "annotations": [
            {"doc_id": "doc-1", "chunk_id": "doc-1#0", "text": "备案所需材料包括产品配方表。"},
            {"doc_id": "doc-1", "chunk_id": "doc-1#1", "text": "备案所需材料包括产品检验报告。"},
        ],
        "annotation": {
            "annotator": "annotator-a",
            "method": "manual-read-and-map",
            "annotated_at": "2026-10-09",
            "source": "human",
            "review_status": REVIEW_STATUS_REVIEWED,
            "reviewed_by": "reviewer-b",
            "reviewed_at": "2026-10-10",
        },
    }
    record.update(overrides)
    return record


# ── review status is mandatory ──────────────────────────────────────────────


def test_a_reviewed_record_is_valid_and_scorable():
    verdict = validate_annotation(_record())
    assert verdict.valid is True
    assert verdict.scorable is True
    assert verdict.reasons == ()


def test_a_record_without_review_status_is_rejected():
    """No default. A record that does not say whether a human looked is unusable."""
    record = _record()
    del record["annotation"]["review_status"]
    verdict = validate_annotation(record)
    assert verdict.valid is False
    assert "missing_annotation_review_status" in verdict.reasons
    assert verdict.scorable is False


def test_an_llm_candidate_is_valid_as_a_draft_but_never_scorable():
    record = _record()
    record["annotation"] = {
        "annotator": "llm-proposer",
        "method": "model-proposed-mapping",
        "annotated_at": "2026-10-09",
        "source": "llm_candidate",
        "review_status": REVIEW_STATUS_DRAFT,
    }
    verdict = validate_annotation(record)
    assert verdict.scorable is False
    assert verdict.valid is True  # a well-formed draft is still a well-formed record


def test_the_contract_gate_refuses_a_draft():
    """The end-to-end guarantee: a draft cannot be scored, whatever else it has."""
    record = _record()
    record["annotation"] = {
        "annotator": "llm-proposer",
        "method": "model-proposed-mapping",
        "annotated_at": "2026-10-09",
        "source": "llm_candidate",
        "review_status": REVIEW_STATUS_DRAFT,
    }
    verdict = validate_row(record)
    assert verdict.status == "INVALID"
    assert f"annotation_not_reviewed:{REVIEW_STATUS_DRAFT}" in verdict.reasons


def test_a_reviewed_record_passes_the_contract_gate():
    assert validate_row(_record()).status == "VALID"


def test_an_llm_candidate_cannot_claim_review_without_a_named_promoter():
    record = _record()
    record["annotation"] = {
        "annotator": "llm-proposer",
        "method": "model-proposed-mapping",
        "annotated_at": "2026-10-09",
        "source": "llm_candidate",
        "review_status": REVIEW_STATUS_REVIEWED,
        "reviewed_by": "reviewer-b",
        "reviewed_at": "2026-10-10",
    }
    verdict = validate_annotation(record)
    assert verdict.valid is False
    assert "annotation_llm_promotion_requires_promoted_by" in verdict.reasons


def test_self_review_is_rejected():
    record = _record()
    record["annotation"]["reviewed_by"] = record["annotation"]["annotator"]
    verdict = validate_annotation(record)
    assert "annotation_self_reviewed" in verdict.reasons
    assert verdict.scorable is False


def test_a_reviewed_record_must_name_its_reviewer_and_date():
    record = _record()
    del record["annotation"]["reviewed_by"]
    del record["annotation"]["reviewed_at"]
    verdict = validate_annotation(record)
    assert "missing_annotation_reviewed_by" in verdict.reasons
    assert "missing_or_invalid_annotation_reviewed_at" in verdict.reasons


# ── corpus binding ──────────────────────────────────────────────────────────


def test_a_record_must_be_bound_to_a_corpus_hash():
    record = _record()
    del record["corpus_sha256"]
    verdict = validate_annotation(record)
    assert "missing_or_invalid_corpus_sha256" in verdict.reasons


def test_a_malformed_corpus_hash_is_rejected():
    verdict = validate_annotation(_record(corpus_sha256="not-a-hash"))
    assert "missing_or_invalid_corpus_sha256" in verdict.reasons


def test_a_dataset_mapped_against_two_corpus_states_is_rejected():
    other = "b" * 64
    report = audit_dataset([_record(), _record(sample_id="0002", corpus_sha256=other)])
    assert report.valid_count == 0
    assert "mixed_corpus_sha256_in_dataset" in report.invalid_reasons()
    assert report.corpus_sha256 is None


# ── structural integrity ────────────────────────────────────────────────────


def test_a_duplicate_chunk_is_rejected():
    """A repeated (doc_id, chunk_id) would double-count in Recall's denominator."""
    record = _record()
    record["annotations"].append(dict(record["annotations"][0]))
    verdict = validate_annotation(record)
    assert "annotation_2_duplicate_chunk" in verdict.reasons


def test_an_invalid_relevance_grade_is_rejected():
    record = _record()
    record["annotations"][0]["relevance"] = 7
    verdict = validate_annotation(record)
    assert "annotation_0_invalid_relevance_grade" in verdict.reasons


def test_multiple_chunks_are_allowed():
    """The normal case: a question is supported by more than one passage."""
    verdict = validate_annotation(_record())
    assert verdict.valid is True
    assert len(_record()["annotations"]) == 2


# ── promotion ───────────────────────────────────────────────────────────────


def test_promotion_requires_a_named_reviewer():
    with pytest.raises(AnnotationError):
        promote_to_reviewed(_record(), reviewer="", reviewed_at="2026-10-10")


def test_promotion_requires_an_iso_date():
    with pytest.raises(AnnotationError):
        promote_to_reviewed(_record(), reviewer="reviewer-b", reviewed_at="yesterday")


def test_promotion_refuses_to_invent_a_missing_annotator():
    """Otherwise an unknown origin gets laundered under a real reviewer's name."""
    record = _record()
    record["annotation"] = {
        "annotator": "",
        "method": "model-proposed-mapping",
        "annotated_at": "2026-10-09",
        "source": "llm_candidate",
        "review_status": REVIEW_STATUS_DRAFT,
    }
    with pytest.raises(AnnotationError, match="named annotator"):
        promote_to_reviewed(record, reviewer="reviewer-b", reviewed_at="2026-10-10")


def test_promotion_turns_a_draft_into_a_scorable_record():
    record = _record()
    record["annotation"] = {
        "annotator": "llm-proposer",
        "method": "model-proposed-mapping",
        "annotated_at": "2026-10-09",
        "source": "llm_candidate",
        "review_status": REVIEW_STATUS_DRAFT,
    }
    assert validate_row(record).status == "INVALID"

    promoted = promote_to_reviewed(record, reviewer="reviewer-b", reviewed_at="2026-10-10", promoted_by="reviewer-b")
    assert promoted["annotation"]["review_status"] == REVIEW_STATUS_REVIEWED
    assert promoted["annotation"]["reviewed_by"] == "reviewer-b"
    # Promotion is explicit and recorded, and the original is untouched.
    assert record["annotation"]["review_status"] == REVIEW_STATUS_DRAFT
    # With promoted_by set, the llm_candidate promotion clause is satisfied too.
    assert validate_annotation(promoted).valid is True
    assert validate_row(promoted).status == "VALID"


# ── dataset level ───────────────────────────────────────────────────────────


def test_dataset_report_counts_drafts_separately():
    draft = _record(sample_id="0002")
    draft["annotation"] = {
        "annotator": "llm-proposer",
        "method": "m",
        "annotated_at": "2026-10-09",
        "source": "llm_candidate",
        "review_status": REVIEW_STATUS_DRAFT,
    }
    report = audit_dataset([_record(), draft])
    assert report.total == 2
    assert report.scorable_count == 1
    assert report.draft_count == 1
    assert report.corpus_versions == ("corpus_2026_10",)
    assert report.corpus_sha256 == CORPUS_HASH


def test_the_committed_annotation_dataset_is_empty_not_fabricated(tmp_path):
    """The shipped v2 annotation file must contain no invented labels."""
    from benchmarks.annotation import ANNOTATION_SCHEMA_VERSION  # noqa: F401

    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    report = audit_dataset(load_records(path))
    assert report.total == 0
    assert report.scorable_count == 0
    assert load_records(path) == []


def test_a_missing_annotation_file_is_an_error_not_an_empty_dataset(tmp_path):
    with pytest.raises(AnnotationError, match="does not exist"):
        load_records(tmp_path / "absent.jsonl")


def test_a_malformed_line_is_rejected(tmp_path):
    path = tmp_path / "broken.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(AnnotationError, match="invalid JSON"):
        load_records(path)


def test_audit_is_deterministic_for_identical_records():
    first = audit_dataset([_record(), _record(sample_id="0002")])
    second = audit_dataset([_record(), _record(sample_id="0002")])
    assert first.as_dict() == second.as_dict()


def test_record_serializes_round_trip(tmp_path):
    path = tmp_path / "one.jsonl"
    path.write_text(json.dumps(_record(), ensure_ascii=False) + "\n", encoding="utf-8")
    assert load_records(path)[0] == _record()
