"""End-to-end integration checks for the retrieval benchmark's blocking path.

These run the real CLI against the committed dataset and assert the two
properties that matter most:

1. a real run against the committed golden set never produces a retrieval score,
   because that dataset has no attributable ground truth; and
2. the artifact records *why*, with the measured corpus correspondence rather
   than a hardcoded refusal.

They are marked ``integration`` but need no external service: the corpus probe is
stubbed, so the outcome is deterministic on any host. A test that depended on
whether a live Elasticsearch happened to be running would not be testing this.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks import retrieval_benchmark as cli

pytestmark = pytest.mark.integration

COMMITTED = "tests/evaluation/golden_set.jsonl"


@pytest.fixture(autouse=True)
def stub_corpus_inspection(monkeypatch, request):
    """Deterministic corpus evidence unless a test opts out.

    The real probe issues Elasticsearch/Qdrant queries. These tests assert the
    *blocking* behaviour, which must not depend on whether the host happens to
    have an index running. ``test_the_real_probe_measures_the_live_corpus`` opts
    out to exercise the genuine code path.
    """
    if "real_corpus_inspection" in request.keywords:
        return

    def _inspection(passages, total_passages=0, **kwargs):
        from benchmarks.corpus import (
            CORRESPONDENCE_MET_FULL,
            CorpusFingerprint,
            CorpusInspection,
            CorrespondenceReport,
        )

        fingerprint = CorpusFingerprint("stub", "stub", 0, 0, "0" * 64, True)
        report = CorrespondenceReport(
            passages_total=total_passages,
            passages_distinct=len(set(passages)),
            passages_checked=len(passages),
            passages_matched=len(passages),
            ratio=1.0,
            verdict=CORRESPONDENCE_MET_FULL,
            sample_hash="0" * 64,
            corpus=fingerprint,
        )
        return CorpusInspection(fingerprints=(fingerprint,), report=report)

    monkeypatch.setattr(cli, "inspect_correspondence", _inspection)
    from benchmarks import backends
    from benchmarks.models import BackendAvailability

    monkeypatch.setattr(
        backends,
        "probe_corpus",
        lambda count, golden_passages=None: BackendAvailability("corpus", True, backends.REASON_OK, "stubbed"),
    )


def _run_dir(out: Path) -> Path:
    return sorted(path for path in out.iterdir() if path.is_dir())[-1]


def test_committed_golden_set_is_blocked_and_exits_nonzero(tmp_path, monkeypatch):
    """A dataset with no attributable ground truth must not yield a score."""
    monkeypatch.setattr(cli, "git_provenance", lambda root: ("abc123", False))
    exit_code = cli.main(
        [
            "--configs",
            "bm25,dense,hybrid_rrf",
            "--dataset",
            COMMITTED,
            "--output-dir",
            str(tmp_path),
            "--allow-dirty",
            "--require-results",
        ]
    )
    # --require-results turns "nothing executed" into a hard failure.
    assert exit_code == 5

    metadata = json.loads((_run_dir(tmp_path) / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["results_are_benchmark"] is False
    assert metadata["synthetic_retriever"] is False
    assert metadata["attributable"] is False
    assert metadata["sample_count"] == 301


def test_blocked_artifact_still_records_the_corpus_inspection(tmp_path, monkeypatch):
    """'Blocked because the corpus lacks the ground truth' needs evidence attached."""
    monkeypatch.setattr(cli, "git_provenance", lambda root: ("abc123", False))
    cli.main(["--config", "bm25", "--dataset", COMMITTED, "--output-dir", str(tmp_path), "--allow-dirty"])

    metadata = json.loads((_run_dir(tmp_path) / "metadata.json").read_text(encoding="utf-8"))
    assert "corpus" in metadata, "a blocked run must still record what it inspected"
    corpus = metadata["corpus"]
    assert corpus["fingerprints"], "the corpus probe must report which stores it inspected"
    assert corpus["correspondence"] is not None

    summary = json.loads((_run_dir(tmp_path) / "retrieval_metrics.json").read_text(encoding="utf-8"))
    assert summary["executed_configs"] == []
    assert summary["any_results"] is False
    assert summary["blocked_configs"] == ["bm25"]
    # Every blocked configuration carries a machine-readable reason.
    reason = summary["configs"]["bm25"]["reason"]
    assert "unavailable backends" in reason


def test_require_contract_refuses_the_committed_dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "git_provenance", lambda root: ("abc123", False))
    exit_code = cli.main(
        [
            "--config",
            "bm25",
            "--dataset",
            COMMITTED,
            "--require-contract",
            "--output-dir",
            str(tmp_path),
            "--allow-dirty",
        ]
    )
    assert exit_code == 2
    assert not list(tmp_path.iterdir()), "a refused dataset must not leave a scored artifact behind"


def test_contract_report_states_zero_valid_rows(tmp_path, monkeypatch):
    """The artifact must state the dataset-level figure, not a scoped one."""
    monkeypatch.setattr(cli, "git_provenance", lambda root: ("abc123", False))
    cli.main(["--config", "bm25", "--dataset", COMMITTED, "--output-dir", str(tmp_path), "--allow-dirty"])
    contract = json.loads((_run_dir(tmp_path) / "metadata.json").read_text(encoding="utf-8"))["dataset_contract"]
    assert contract["total"] == 301
    assert contract["valid_count"] == 0


def test_missing_corpus_probe_evidence_is_not_silently_dropped(tmp_path, monkeypatch):
    """If the evidence collector itself fails, the artifact says so."""
    monkeypatch.setattr(cli, "git_provenance", lambda root: ("abc123", False))

    def boom(*args, **kwargs):
        raise RuntimeError("index driver exploded")

    # Patch what the defensive wrapper calls, so the wrapper itself is what is
    # under test. An evidence failure must degrade the artifact, not the run.
    monkeypatch.setattr(cli, "inspect_correspondence", boom)

    exit_code = cli.main(["--config", "bm25", "--dataset", COMMITTED, "--output-dir", str(tmp_path), "--allow-dirty"])
    assert exit_code == 0
    metadata = json.loads((_run_dir(tmp_path) / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["corpus"]["verdict"] == "unavailable"
    assert "index driver exploded" in metadata["corpus"]["error"]


def test_corpus_probe_failure_does_not_mask_the_blocking_reason(tmp_path, monkeypatch):
    """A broken probe must not be mistaken for a corpus that simply lacks data."""
    monkeypatch.setattr(cli, "git_provenance", lambda root: ("abc123", False))

    def boom(*args, **kwargs):
        raise RuntimeError("index driver exploded")

    monkeypatch.setattr(cli, "inspect_correspondence", boom)
    cli.main(["--config", "bm25", "--dataset", COMMITTED, "--output-dir", str(tmp_path), "--allow-dirty"])

    metadata = json.loads((_run_dir(tmp_path) / "metadata.json").read_text(encoding="utf-8"))
    # The run is still BLOCKED, and still not a benchmark result.
    assert metadata["results_are_benchmark"] is False
    assert metadata["attributable"] is False
