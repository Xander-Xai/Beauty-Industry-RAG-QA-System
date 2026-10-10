"""Documentation-vs-evidence consistency for the Golden Set contract result.

The defect this prevents
------------------------
`docs/validation/rag-eval-readiness.md` quoted the benchmark CLI's refusal as
``0/2 samples ... refusing to score``. That number came from a ``--limit 2`` run
and was presented as the state of a 301-row dataset. Nothing failed: the
validator is correct, the documentation was simply stale, and no test compared
the two.

These tests re-measure the contract result and assert the documents state the
measured values. They deliberately re-run the real validator rather than
hard-coding 301/0, so a dataset change surfaces as a failing test that asks for
a documentation update instead of as silent drift.

They also pin the reporting discipline: a scoped ``--limit`` denominator may be
quoted only when it is labelled as scoped.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = Path("tests/evaluation/golden_set.jsonl")
VALIDATOR = Path("scripts/validation/validate_golden_set_contract.py")

RAG_READINESS = Path("docs/validation/rag-eval-readiness.md")
DATA_QUALITY = Path("docs/benchmark-data-quality.md")
BENCHMARK_DATA_QUALITY_DOCS = (RAG_READINESS, DATA_QUALITY)


def _run_validator(*extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--dataset", str(DATASET), *extra],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


@pytest.fixture(scope="module")
def measurement() -> dict[str, str | int]:
    """Re-measure the contract result from the real validator."""
    from benchmarks.golden_set_contract import dataset_sha256

    completed = _run_validator()
    stdout = completed.stdout
    parsed = {
        "sha256": re.search(r"^sha256:\s+(\w+)", stdout, re.M).group(1),
        "total": int(re.search(r"^total:\s+(\d+)", stdout, re.M).group(1)),
        "valid": int(re.search(r"^valid:\s+(\d+)", stdout, re.M).group(1)),
        "invalid": int(re.search(r"^invalid:\s+(\d+)", stdout, re.M).group(1)),
        "exit_code": completed.returncode,
    }
    assert parsed["sha256"] == dataset_sha256(REPO_ROOT / DATASET)
    return parsed


# ── the measured facts themselves ─────────────────────────────────────────────


def test_committed_dataset_is_still_the_one_that_was_measured(measurement):
    """If the dataset changes, this fails and asks for a doc refresh."""
    assert measurement["total"] == 301
    assert measurement["sha256"] == "36cdaf4452a573eb77856c7c406ac091898f9895c2d7b5cd034348f2c4aac7df"


def test_contract_result_is_still_zero_of_301(measurement):
    """The 0 is the honest result; it is not "fixed" by loosening the contract."""
    assert (measurement["valid"], measurement["invalid"]) == (0, 301)


def test_validator_still_exits_non_zero(measurement):
    assert measurement["exit_code"] == 1


def test_cli_gate_reports_the_whole_dataset_denominator(tmp_path):
    """The number quoted in the docs must be the number the command prints."""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.retrieval_benchmark",
            "--config",
            "bm25",
            "--require-contract",
            "--dataset",
            str(DATASET),
            "--output-dir",
            str(tmp_path / "out"),
            "--allow-dirty",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert completed.returncode == 2
    assert "0/301 samples satisfy the golden-set-contract/v2 contract" in completed.stderr
    assert "refusing to score" in completed.stderr


def test_scoped_limit_reports_its_own_denominator(tmp_path):
    """`--limit 2` really does print 0/2 — which is exactly why it is scoped."""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.retrieval_benchmark",
            "--config",
            "bm25",
            "--limit",
            "2",
            "--require-contract",
            "--dataset",
            str(DATASET),
            "--output-dir",
            str(tmp_path / "out"),
            "--allow-dirty",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert completed.returncode == 2
    assert "0/2 samples satisfy" in completed.stderr


# ── the documents must agree with the measurement ────────────────────────────


@pytest.mark.parametrize("doc", BENCHMARK_DATA_QUALITY_DOCS)
def test_document_states_the_measured_dataset_total(doc):
    text = (REPO_ROOT / doc).read_text(encoding="utf-8")
    assert "301" in text, f"{doc} no longer states the dataset size"


@pytest.mark.parametrize("doc", BENCHMARK_DATA_QUALITY_DOCS)
def test_document_states_zero_contract_valid(doc):
    """Both spellings (`0 / 301`, `0/301`) must describe the same zero."""
    text = (REPO_ROOT / doc).read_text(encoding="utf-8")
    assert re.search(r"0\s*/\s*301", text), f"{doc} does not state 0/301 contract-valid rows"


def test_rag_readiness_states_the_measured_sha256():
    text = (REPO_ROOT / RAG_READINESS).read_text(encoding="utf-8")
    from benchmarks.golden_set_contract import dataset_sha256

    assert dataset_sha256(REPO_ROOT / DATASET) in text


@pytest.mark.parametrize("doc", BENCHMARK_DATA_QUALITY_DOCS)
def test_no_document_quotes_a_scoped_denominator_as_the_dataset_state(doc):
    """`0/2` may only appear next to an explicit `--limit` marker."""
    text = (REPO_ROOT / doc).read_text(encoding="utf-8")
    for match in re.finditer(r"0\s*/\s*2\b", text):
        window = text[max(0, match.start() - 400) : match.end() + 200]
        assert "--limit" in window, (
            f"{doc} quotes {match.group(0)!r} as the dataset state; "
            "a scoped --limit denominator must be labelled as scoped"
        )


def test_rag_readiness_records_both_exit_codes():
    text = (REPO_ROOT / RAG_READINESS).read_text(encoding="utf-8")
    assert "exit 1" in text, "the validator exit code must be recorded"
    assert "exit 2" in text, "the benchmark CLI gate exit code must be recorded"


def test_toolchain_and_data_status_are_distinguished():
    """`VERIFIED_CODE` for the toolchain; the data stays unusable."""
    text = (REPO_ROOT / RAG_READINESS).read_text(encoding="utf-8")
    # Normalise the hard line wrapping so the assertion states meaning, not layout.
    flat = re.sub(r"\s+", " ", text)
    assert "VERIFIED_CODE" in text
    assert re.search(r"unusable for official retrieval-quality scoring", flat, re.I)


def test_documents_do_not_claim_a_retrieval_score_was_produced():
    """No document may imply a real Recall/NDCG number exists for this dataset."""
    forbidden = re.compile(r"recall@\d+\s*(?:=|:|of)\s*(?:0?\.\d+|1\.0)", re.I)
    for doc in BENCHMARK_DATA_QUALITY_DOCS:
        text = (REPO_ROOT / doc).read_text(encoding="utf-8")
        assert not forbidden.search(text), f"{doc} states a retrieval score for a non-attributable dataset"
