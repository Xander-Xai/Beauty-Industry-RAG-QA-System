"""Tests for issue #86's unattributable-sample filter and integrity check.

The requirement is narrow and testable: a sample whose stable identity cannot be
produced must be **excluded**, not silently scored by normalized-text matching,
and the exclusion must be loud (recorded reasons, and a refusal when nothing is
left). These tests pin that behaviour at the function level and through the CLI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchmarks.dataset import filter_attributable_queries, load_queries
from benchmarks.golden_set_contract import (
    dataset_integrity_errors,
    select_attributable_rows,
)

VALID_CORPUS = "epoch_2026_10_09"


def _valid_row(sample_id: str, doc_id: str, chunk_id: str, text: str) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "question": f"question {sample_id}",
        "contexts": [text],
        "business_type": "regulation",
        "difficulty": "easy",
        "visual_required": False,
        "complexity_label": "simple",
        "corpus_version": VALID_CORPUS,
        "annotations": [{"doc_id": doc_id, "chunk_id": chunk_id, "text": text}],
        "annotation": {
            "annotator": "annotator-a",
            "method": "manual-passage-mapping",
            "annotated_at": "2026-10-09",
            "review_status": "REVIEWED",
            "source": "human",
            "reviewed_by": "reviewer-b",
        },
    }


def _committed_shape(sample_id: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "question": "q",
        "answer": "a",
        "ground_truth": "g",
        "contexts": ["some passage"],
        "business_type": "regulation",
        "difficulty": "easy",
    }
    if sample_id is not None:
        row["sample_id"] = sample_id
    return row


def _write(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    return path


# ── select_attributable_rows ────────────────────────────────────────────────


def test_select_attributable_rows_splits_and_records_reasons():
    rows = [_valid_row("s0", "d0", "c0", "alpha"), _committed_shape()]
    kept, excluded = select_attributable_rows(rows)

    assert len(kept) == 1
    assert kept[0]["sample_id"] == "s0"
    assert len(excluded) == 1
    reasons = set(excluded[0]["reasons"])
    # The committed shape lacks identity, labels, corpus version and provenance.
    assert {"missing_sample_id", "missing_annotations", "missing_visual_required_label"} <= reasons


# ── dataset_integrity_errors ────────────────────────────────────────────────


def test_integrity_flags_duplicate_sample_id():
    rows = [_valid_row("dup", "d0", "c0", "a"), _valid_row("dup", "d1", "c1", "b")]
    errors = dataset_integrity_errors(rows)
    assert any("duplicate_sample_id" in error["reason"] for error in errors)


def test_integrity_flags_duplicate_annotation_identity():
    row = _valid_row("s0", "d0", "c0", "a")
    row["annotations"] = [
        {"doc_id": "d0", "chunk_id": "c0", "text": "a"},
        {"doc_id": "d0", "chunk_id": "c0", "text": "a"},
    ]
    errors = dataset_integrity_errors([row])
    assert any("duplicate_annotation_identity" in error["reason"] for error in errors)


def test_integrity_is_silent_without_identifiers():
    """The committed dataset has no ids to compare, so there is nothing to flag."""
    assert dataset_integrity_errors([_committed_shape(), _committed_shape()]) == []


# ── filter_attributable_queries ─────────────────────────────────────────────


def test_filter_queries_preserves_sample_id_and_reports_exclusions(tmp_path):
    path = _write(tmp_path / "mixed.jsonl", [_valid_row("s0", "d0", "c0", "a"), _committed_shape()])
    queries = load_queries(path)
    # sample_id is the 1-based line index, so it is stable regardless of content.
    assert [q.sample_id for q in queries] == ["0000", "0001"]

    kept, excluded = filter_attributable_queries(queries)
    assert [q.sample_id for q in kept] == ["0000"]
    assert [entry["sample_id"] for entry in excluded] == ["0001"]
    assert excluded[0]["reasons"]


# ── CLI ─────────────────────────────────────────────────────────────────────


def _run_dir(out: Path) -> Path:
    return sorted(path for path in out.iterdir() if path.is_dir())[-1]


def _metadata(out: Path) -> dict[str, Any]:
    return json.loads((_run_dir(out) / "metadata.json").read_text(encoding="utf-8"))


def test_cli_drop_unattributable_refuses_when_nothing_remains(tmp_path, monkeypatch, capsys):
    from benchmarks import retrieval_benchmark as cli

    monkeypatch.setattr(cli, "git_provenance", lambda root: ("abc123", False))
    exit_code = cli.main(
        [
            "--config",
            "bm25",
            "--limit",
            "3",
            "--drop-unattributable",
            "--dataset",
            "tests/evaluation/golden_set.jsonl",
            "--output-dir",
            str(tmp_path),
            "--allow-dirty",
        ]
    )
    assert exit_code == 2
    # A refused run must not leave a scored artifact behind.
    assert not list(tmp_path.iterdir())
    stderr = capsys.readouterr().err
    assert "removed all 3 samples" in stderr
    assert "refusing to score" in stderr
    assert "down-graded to normalized-text matching" in stderr


def test_cli_drop_unattributable_keeps_valid_rows_and_records_exclusions(tmp_path, monkeypatch):
    from benchmarks import retrieval_benchmark as cli

    monkeypatch.setattr(cli, "git_provenance", lambda root: ("abc123", False))
    path = _write(tmp_path / "mixed.jsonl", [_valid_row("s0", "d0", "c0", "a"), _committed_shape()])
    out = tmp_path / "out"
    exit_code = cli.main(
        [
            "--config",
            "bm25",
            "--drop-unattributable",
            "--dataset",
            str(path),
            "--output-dir",
            str(out),
            "--allow-dirty",
        ]
    )
    assert exit_code == 0
    metadata = _metadata(out)
    assert metadata["sample_count"] == 1
    # The kept row is fully attributable...
    assert metadata["attributable"] is True
    assert metadata["dataset_contract"]["valid_count"] == 1
    # ...and the exclusion is recorded, not silent.
    filter_info = metadata["unattributable_filter"]
    assert filter_info["applied"] is True
    assert filter_info["original_sample_count"] == 2
    assert filter_info["dropped"] == 1
    assert filter_info["excluded"][0]["sample_id"] == "0001"
    # No real executor was wired, so this is still not a benchmark result.
    assert metadata["results_are_benchmark"] is False


def test_cli_defaults_do_not_filter(tmp_path, monkeypatch):
    from benchmarks import retrieval_benchmark as cli

    monkeypatch.setattr(cli, "git_provenance", lambda root: ("abc123", False))
    out = tmp_path / "out"
    cli.main(
        [
            "--config",
            "bm25",
            "--limit",
            "2",
            "--dataset",
            "tests/evaluation/golden_set.jsonl",
            "--output-dir",
            str(out),
            "--allow-dirty",
        ]
    )
    metadata = _metadata(out)
    assert metadata["unattributable_filter"]["applied"] is False
    assert metadata["unattributable_filter"]["dropped"] == 0
    assert metadata["sample_count"] == 2


def test_cli_require_integrity_refuses_duplicate_ids(tmp_path, monkeypatch):
    from benchmarks import retrieval_benchmark as cli

    monkeypatch.setattr(cli, "git_provenance", lambda root: ("abc123", False))
    rows = [_valid_row("dup", "d0", "c0", "a"), _valid_row("dup", "d1", "c1", "b")]
    path = _write(tmp_path / "dupes.jsonl", rows)
    out = tmp_path / "out"
    exit_code = cli.main(
        [
            "--config",
            "bm25",
            "--require-integrity",
            "--dataset",
            str(path),
            "--output-dir",
            str(out),
            "--allow-dirty",
        ]
    )
    assert exit_code == 2
    assert not out.exists()
