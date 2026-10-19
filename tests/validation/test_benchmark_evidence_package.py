"""The published benchmark evidence must be independently checkable.

`docs/validation/evidence/` replaces the previous arrangement — a report that
quoted numbers and a table of SHA-256 digests for artifacts nobody could fetch.
The claim it makes is narrower and checkable: *these metrics follow from these
per-sample ranks*. `scripts/validation/verify_benchmark_evidence.py` re-derives
them through the repository's own metric implementation.

These tests pin both directions, because a verification script that only ever
passes is worthless:

* the committed evidence reproduces, as recorded;
* tampering with any published number is caught, and the tool says which one.

They also pin the redaction boundary — the evidence must carry enough to
recompute the metrics and not enough to republish the indexed regulatory text.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = REPO_ROOT / "docs" / "validation" / "evidence"
MANIFEST = EVIDENCE_DIR / "real-v1-approved-bm25-evidence.json"
PER_SAMPLE = EVIDENCE_DIR / "real-v1-approved-bm25-per-sample.jsonl"
VERIFY = REPO_ROOT / "scripts" / "validation" / "verify_benchmark_evidence.py"

GOLDEN_SET = REPO_ROOT / "tests/evaluation/golden_set_v1/reviewed/annotations_v1_reviewed.jsonl"
GOLDEN_SET_SHA256 = "9621d150996f9144d7d4b8af486ec5f4cccfbf29dc3a31599b82c328349159d1"


def _run_verifier(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VERIFY), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def per_sample() -> list[dict]:
    return [json.loads(line) for line in PER_SAMPLE.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_the_committed_evidence_reproduces_every_published_metric():
    """The headline claim: the numbers follow from the committed per-sample data."""
    result = _run_verifier()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All published metrics reproduced" in result.stdout
    # Every run that claims to be a benchmark result must actually be verified,
    # not silently skipped.
    assert "SKIPPED" not in result.stdout


def test_a_doctored_metric_is_caught_and_named(tmp_path: Path):
    """A verification script that always passes proves nothing.

    Point the tool at a doctored copy of the evidence and it must fail *and* say
    which number did not reproduce, so a reader knows what to distrust.
    """
    doctored = json.loads(MANIFEST.read_text(encoding="utf-8"))
    doctored["runs"]["ab_smartcn"]["metrics"]["recall_at_10"] = 0.9999
    path = tmp_path / "doctored.json"
    path.write_text(json.dumps(doctored, ensure_ascii=False), encoding="utf-8")

    result = _run_verifier("--manifest", str(path), "--run", "ab_smartcn")

    assert result.returncode == 1
    assert "MISMATCH" in result.stdout
    assert "recall_at_10" in result.stdout


def test_a_doctored_per_sample_rank_is_caught(tmp_path: Path):
    """The evidence, not just the summary, must be load-bearing.

    Changing one sample's first-relevant rank has to move the aggregate, which
    is what makes the per-sample file worth committing.
    """
    rows = [json.loads(line) for line in PER_SAMPLE.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows[0]["ab_standard"]["retrieved_chunk_ids"] = list(rows[0]["gold_chunk_ids"])
    doctored = tmp_path / "per-sample-doctored.jsonl"
    doctored.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    result = _run_verifier("--per-sample", str(doctored), "--run", "ab_standard")

    assert result.returncode == 1
    assert "MISMATCH" in result.stdout


# ── identity: the evidence is bound to a specific signed dataset ──────────────


def test_the_evidence_is_bound_to_the_signed_golden_set(manifest):
    import hashlib

    actual = hashlib.sha256(GOLDEN_SET.read_bytes()).hexdigest()
    assert manifest["dataset"]["sha256"] == GOLDEN_SET_SHA256
    assert actual == GOLDEN_SET_SHA256, "the committed golden set changed; the evidence is now bound to nothing"


def test_the_evidence_covers_every_golden_sample_in_order(per_sample, manifest):
    import json as _json

    golden = [_json.loads(line) for line in GOLDEN_SET.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert manifest["dataset"]["samples"] == len(golden) == 27
    assert [row["sample_id"] for row in per_sample] == [g["sample_id"] for g in golden]
    for row, gold in zip(per_sample, golden, strict=True):
        assert row["gold_chunk_ids"] == [a["chunk_id"] for a in gold["annotations"]]


# ── the gate the evidence claims ─────────────────────────────────────────────


def test_every_published_run_passed_the_source_approval_gate(manifest):
    for name, run in sorted(manifest["runs"].items()):
        gate = run["gate"]
        assert gate["source_approval_verified"] is True, name
        assert gate["provenance_verified"] is True, name
        assert gate["attributable"] is True, name
        assert gate["synthetic_retriever"] is False, name
        assert gate["results_are_benchmark"] is True, name
        assert gate["publishable"] is True, name
        assert run["source_approval"]["issues"] == {}, name
        assert run["source_approval"]["documents_verified"] == run["source_approval"]["document_count"], name


def test_the_evidence_names_the_two_approved_sources_with_their_bytes(manifest):
    assert len(manifest["sources"]) == 2
    hashes = {s["source_id"]: s["content_sha256"] for s in manifest["sources"]}
    assert hashes["corpus/real_v1/sources/regulation_727.jsonl"] == (
        "c9499ca5eefa368328a0ecbe57ea0410687fafcd32bbb5c9d0aa9014f6075c91"
    )
    assert hashes["corpus/real_v1/sources/used_ingredients_2021.jsonl"] == (
        "3dd67722972d7a10dd3da56028bd85d649e68b17a87c0499f169252527c0c06b"
    )
    assert sum(s["document_count"] for s in manifest["sources"]) == 212
    for source in manifest["sources"]:
        assert source["trust_class"] == "APPROVED_EXTERNAL"
        assert source["approval_status"] == "APPROVED"
        assert source["approval_actor"] == "Xander-Xai"
        assert source["approval_decided_at"]


def test_the_approval_ledger_itself_is_not_committed(manifest):
    """The ledger stays local; the manifest is its published projection."""
    assert not (REPO_ROOT / "data" / "offline_source_trust.sqlite3").exists() or (
        REPO_ROOT / ".git" / "info" / "exclude"
    )
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "data/offline_source_trust.sqlite3"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert tracked.returncode != 0, "the raw approval ledger must never be committed"
    assert "not committed" in manifest["ledger_notice"].lower()


# ── redaction boundary ───────────────────────────────────────────────────────


def test_the_evidence_carries_no_indexed_regulatory_text(per_sample):
    """Enough to recompute the metrics, not enough to republish the corpus.

    The evidence names chunk ids and ranks. Document text would make the file a
    republication of the corpus, which the licensing note in
    `corpus/real_v1/SOURCES.md` deliberately does not claim.
    """
    blob = PER_SAMPLE.read_text(encoding="utf-8")
    for fragment in ("本条例所称化妆品", "烟酰胺", "最高历史使用量", "国务院令"):
        assert fragment not in blob, f"regulatory text leaked into the evidence: {fragment!r}"
    for row in per_sample:
        for arm in row:
            if not isinstance(row[arm], dict):
                continue
            assert set(row[arm]) >= {"first_relevant_rank", "hit_at_10", "retrieved_chunk_ids"}
            assert all(isinstance(c, str) and len(c) == 36 for c in row[arm]["retrieved_chunk_ids"])


def test_the_a_b_arms_declare_the_analyzer_that_produced_them(manifest):
    """Each run must say which tokenizer it used.

    Without this, a reader cannot tell an analyzer result from a re-run of the
    baseline — the whole point of the comparison.
    """
    by_run = {name: run["analyzer"] for name, run in manifest["runs"].items()}
    assert by_run["ab_standard"] == "standard (index default)"
    assert by_run["ab_smartcn"] == "smartcn"
    assert by_run["ab_ik_smart"] == "ik_smart"


def test_the_a_b_arms_differ_only_by_analyzer(manifest):
    """Same corpus, same epoch, same provenance — one variable."""
    arms = {k: v for k, v in manifest["runs"].items() if k.startswith("ab_")}
    corpora = {run["corpus_content_sha256"] for run in arms.values()}
    epochs = {run["knowledge_version_epoch"] for run in arms.values()}
    contracts = {run["corpus_correspondence"] for run in arms.values()}
    assert len(corpora) == 1, "the A/B arms must retrieve the identical corpus"
    assert epochs == {"real-v1-20261019"}
    assert contracts == {"full"}
    assert len({run["analyzer"] for run in arms.values()}) == 3, "three arms, three analyzers"
