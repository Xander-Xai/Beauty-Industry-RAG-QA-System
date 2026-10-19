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
from pathlib import Path

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
