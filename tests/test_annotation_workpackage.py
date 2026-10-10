"""The annotation work package must be a worksheet, never a smuggled label set.

The risk this guards against: a "candidate" package that is structurally
indistinguishable from a reviewed dataset, so an unreviewed machine proposal
scores like ground truth. Every assertion here fails if the package starts
carrying a review decision, an annotator name, a date, or a scorable flag.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKPACKAGE = REPO_ROOT / "tests/evaluation/golden_set_v2/annotation_workpackage_v1.jsonl"
GOLDEN = REPO_ROOT / "tests/evaluation/golden_set.jsonl"

sys.path.insert(0, str(REPO_ROOT))

from scripts.validation.build_annotation_workpackage import (  # noqa: E402
    CATEGORIES,
    build_records,
    category_counts,
    primary_category,
)


@pytest.fixture(scope="module")
def records():
    from benchmarks.dataset import load_rows

    return build_records(load_rows(GOLDEN))


def test_every_row_is_unscorable(records):
    assert records, "work package must not be empty"
    assert all(record["scorable"] is False for record in records)
    assert all(record["blocking_reasons"] for record in records)


def test_no_row_fabricates_a_review_or_an_annotator(records):
    for record in records:
        candidate = record["candidate"]
        assert candidate["source"] == "llm_candidate"
        assert candidate["review_status"] == "DRAFT_UNVERIFIED"
        # The fields that would turn a candidate into a reviewed record are empty.
        assert candidate["annotator"] == ""
        assert candidate["reviewed_by"] == ""
        assert candidate["reviewed_at"] == ""
        assert candidate["annotated_at"] == ""


def test_no_row_fabricates_identity_or_corpus_fields(records):
    """doc_id / chunk_id / corpus fields must be absent, never invented."""

    def _walk(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in ("doc_id", "chunk_id", "corpus_version", "corpus_sha256"):
                    raise AssertionError(f"fabricated field {key!r} present")
                _walk(value)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    for record in records:
        _walk(record)


def test_missing_categories_are_reported_not_faked(records):
    counts = category_counts(records)
    # The committed set has no unanswerable/conflict sample. The package must say
    # so (zero rows) rather than invent one.
    assert counts["no_answer_or_conflict"] == 0
    for name in CATEGORIES:
        if name != "no_answer_or_conflict":
            assert counts[name] > 0, f"category {name} has no coverage"


def test_build_is_deterministic(records):
    from benchmarks.dataset import load_rows

    assert build_records(load_rows(GOLDEN)) == records


def test_category_assignment_does_not_invent_a_category():
    # A row that fits nothing must be dropped, not forced into a bucket.
    orphan = {"question": "一个不属于任何类别的问题", "business_type": "general", "contexts": ["x"]}
    assert primary_category(orphan) is None


def test_generated_file_matches_the_generator(records):
    """The committed worksheet is the generator's output, not hand-edited."""
    written = [json.loads(line) for line in WORKPACKAGE.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert written == records


def test_cli_writes_a_package_that_is_unscorable(tmp_path):
    out = tmp_path / "wp.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/validation/build_annotation_workpackage.py"),
            "--dataset",
            str(GOLDEN),
            "--out",
            str(out),
            "--markdown",
            str(tmp_path / "wp.md"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows
    assert all(row["scorable"] is False for row in rows)
