"""Regression candidate gating tests.

Fully deterministic and synthetic: no LLM, no retrieval, no network. Every
fixture is a hand-written record, so a failure means the gate broke rather than
that a model drifted.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from offline.feedback_loop import (
    ACCEPTED as FEEDBACK_ACCEPTED,
)
from offline.feedback_loop import (
    PENDING as FEEDBACK_PENDING,
)
from offline.feedback_loop import FeedbackRecord, make_feedback_id
from offline.regression_candidates import (
    ACCEPTED,
    PENDING_REVIEW,
    REJECTED,
    MissingExpectationError,
    RegressionCandidateError,
    RegressionCandidateLoop,
    UnknownRegressionCase,
    build_regression_case,
    make_case_id,
)

QUESTION = "视黄醇可以和维生素 A 一起用吗"
BUSINESS_TYPE = "ingredient"
MODEL_ANSWER = "不可以，维生素 A 会让视黄醇完全失效。"

HUMAN_BEHAVIOUR = "说明两者叠加使用的浓度上限与刺激性风险，而不是一概判定为不可用。"
HUMAN_EVIDENCE = ["doc-retinol-guideline", "doc-vitamin-a-limit"]


def _loop(tmp_path: Path) -> RegressionCandidateLoop:
    return RegressionCandidateLoop(tmp_path / "feedback.sqlite3", output_dir=tmp_path / "out")


def _feedback(
    query: str = QUESTION,
    *,
    rating: float | None = -1.0,
    answer: str = MODEL_ANSWER,
    correction: str = "",
    business_type: str = BUSINESS_TYPE,
    request_id: str = "req-1",
) -> FeedbackRecord:
    return FeedbackRecord(
        feedback_id=make_feedback_id(query, request_id),
        query=query,
        answer=answer,
        evidence_doc_ids=["doc-wrong-1", "doc-wrong-2"],
        request_id=request_id,
        session_ref="hashed-session",
        rating=rating,
        correction=correction,
        business_type=business_type,
        intent="compatibility",
    )


def _reviewed_negative(tmp_path: Path, record: FeedbackRecord | None = None) -> tuple[RegressionCandidateLoop, str]:
    """Drive one negative record through the existing feedback review gate."""
    loop = _loop(tmp_path)
    record = record or _feedback()
    loop.feedback_store.add(record)
    loop.feedback_store.set_review_status(record.feedback_id, FEEDBACK_ACCEPTED)
    return loop, record.feedback_id


def _dataset_rows(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _accept(loop: RegressionCandidateLoop, case_id: str, *, reviewer: str = "qa-owner") -> None:
    loop.candidate_store.set_expectations(
        case_id,
        expected_behaviour=HUMAN_BEHAVIOUR,
        expected_evidence=HUMAN_EVIDENCE,
        reviewer=reviewer,
    )
    loop.candidate_store.set_review_status(case_id, ACCEPTED, reviewer=reviewer)


# ── negative feedback becomes a candidate ───────────────────────────────────


def test_negative_feedback_creates_pending_candidate(tmp_path):
    loop, feedback_id = _reviewed_negative(tmp_path)

    assert loop.collect_candidates() == {"created": 1, "skipped": 0}

    candidate = loop.candidate_store.list_candidates()[0]
    assert candidate.review_status == PENDING_REVIEW
    assert candidate.source_feedback_id == feedback_id
    assert candidate.question == QUESTION
    assert candidate.business_type == BUSINESS_TYPE
    assert candidate.intent == "compatibility"
    assert candidate.created_at


def test_candidate_records_the_required_fields(tmp_path):
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()

    candidate = loop.candidate_store.list_candidates()[0]
    payload = candidate.to_dict()
    for field in (
        "source_feedback_id",
        "question",
        "expected_behaviour",
        "expected_evidence",
        "business_type",
        "created_at",
        "provenance",
        "review_status",
        "case_id",
    ):
        assert field in payload, field


def test_feedback_must_clear_the_existing_gate_first(tmp_path):
    """A signal still sitting in the feedback review queue is not yet a candidate."""
    loop = _loop(tmp_path)
    loop.feedback_store.add(_feedback())

    assert loop.feedback_store.list_records()[0].review_status == FEEDBACK_PENDING
    assert loop.collect_candidates() == {"created": 0, "skipped": 0}
    assert loop.candidate_store.count() == 0


def test_positive_feedback_is_not_a_regression_candidate(tmp_path):
    loop, _ = _reviewed_negative(tmp_path, _feedback(rating=1.0))

    assert loop.collect_candidates() == {"created": 0, "skipped": 0}
    assert loop.candidate_store.count() == 0


def test_unrated_feedback_is_not_a_regression_candidate(tmp_path):
    loop, _ = _reviewed_negative(tmp_path, _feedback(rating=None))

    assert loop.collect_candidates() == {"created": 0, "skipped": 0}


def test_feedback_without_a_question_is_skipped_not_stored(tmp_path):
    loop, _ = _reviewed_negative(tmp_path, _feedback(query=""))

    assert loop.collect_candidates() == {"created": 0, "skipped": 1}
    assert loop.candidate_store.count() == 0


# ── candidates are never auto-promoted ──────────────────────────────────────


def test_pending_candidate_never_enters_the_dataset(tmp_path):
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()
    candidate = loop.candidate_store.list_candidates()[0]
    loop.candidate_store.set_expectations(
        candidate.case_id,
        expected_behaviour=HUMAN_BEHAVIOUR,
        expected_evidence=HUMAN_EVIDENCE,
    )

    assert loop.candidate_store.count(ACCEPTED) == 0
    assert _dataset_rows(loop.export_regression_dataset()) == []
    assert candidate.case_id in Path(loop.export_candidates()).read_text(encoding="utf-8")


def test_export_candidates_is_the_queue_not_the_dataset(tmp_path):
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()

    queue = _dataset_rows(loop.export_candidates())
    assert [row["review_status"] for row in queue] == [PENDING_REVIEW]
    assert all("answer" not in row for row in queue)


def test_rejected_candidate_never_enters_the_dataset(tmp_path):
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()
    candidate = loop.candidate_store.list_candidates()[0]
    loop.candidate_store.set_expectations(
        candidate.case_id,
        expected_behaviour=HUMAN_BEHAVIOUR,
        expected_evidence=HUMAN_EVIDENCE,
    )
    loop.candidate_store.set_review_status(candidate.case_id, REJECTED, reviewer="qa-owner")

    assert _dataset_rows(loop.export_regression_dataset()) == []


def test_non_accepted_candidate_cannot_be_rendered_as_a_case(tmp_path):
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()
    candidate = loop.candidate_store.list_candidates()[0]

    with pytest.raises(RegressionCandidateError):
        build_regression_case(candidate)


# ── manual acceptance promotes a candidate ──────────────────────────────────


def test_manual_acceptance_promotes_to_a_regression_case(tmp_path):
    loop, feedback_id = _reviewed_negative(tmp_path)
    loop.collect_candidates()
    case_id = loop.candidate_store.list_candidates()[0].case_id

    _accept(loop, case_id)
    rows = _dataset_rows(loop.export_regression_dataset())

    assert len(rows) == 1
    row = rows[0]
    assert row["case_id"] == case_id
    assert row["question"] == QUESTION
    assert row["expected_behaviour"] == HUMAN_BEHAVIOUR
    assert row["expected_evidence"] == HUMAN_EVIDENCE
    assert row["business_type"] == BUSINESS_TYPE
    assert row["source_feedback_id"] == feedback_id
    assert row["review_status"] == ACCEPTED
    assert row["reviewed_by"] == "qa-owner"
    assert row["reviewed_at"]


def test_acceptance_is_recorded_as_a_decision(tmp_path):
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()
    case_id = loop.candidate_store.list_candidates()[0].case_id

    loop.candidate_store.set_expectations(
        case_id,
        expected_behaviour=HUMAN_BEHAVIOUR,
        expected_evidence=HUMAN_EVIDENCE,
        note="retrieval missed both guidelines",
        reviewer="qa-owner",
    )
    loop.candidate_store.set_review_status(case_id, ACCEPTED, reviewer="qa-owner")

    stored = loop.candidate_store.require(case_id)
    assert stored.review_note == "retrieval missed both guidelines"
    assert stored.reviewed_by == "qa-owner"


# ── missing expectations fail closed ────────────────────────────────────────


def test_acceptance_without_any_expectation_fails_closed(tmp_path):
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()
    case_id = loop.candidate_store.list_candidates()[0].case_id

    with pytest.raises(MissingExpectationError):
        loop.candidate_store.set_review_status(case_id, ACCEPTED)

    assert loop.candidate_store.require(case_id).review_status == PENDING_REVIEW
    assert _dataset_rows(loop.export_regression_dataset()) == []


def test_missing_expected_evidence_alone_fails_closed(tmp_path):
    """Behaviour text is not enough: an accepted case must say what it expects to retrieve."""
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()
    case_id = loop.candidate_store.list_candidates()[0].case_id
    loop.candidate_store.set_expectations(case_id, expected_behaviour=HUMAN_BEHAVIOUR, expected_evidence=[])

    with pytest.raises(MissingExpectationError) as error:
        loop.candidate_store.set_review_status(case_id, ACCEPTED)

    assert "expected_evidence" in str(error.value)
    assert loop.candidate_store.count(ACCEPTED) == 0


def test_missing_expected_behaviour_fails_closed(tmp_path):
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()
    case_id = loop.candidate_store.list_candidates()[0].case_id
    loop.candidate_store.set_expectations(case_id, expected_behaviour="", expected_evidence=HUMAN_EVIDENCE)

    with pytest.raises(MissingExpectationError) as error:
        loop.candidate_store.set_review_status(case_id, ACCEPTED)

    assert "expected_behaviour" in str(error.value)


def test_blank_expectations_are_treated_as_missing(tmp_path):
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()
    case_id = loop.candidate_store.list_candidates()[0].case_id
    loop.candidate_store.set_expectations(case_id, expected_behaviour="   ", expected_evidence=["", "  "])

    with pytest.raises(MissingExpectationError):
        loop.candidate_store.set_review_status(case_id, ACCEPTED)


def test_dataset_export_fails_closed_on_a_tampered_accepted_row(tmp_path):
    """Defence in depth: an approval bypassed outside the API cannot ship an empty case."""
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()
    case_id = loop.candidate_store.list_candidates()[0].case_id
    with sqlite3.connect(loop.candidate_store.path) as connection:
        connection.execute(
            "UPDATE regression_candidates SET review_status = ?, expected_evidence = ? WHERE case_id = ?",
            (ACCEPTED, "[]", case_id),
        )

    assert loop.candidate_store.count(ACCEPTED) == 1
    with pytest.raises(MissingExpectationError):
        loop.export_regression_dataset()


# ── stable ids and duplicate detection ──────────────────────────────────────


def test_case_id_is_stable_across_whitespace_and_case():
    assert make_case_id("  视黄醇  可以一起用吗 ", BUSINESS_TYPE) == make_case_id("视黄醇 可以一起用吗", BUSINESS_TYPE)
    assert make_case_id("视黄醇 可以一起用吗", "Ingredient") == make_case_id("视黄醇 可以一起用吗", "ingredient")


def test_case_id_separates_questions_and_business_types():
    assert make_case_id("问题 A", BUSINESS_TYPE) != make_case_id("问题 B", BUSINESS_TYPE)
    assert make_case_id("问题 A", "ingredient") != make_case_id("问题 A", "regulation")


def test_case_id_ignores_time_rating_and_feedback_identity(tmp_path):
    """Rebuilding the store from the same feedback must reproduce the same ids."""
    first = _reviewed_negative(tmp_path)[0]
    first.collect_candidates()
    first_id = first.candidate_store.list_candidates()[0].case_id
    first.close()

    rebuilt = _reviewed_negative(tmp_path)[0]
    rebuilt.collect_candidates()

    assert rebuilt.candidate_store.list_candidates()[0].case_id == first_id
    assert first_id == make_case_id(QUESTION, BUSINESS_TYPE)


def test_duplicate_candidate_is_idempotent(tmp_path):
    loop, _ = _reviewed_negative(tmp_path)

    assert loop.collect_candidates() == {"created": 1, "skipped": 0}
    assert loop.collect_candidates() == {"created": 0, "skipped": 0}
    assert loop.candidate_store.count() == 1


def test_same_question_from_another_request_is_one_case(tmp_path):
    """A repeat failure is the same regression case, not a second copy of it."""
    loop = _loop(tmp_path)
    for request_id in ("req-1", "req-2"):
        record = _feedback(request_id=request_id)
        loop.feedback_store.add(record)
        loop.feedback_store.set_review_status(record.feedback_id, FEEDBACK_ACCEPTED)

    assert loop.collect_candidates() == {"created": 1, "skipped": 0}
    assert loop.candidate_store.count() == 1
    assert loop.candidate_store.list_candidates()[0].case_id == make_case_id(QUESTION, BUSINESS_TYPE)


def test_store_add_is_idempotent_on_case_id(tmp_path):
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()
    candidate = loop.candidate_store.list_candidates()[0]

    assert loop.candidate_store.add(candidate) is False
    assert loop.candidate_store.count() == 1


def test_repeated_acceptance_keeps_one_case(tmp_path):
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()
    case_id = loop.candidate_store.list_candidates()[0].case_id

    _accept(loop, case_id)
    _accept(loop, case_id)

    assert len(_dataset_rows(loop.export_regression_dataset())) == 1


# ── model output is never ground truth ──────────────────────────────────────


def test_model_answer_never_reaches_the_dataset(tmp_path):
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()
    case_id = loop.candidate_store.list_candidates()[0].case_id
    _accept(loop, case_id)

    row = _dataset_rows(loop.export_regression_dataset())[0]
    serialized = json.dumps(row, ensure_ascii=False)

    assert MODEL_ANSWER not in serialized
    assert "answer" not in row


def test_retrieved_evidence_is_never_promoted_to_expected_evidence(tmp_path):
    """The documents behind a rejected answer are the failure, not the target."""
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()
    case_id = loop.candidate_store.list_candidates()[0].case_id
    _accept(loop, case_id)

    row = _dataset_rows(loop.export_regression_dataset())[0]
    assert "doc-wrong-1" not in row["expected_evidence"]
    assert "doc-wrong-2" not in row["expected_evidence"]
    assert row["provenance"]["observed_evidence_doc_ids"] == ["doc-wrong-1", "doc-wrong-2"]


def test_new_candidate_starts_without_an_expectation(tmp_path):
    """Nothing about the model output seeds the expectation."""
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()

    candidate = loop.candidate_store.list_candidates()[0]
    assert candidate.expected_behaviour == ""
    assert candidate.expected_evidence == []
    assert candidate.provenance["expected_behaviour_origin"] == "pending_human_annotation"
    assert candidate.provenance["expected_evidence_origin"] == "pending_human_annotation"
    assert candidate.provenance["model_answer_promoted"] is False


def test_human_correction_may_seed_expected_behaviour(tmp_path):
    """A correction is human-authored, so it is a legitimate starting expectation."""
    loop, _ = _reviewed_negative(
        tmp_path,
        _feedback(correction="两者可以在夜间交替使用，白天需注意防晒。"),
    )
    loop.collect_candidates()

    candidate = loop.candidate_store.list_candidates()[0]
    assert candidate.expected_behaviour == "两者可以在夜间交替使用，白天需注意防晒。"
    assert candidate.provenance["expected_behaviour_origin"] == "human_correction"


def test_correction_that_repeats_the_model_answer_is_not_seeded(tmp_path):
    """Guards against laundering the model's own answer into ground truth."""
    loop, _ = _reviewed_negative(tmp_path, _feedback(correction=MODEL_ANSWER))
    loop.collect_candidates()

    candidate = loop.candidate_store.list_candidates()[0]
    assert candidate.expected_behaviour == ""
    assert candidate.provenance["expected_behaviour_origin"] == "pending_human_annotation"

    with pytest.raises(MissingExpectationError):
        loop.candidate_store.set_review_status(candidate.case_id, ACCEPTED)


