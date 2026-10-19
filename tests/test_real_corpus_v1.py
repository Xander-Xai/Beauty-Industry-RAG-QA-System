"""Determinism and identity checks for real corpus V1 and its candidates.

These tests assert the two properties the corpus bundle exists to provide:

* the committed chunk view is reproducible from the committed source records;
* every candidate annotation points at a real ``(doc_id, chunk_id)`` in that
  view, and none of the candidates claims to be human-reviewed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from offline.source_trust import file_content_hash

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = REPO_ROOT / "corpus" / "real_v1"
GOLDEN_DIR = REPO_ROOT / "tests" / "evaluation" / "golden_set_v1"


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_build_is_reproducible(tmp_path: Path) -> None:
    committed_manifest = json.loads((CORPUS_DIR / "manifest.json").read_text(encoding="utf-8"))
    committed_chunks = _load_jsonl(CORPUS_DIR / "chunks.jsonl")

    subprocess.run(
        [
            sys.executable,
            "scripts/corpus/build_real_corpus_v1.py",
            "--sources-dir",
            str(CORPUS_DIR / "sources"),
            "--out-dir",
            str(tmp_path / "out"),
            "--generated-dir",
            str(tmp_path / "generated"),
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    rebuilt_manifest = json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))
    rebuilt_chunks = _load_jsonl(tmp_path / "out" / "chunks.jsonl")

    assert rebuilt_manifest["corpus_sha256_local"] == committed_manifest["corpus_sha256_local"]
    assert rebuilt_manifest["counts"] == committed_manifest["counts"]
    assert rebuilt_chunks == committed_chunks


def test_every_candidate_annotation_resolves_to_a_committed_chunk() -> None:
    chunks = {chunk["chunk_id"]: chunk for chunk in _load_jsonl(CORPUS_DIR / "chunks.jsonl")}
    candidates = _load_jsonl(GOLDEN_DIR / "candidates_v1.jsonl")
    assert candidates, "candidate file must not be empty"

    for candidate in candidates:
        assert candidate["annotations"], candidate["sample_id"]
        for annotation in candidate["annotations"]:
            stored = chunks.get(annotation["chunk_id"])
            assert stored is not None, f"{candidate['sample_id']}: unknown chunk {annotation['chunk_id']}"
            assert stored["doc_id"] == annotation["doc_id"]
            assert stored["text"] == annotation["text"]


def test_candidates_are_drafts_and_never_scorable() -> None:
    candidates = _load_jsonl(GOLDEN_DIR / "candidates_v1.jsonl")
    for candidate in candidates:
        assert candidate["annotation"]["review_status"] == "DRAFT_UNVERIFIED"
        assert candidate["annotation"]["source"] == "llm_candidate"
        assert "reviewed_by" not in candidate["annotation"]
        assert "reviewed_at" not in candidate["annotation"]


def test_committed_candidates_are_reproducible_from_the_generator() -> None:
    """The committed drafts must equal what the generator produces right now.

    Corrections are made in ``build_real_golden_set_v1.py`` and regenerated, so a
    hand-edited artifact that drifts from its generator would silently become a
    second source of truth.
    """
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [
                sys.executable,
                "scripts/validation/build_real_golden_set_v1.py",
                "--out-dir",
                tmp,
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
        assert _load_jsonl(Path(tmp) / "candidates_v1.jsonl") == _load_jsonl(GOLDEN_DIR / "candidates_v1.jsonl")
        assert _load_jsonl(Path(tmp) / "no_answer_candidates_v1.jsonl") == _load_jsonl(
            GOLDEN_DIR / "no_answer_candidates_v1.jsonl"
        )


def test_generator_refuses_to_overwrite_a_reviewed_record() -> None:
    """Regeneration is how a correction lands, so it must not eat a real review."""
    from scripts.validation import build_real_golden_set_v1 as builder

    reviewed = _load_jsonl(GOLDEN_DIR / "candidates_v1.jsonl")[0]
    reviewed["annotation"] = dict(reviewed["annotation"])
    reviewed["annotation"]["review_status"] = "REVIEWED"
    reviewed["annotation"]["reviewed_by"] = "someone-real"
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "candidates_v1.jsonl"
        target.write_text(json.dumps(reviewed, ensure_ascii=False) + "\n", encoding="utf-8")
        with pytest.raises(SystemExit, match="refusing to overwrite"):
            builder.assert_no_human_annotations(target)

    # The committed drafts are all still drafts, so regeneration stays allowed.
    builder.assert_no_human_annotations(GOLDEN_DIR / "candidates_v1.jsonl")


def test_workpackage_carries_ai_recommendations_but_no_human_verdicts() -> None:
    """A reviewer must be able to see what the AI concluded and what is still blank."""
    records = _load_jsonl(GOLDEN_DIR / "retrieval_review_workpackage.jsonl")
    assert len(records) == len(_load_jsonl(GOLDEN_DIR / "candidates_v1.jsonl"))
    for record in records:
        assert record["review_status"] == "DRAFT_UNVERIFIED"
        assert record["scorable"] is False
        assert record["ai_suggestion"] in {"ACCEPT", "MODIFY", "REJECT"}
        review = record["review"]
        assert review["decision"] == ""
        assert review["reviewer"] == ""
        assert review["reviewed_at"] == ""
        assert review["notes"] == ""


def test_paraphrase_pairs_are_marked_and_not_treated_as_duplicates() -> None:
    records = {record["sample_id"]: record for record in _load_jsonl(GOLDEN_DIR / "retrieval_review_workpackage.jsonl")}
    for variant, original in (
        ("REAL-V1-018", "REAL-V1-001"),
        ("REAL-V1-019", "REAL-V1-003"),
        ("REAL-V1-020", "REAL-V1-010"),
    ):
        record = records[variant]
        assert record["query_family"] == original, variant
        assert record["derivation_risk"]["level"] in {"LOW", "MEDIUM"}, variant
        assert record["question"] != records[original]["question"], variant


def test_ingredient_questions_pin_the_2021_snapshot() -> None:
    """The usage column was withdrawn in 2025, so no question may read as a current cap."""
    candidates = {c["sample_id"]: c for c in _load_jsonl(GOLDEN_DIR / "candidates_v1.jsonl")}
    for sample_id in ("REAL-V1-010", "REAL-V1-012", "REAL-V1-013", "REAL-V1-014", "REAL-V1-015", "REAL-V1-016"):
        question = candidates[sample_id]["question"]
        assert "2021年版" in question, sample_id
        assert candidates[sample_id]["business_type"] == "ingredient"
    for sample_id in ("REAL-V1-011", "REAL-V1-017", "REAL-V1-025", "REAL-V1-027"):
        assert "2021年版" in candidates[sample_id]["question"], sample_id


def test_corpus_sha256_binds_candidates_to_the_index_fingerprint() -> None:
    fingerprint = json.loads((CORPUS_DIR / "es_fingerprint.json").read_text(encoding="utf-8"))
    expected = fingerprint["fingerprint"]["content_sha256"]
    for candidate in _load_jsonl(GOLDEN_DIR / "candidates_v1.jsonl"):
        assert candidate["corpus_version"] == fingerprint["corpus_version"]
        assert candidate["corpus_sha256"] == expected


def test_no_answer_candidates_have_no_matching_chunk() -> None:
    records = _load_jsonl(GOLDEN_DIR / "no_answer_candidates_v1.jsonl")
    assert records, "no-answer file must not be empty"
    for record in records:
        assert record["answerable"] is False
        assert record["expected_behavior"] == "refuse"
        assert record["matched_chunk_ids"] == []


def test_source_files_still_match_the_hashes_the_approval_binds() -> None:
    """Documentation edits must not disturb the bytes an approval is bound to.

    ``offline.source_trust.file_content_hash`` hashes the committed source file,
    so an approval names exactly these bytes. Editing SOURCES.md is safe;
    editing a source file would silently return both sources to PENDING_REVIEW.
    """
    manifest = json.loads((CORPUS_DIR / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["source_files"].items():
        actual = file_content_hash(CORPUS_DIR / "sources" / name)
        assert actual == expected, f"{name} changed; a prior approval no longer covers these bytes"


def test_sources_md_discloses_regulatory_currency_limits() -> None:
    """The corpus must not let a 2021 snapshot be read as current law.

    Each marker below is a fact that changed after this corpus was extracted and
    that a reader cannot derive from the corpus text itself, so dropping any of
    them from SOURCES.md would reintroduce a materially misleading silence.
    """
    text = (CORPUS_DIR / "SOURCES.md").read_text(encoding="utf-8")

    # Article 78's transition period expired and its implementing notice is absent.
    assert "2025-12-31" in text
    assert "2021年第150号" in text
    assert "第七十八条" in text

    # The catalog was restructured and the usage field was withdrawn.
    assert "2025年第61号" in text
    assert "目录》Ⅰ" in text and "目录》Ⅱ" in text
    assert "最高历史使用量" in text

    # Each dynamic adjustment is dated, so the timeline stays checkable.
    for date in ("2025-10-20", "2026-01-04", "2026-04-13", "2026-08-19"):
        assert date in text, f"missing dynamic-adjustment date {date}"

    # The subset is not the whole catalog, and absence here proves nothing.
    assert "8965" in text
    assert "127" in text
