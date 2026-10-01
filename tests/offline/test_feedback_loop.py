"""Unified feedback loop review-gating and export tests."""

from __future__ import annotations

from offline.feedback_loop import (
    ACCEPTED,
    PENDING,
    FeedbackLoop,
    FeedbackRecord,
    make_feedback_id,
)


def _loop(tmp_path):
    return FeedbackLoop(tmp_path / "feedback.sqlite3", output_dir=tmp_path / "out")


def _record(query="how to use retinol", rating=1.0, correction="", answer="Apply at night."):
    return FeedbackRecord(
        feedback_id=make_feedback_id(query, "req-1"),
        query=query,
        answer=answer,
        evidence_doc_ids=["doc-a"],
        request_id="req-1",
        session_ref="hashed-session",
        rating=rating,
        correction=correction,
        business_type="ingredient",
        intent="how_to",
    )


def test_add_deduplicates_and_defaults_to_pending(tmp_path):
    loop = _loop(tmp_path)
    assert loop.store.add(_record()) is True
    assert loop.store.add(_record()) is False
    assert loop.store.count(PENDING) == 1
    assert loop.store.list_records()[0].review_status == PENDING


def test_review_gating_controls_training_exports(tmp_path):
    loop = _loop(tmp_path)
    loop.store.add(_record(query="q1", rating=-1.0, correction="pending rewrite"))
    loop.store.add(_record(query="q2", rating=1.0, correction="accepted correction"))
    records = loop.store.list_records()
    loop.store.set_review_status(records[1].feedback_id, ACCEPTED)
    # first record stays pending

    datasets = loop.export_datasets()
    accepted_only = (tmp_path / "out" / "rewrite_corrections.jsonl").read_text(encoding="utf-8")
    assert "accepted correction" in accepted_only
    assert "pending rewrite" not in accepted_only
    assert set(datasets) == {"hard_negatives", "rewrite_corrections", "evaluation", "qlora_dpo"}


def test_review_queue_and_full_cycle(tmp_path):
    loop = _loop(tmp_path)
    loop.store.add(_record())
    result = loop.run_full_feedback_cycle(days=1)
    assert result["pending"] == 1
    assert (tmp_path / "out" / "review_queue.jsonl").exists()


def test_invalid_review_status_rejected(tmp_path):
    loop = _loop(tmp_path)
    record = _record()
    loop.store.add(record)
    import pytest

    with pytest.raises(ValueError):
        loop.store.set_review_status(record.feedback_id, "maybe")