# ── provenance survives promotion ───────────────────────────────────────────


def test_provenance_is_preserved_through_promotion(tmp_path):
    loop, feedback_id = _reviewed_negative(tmp_path, _feedback(correction="人工纠正内容"))
    loop.collect_candidates()
    case_id = loop.candidate_store.list_candidates()[0].case_id
    before = dict(loop.candidate_store.require(case_id).provenance)

    _accept(loop, case_id)
    row = _dataset_rows(loop.export_regression_dataset())[0]

    assert row["provenance"] == before
    assert row["provenance"]["source_feedback_id"] == feedback_id
    assert row["provenance"]["source_feedback_rating"] == -1.0
    assert row["provenance"]["source_feedback_review_status"] == FEEDBACK_ACCEPTED
    assert row["provenance"]["source_feedback_source"] == "user"
    assert row["provenance"]["schema_version"] == "regression-candidate/1"
    assert row["provenance"]["generator"] == "offline.regression_candidates"


def test_provenance_survives_a_jsonl_round_trip(tmp_path):
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()
    case_id = loop.candidate_store.list_candidates()[0].case_id
    _accept(loop, case_id)

    stored = _dataset_rows(loop.export_regression_dataset())[0]
    assert stored["provenance"] == loop.candidate_store.require(case_id).provenance


