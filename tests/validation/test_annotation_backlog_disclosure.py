"""The human annotation backlog must disclose its own data provenance.

`scripts/validation/audit_annotations.py` writes a Markdown work order that
quotes golden-set `question` text verbatim. That file is the one most likely to
be forwarded on its own — to an annotator, into an issue, to a reviewer — and a
sidecar does not travel with a forwarded Markdown file. So the work order has to
say what its text is, above the table it describes.

The failure this guards is not "the warning is missing". It is the subtler one:
a warning that is *wrong*. A backlog pointed at an unregistered corpus must not
claim its text is synthetic, and one pointed at the committed fixtures must not
claim it is verified regulatory material. Both are affirmative claims about a
dataset, and the reassuring one is the more dangerous.

The committed file is regenerated from the real writer and asserted byte-for-byte
against the committed artifact, so a change to the disclosure cannot drift away
from the checked-in Markdown.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.provenance import (
    SYNTHETIC_DATA_CLASS,
    SYNTHETIC_DATA_NOTICE,
    UNCLASSIFIED_DATA_CLASS,
    UNCLASSIFIED_DATA_NOTICE,
)
from scripts.validation.audit_annotations import build_backlog

REPO_ROOT = Path(__file__).resolve().parents[2]

GOLDEN_SET = "tests/evaluation/golden_set.jsonl"
ANNOTATIONS = "tests/evaluation/golden_set_v2/annotations_v1.jsonl"
COMMITTED_BACKLOG = REPO_ROOT / "tests/evaluation/golden_set_v2/HUMAN_ANNOTATION_BACKLOG.md"


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return path


@pytest.fixture
def empty_annotations(tmp_path: Path) -> str:
    """An annotation file with no records — the committed state."""
    path = tmp_path / "annotations.jsonl"
    path.write_text("", encoding="utf-8")
    return str(path)


# --- the registered synthetic fixture gets a real warning -------------------


def test_registered_synthetic_dataset_is_labelled_synthetic(empty_annotations: str) -> None:
    markdown = build_backlog(empty_annotations, GOLDEN_SET)

    assert f"**data class**: `{SYNTHETIC_DATA_CLASS}`" in markdown
    # The notice is the whole point: fiction, plus the uses it must not be put to.
    assert "虚构占位值" in markdown
    assert "不得用于配方开发" in markdown
    assert "must not be used for formulation" in markdown


def test_disclosure_precedes_the_question_table(empty_annotations: str) -> None:
    """A reader who stops at the first screen must still have been told."""
    markdown = build_backlog(empty_annotations, GOLDEN_SET)

    notice_at = markdown.index("虚构占位值")
    table_at = markdown.index("| sample_id | question")
    heading_at = markdown.index("## 1. Samples needing a first annotation pass")

    assert markdown.index("# Human annotation backlog") < notice_at
    assert notice_at < heading_at < table_at


def test_disclosure_names_the_source_dataset_and_its_digest(empty_annotations: str) -> None:
    """A forwarded file has to be traceable back to an exact source revision."""
    from benchmarks.annotation import dataset_sha256

    markdown = build_backlog(empty_annotations, GOLDEN_SET)

    assert f"`{GOLDEN_SET}`" in markdown
    assert dataset_sha256(REPO_ROOT / GOLDEN_SET) in markdown


# --- unknown sources must not be called anything -----------------------------


def test_unregistered_source_is_unclassified_not_synthetic(tmp_path: Path, empty_annotations: str) -> None:
    """The requirement that matters most: unknown is never rounded to SYNTHETIC."""
    source = _write_jsonl(
        tmp_path / "licensed-corpus.jsonl",
        [{"question": "化妆品备案需要哪些材料？", "contexts": [], "answer": "", "ground_truth": ""}],
    )

    markdown = build_backlog(empty_annotations, str(source))

    assert f"**data class**: `{UNCLASSIFIED_DATA_CLASS}`" in markdown
    assert SYNTHETIC_DATA_CLASS not in markdown
    assert "虚构占位值" not in markdown
    assert "不得将其断言为合成数据" in markdown


def test_unregistered_source_is_never_called_verified_regulation(tmp_path: Path, empty_annotations: str) -> None:
    """The reassuring direction fails too: 'unclassified' is not 'legitimate'.

    Substring-matching for "已核验的真实法规" would be the wrong tool — the
    unclassified notice contains that phrase *inside a negation* ("must not assert
    it as verified regulatory material"). What must be true is about the
    classification, not the wording.
    """
    source = _write_jsonl(tmp_path / "operator-corpus.jsonl", [{"question": "烟酰胺限量是多少？"}])

    markdown = build_backlog(empty_annotations, str(source))

    assert f"**data class**: `{UNCLASSIFIED_DATA_CLASS}`" in markdown
    assert "carries real regulatory claims**: unknown" in markdown
    # It carries the "unconfirmed" notice, and specifically not the synthetic one.
    assert UNCLASSIFIED_DATA_NOTICE in markdown
    assert SYNTHETIC_DATA_NOTICE not in markdown


def test_path_outside_the_repository_is_unclassified(tmp_path: Path, empty_annotations: str) -> None:
    """A path under /tmp cannot be a registered asset even if it looks repo-like."""
    relative_lookalike = tmp_path / "tests" / "evaluation"
    relative_lookalike.mkdir(parents=True)
    source = _write_jsonl(relative_lookalike / "golden_set.jsonl", [{"question": "什么是 INCI 名称？"}])

    markdown = build_backlog(empty_annotations, str(source))

    assert f"**data class**: `{UNCLASSIFIED_DATA_CLASS}`" in markdown
    assert SYNTHETIC_DATA_CLASS not in markdown


# --- the existing contract must survive the new block -----------------------


def test_counts_ordering_and_review_statuses_are_preserved(empty_annotations: str) -> None:
    """Adding a disclosure must not disturb what the backlog actually reports."""
    markdown = build_backlog(empty_annotations, GOLDEN_SET)

    assert "- annotation file: `" in markdown
    assert "- reviewed records: 0" in markdown
    assert "- unreviewed drafts: 0" in markdown
    assert "- fully reviewed and scorable: 0" in markdown
    assert "- samples with no annotation record yet: 301" in markdown
    rows = [line for line in markdown.splitlines() if line.startswith("| `0")]
    assert rows[0].startswith("| `0000` |")
    assert len(rows) == 60  # the documented 60-row cap is unchanged


def test_empty_annotation_set_still_renders_a_disclosure(empty_annotations: str) -> None:
    """The committed state is an empty annotation file. Silence is the failure."""
    markdown = build_backlog(empty_annotations, GOLDEN_SET)

    assert "data class" in markdown
    assert "虚构占位值" in markdown
    assert "no labels" in markdown


def test_a_draft_record_is_not_promoted_to_reviewed(tmp_path: Path) -> None:
    """Adding provenance must not become a way to advance review state."""
    annotations = _write_jsonl(
        tmp_path / "annotations.jsonl",
        [
            {
                "sample_id": "0000",
                "question": "化妆品备案需要哪些材料？",
                "corpus_version": "v1",
                "corpus_sha256": "0" * 64,
                "visual_required": False,
                "annotations": [{"doc_id": "d0", "chunk_id": "c0", "text": "t", "relevance": 2}],
                "annotation": {
                    "annotator": "a",
                    "method": "m",
                    "annotated_at": "2026-01-01",
                    "review_status": "DRAFT_UNVERIFIED",
                    "source": "llm_candidate",
                },
            }
        ],
    )

    markdown = build_backlog(str(annotations), GOLDEN_SET)

    assert "- reviewed records: 0" in markdown
    assert "- unreviewed drafts: 1" in markdown
    assert "REQUIRES_HUMAN_REVIEW" in markdown


# --- standalone propagation --------------------------------------------------


def test_forwarded_file_says_what_it_is_with_no_sidecar_present(empty_annotations: str) -> None:
    """Cut the file at the first table: the classification still came through.

    This is the scenario the whole change exists for. Everything a receiver of a
    forwarded fragment can see is the first screenful, so the answer has to be in
    it rather than in a document the receiver never received.
    """
    markdown = build_backlog(empty_annotations, GOLDEN_SET)
    fragment = markdown.split("## 1.")[0]

    assert SYNTHETIC_DATA_CLASS in fragment
    assert "虚构占位值" in fragment
    assert GOLDEN_SET in fragment
    # No sidecar link is needed for the statement to be complete on its own.
    assert "DATA_PROVENANCE.md" not in fragment


def test_committed_backlog_matches_the_writer() -> None:
    """The checked-in file must be exactly what the tool produces.

    Otherwise CI keeps passing on a disclosure nobody actually receives.
    """
    regenerated = build_backlog(ANNOTATIONS, GOLDEN_SET)

    assert regenerated == COMMITTED_BACKLOG.read_text(encoding="utf-8")
