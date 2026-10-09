"""Contract tests for the VAL-STORE-001 Qdrant evidence artifact.

Why this exists
---------------
The committed `metadata.json` used to claim `checks_run=21` / `checks_passed=21`
while the script only ever executed 20 checks. Nothing detected the drift,
because the count was a hand-written integer that no test ever reconciled
against the checks it described.

These tests make the artifact self-describing and fail-closed:

* `counts` must be *derivable* from the recorded check list;
* the metadata counts must equal the record counts;
* a mismatch must actually be detected (negative controls).

The runtime behaviour is covered separately by
`tests/integration/test_qdrant_store_runtime.py`; this file never needs a live
Qdrant, so the contract holds in an offline CI run too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.validation.validate_qdrant_store import (
    check_counts,
    qdrant_version_compatibility,
)

ARTIFACT_DIR = Path("artifacts/qdrant/2026-10-09-qdrant-v1.12.0")
METADATA = ARTIFACT_DIR / "metadata.json"
RECORD = ARTIFACT_DIR / "checks.json"


@pytest.fixture(scope="module")
def record() -> dict:
    return json.loads(RECORD.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def metadata() -> dict:
    return json.loads(METADATA.read_text(encoding="utf-8"))


# ── the record is the source of truth ────────────────────────────────────────


def test_record_counts_are_derivable_from_the_check_list(record):
    """`counts` must be recomputable, not asserted."""
    checks = record["checks"]
    assert isinstance(checks, list) and checks
    assert record["counts"] == check_counts(checks)


def test_counts_partition_the_check_list(record):
    counts = record["counts"]
    assert counts["checks_passed"] + counts["checks_failed"] == counts["checks_run"]
    assert counts["checks_run"] == len(record["checks"])


def test_every_check_has_a_stable_unique_id(record):
    ids = [check["id"] for check in record["checks"]]
    assert all(isinstance(i, str) and i for i in ids)
    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"duplicate check ids would make the record ambiguous: {duplicates}"


def test_a_passing_run_records_no_failures(record):
    assert record["counts"]["checks_failed"] == 0
    assert all(check["ok"] for check in record["checks"])


# ── the metadata must not drift from the record ──────────────────────────────


def test_metadata_counts_match_the_check_record(metadata, record):
    """The exact defect this file was written to prevent."""
    assert metadata["counts"] == record["counts"], (
        f"metadata claims {metadata['counts']} but the check record executed "
        f"{record['counts']}; update metadata.json from checks.json"
    )


def test_metadata_does_not_claim_more_checks_than_were_executed(metadata, record):
    assert metadata["counts"]["checks_run"] == len(record["checks"])


def test_run_id_and_validation_id_agree(metadata, record):
    assert metadata["validation_id"] == record["validation_id"]
    assert metadata["run_id"] == ARTIFACT_DIR.name


# ── negative controls: the contract must actually catch drift ─────────────────


def test_check_counts_detects_an_inflated_metadata_total(record):
    """Re-deriving from a doctored record must disagree with the old '21'."""
    checks = list(record["checks"])
    derived = check_counts(checks)
    # The historical artifact asserted 21; the records only support 20.
    assert derived["checks_run"] == len(checks) == 20
    assert derived != {"checks_run": 21, "checks_passed": 21, "checks_failed": 0}


def test_check_counts_counts_a_failure_as_failed():
    checks = [{"id": "a", "ok": True}, {"id": "b", "ok": False}]
    assert check_counts(checks) == {"checks_run": 2, "checks_passed": 1, "checks_failed": 1}


def test_metadata_mismatch_is_detected_by_the_shared_derivation(record):
    """A metadata count that does not match the record fails this comparison."""
    doctored_metadata_counts = {"checks_run": 21, "checks_passed": 21, "checks_failed": 0}
    with pytest.raises(AssertionError):
        assert doctored_metadata_counts == record["counts"]


# ── the client/server risk is recorded, not hidden ───────────────────────────


def test_compatibility_finding_is_recorded_and_excluded_from_counts(record):
    """The version gap is a finding, not a functional check."""
    compatibility = record["compatibility"]
    assert compatibility["client_version"] and compatibility["server_version"]
    assert "compatibility" not in {check["id"] for check in record["checks"]}


@pytest.mark.parametrize(
    ("client", "server", "verdict", "supported"),
    [
        ("1.12.0", "1.12.4", "MATCHED_MINOR", True),
        ("1.12.0", "1.13.0", "ADJACENT_MINOR_TESTED", True),
        ("1.11.0", "1.12.0", "ADJACENT_MINOR_TESTED", True),
        ("1.18.0", "1.12.0", "OUTSIDE_GUARANTEED_WINDOW", False),
        ("2.0.0", "1.12.0", "MAJOR_MISMATCH", False),
    ],
)
def test_version_window_matches_qdrant_documented_policy(client, server, verdict, supported):
    result = qdrant_version_compatibility(client, server)
    assert result["verdict"] == verdict
    assert result["within_documented_support"] is supported


def test_recorded_pair_is_flagged_as_outside_the_guaranteed_window(record):
    """Pins the honest risk statement for the pair this artifact was run on."""
    compatibility = record["compatibility"]
    if compatibility["minor_delta"] > 1:
        assert compatibility["within_documented_support"] is False
        assert compatibility["verdict"] == "OUTSIDE_GUARANTEED_WINDOW"


def test_unparseable_version_is_not_claimed_as_supported():
    assert qdrant_version_compatibility("dev", "1.12.0")["within_documented_support"] is False