# ── review API guards ───────────────────────────────────────────────────────


def test_unknown_case_id_fails_closed(tmp_path):
    loop, _ = _reviewed_negative(tmp_path)

    with pytest.raises(UnknownRegressionCase):
        loop.candidate_store.set_review_status("does-not-exist", REJECTED)
    with pytest.raises(UnknownRegressionCase):
        loop.candidate_store.set_expectations("does-not-exist", expected_behaviour="x")
    with pytest.raises(UnknownRegressionCase):
        loop.candidate_store.require("does-not-exist")


def test_invalid_review_status_is_rejected(tmp_path):
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()
    case_id = loop.candidate_store.list_candidates()[0].case_id

    with pytest.raises(ValueError):
        loop.candidate_store.set_review_status(case_id, "maybe")


def test_candidate_review_states_are_the_feedback_gate_terminal_states():
    """One reviewer vocabulary across both gates; only the pending state differs."""
    from offline.feedback_loop import ACCEPTED as GATE_ACCEPTED
    from offline.feedback_loop import REJECTED as GATE_REJECTED

    assert ACCEPTED == GATE_ACCEPTED
    assert REJECTED == GATE_REJECTED
    assert PENDING_REVIEW == "PENDING_REVIEW"


# ── export cycle ────────────────────────────────────────────────────────────


def test_run_export_cycle_reports_counts_and_files(tmp_path):
    loop, _ = _reviewed_negative(tmp_path)

    result = loop.run_export_cycle()

    assert result["candidates_created"] == 1
    assert result["candidates_total"] == 1
    assert result["pending_review"] == 1
    assert result["accepted"] == 0
    assert result["rejected"] == 0
    assert Path(result["review_queue"]).name == "regression_candidates.jsonl"
    assert Path(result["regression_dataset"]).name == "regression_dataset.jsonl"
    assert Path(result["review_queue"]).exists()
    assert Path(result["regression_dataset"]).exists()


def test_run_export_cycle_is_repeatable(tmp_path):
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()
    case_id = loop.candidate_store.list_candidates()[0].case_id
    _accept(loop, case_id)

    first = loop.run_export_cycle()
    second = loop.run_export_cycle()

    assert first["candidates_created"] == 0
    assert second["candidates_created"] == 0
    assert first["regression_dataset"] == second["regression_dataset"]
    assert len(_dataset_rows(second["regression_dataset"])) == 1


def test_export_cycle_can_include_approved_candidates_in_the_queue(tmp_path):
    loop, _ = _reviewed_negative(tmp_path)
    loop.collect_candidates()
    case_id = loop.candidate_store.list_candidates()[0].case_id
    _accept(loop, case_id)

    loop.run_export_cycle(status=None)

    queue = _dataset_rows(loop.export_candidates(status=None))
    assert [row["review_status"] for row in queue] == [ACCEPTED]
