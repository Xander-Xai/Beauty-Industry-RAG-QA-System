"""Tests for the real-corpus V1 review-decision import chain.

These tests never sign the committed ledger. They build synthetic decisions in
``tmp_path`` to prove that each gate fires, so the behaviour is verified without
a human decision ever being fabricated in the repository.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(REPO_ROOT / "scripts" / "validation"))

import import_real_v1_review_decisions as importer  # noqa: E402

from benchmarks.annotation import (  # noqa: E402
    REVIEW_STATUS_REVIEWED,
    audit_dataset,
    load_records,
    record_sha256,
)

CANDIDATES = REPO_ROOT / "tests/evaluation/golden_set_v1/candidates_v1.jsonl"
DECISIONS = REPO_ROOT / "tests/evaluation/golden_set_v1/review_decisions_v1.jsonl"
V2_SCHEMA = REPO_ROOT / "tests/evaluation/golden_set_v2/annotations.schema.json"
WORKPACKAGE = REPO_ROOT / "tests/evaluation/golden_set_v1/retrieval_review_workpackage.jsonl"


def _candidate(sample_id: str) -> dict:
    for record in load_records(CANDIDATES):
        if record["sample_id"] == sample_id:
            return record
    raise AssertionError(f"no candidate {sample_id}")


def _build_decision(sample_id: str, **overrides) -> dict:
    candidate = _candidate(sample_id)
    decision = {
        "decision_schema": importer.DECISION_SCHEMA,
        "sample_id": sample_id,
        "candidate_sha256": record_sha256(candidate),
        "candidate_dataset_sha256": importer.dataset_sha256(CANDIDATES),
        "decision": "ACCEPT",
        "reviewer": "Synthetic Test Reviewer",
        "reviewed_at": "2026-10-19",
        "promoted_by": "Synthetic Test Reviewer",
        "notes": "",
        "relevance_changes": [],
        "removed_passages": [],
        "added_passages": [],
        "confirmed_difficulty": "",
        "confirmed_complexity_label": "",
        "confirmed_business_type": "",
        "confirmed_question": "",
    }
    decision.update(overrides)
    return decision


def _write_ledger(tmp_path: Path, decisions: list[dict]) -> Path:
    path = tmp_path / "decisions.jsonl"
    path.write_text(
        "".join(json.dumps(decision, ensure_ascii=False) + "\n" for decision in decisions),
        encoding="utf-8",
    )
    return path


def _sign(tmp_path: Path, sample_id: str, **overrides) -> dict:
    """Write a ledger holding one synthetic signed decision."""
    decision = _build_decision(sample_id, **overrides)
    _write_ledger(tmp_path, [decision])
    return decision


def _sign_again(tmp_path: Path, sample_id: str, **overrides) -> dict:
    """Append a second signed decision to the ledger ``_sign`` already wrote."""
    ledger = tmp_path / "decisions.jsonl"
    existing = load_records(ledger) if ledger.exists() else []
    decision = _build_decision(sample_id, **overrides)
    _write_ledger(tmp_path, [*existing, decision])
    return decision


def _run(tmp_path: Path, ledger: Path, apply: bool = True) -> int:
    argv = [
        "--decisions",
        str(ledger),
        "--out-dir",
        str(tmp_path / "out"),
    ]
    if apply:
        argv.append("--apply")
    return importer.main(argv)


def test_committed_ledger_is_blank_so_no_review_is_claimed() -> None:
    """The committed ledger must be a blank template, never a filled-in one."""
    assert DECISIONS.exists(), "the blank decision ledger must exist for a reviewer to sign"
    for record in load_records(DECISIONS):
        assert record["decision"] == "", f"{record['sample_id']} carries a decision in the committed ledger"
        assert record["reviewer"] == ""
        assert record["reviewed_at"] == ""
        assert record["promoted_by"] == ""


def test_committed_candidates_stay_drafts() -> None:
    """Nothing in the committed set may be promoted before a human signs."""
    records = load_records(CANDIDATES)
    report = audit_dataset(records)
    assert report.scorable_count == 0
    assert report.valid_count == len(records)
    assert report.draft_count == len(records)


def test_emit_template_refuses_to_overwrite_a_signed_ledger(tmp_path: Path) -> None:
    signed = tmp_path / "decisions.jsonl"
    _sign(tmp_path, "REAL-V1-001")
    signed.write_text(signed.read_text(encoding="utf-8"), encoding="utf-8")
    before = signed.read_text(encoding="utf-8")
    assert importer.emit_template(load_records(CANDIDATES), signed, "x" * 64) == 2
    assert signed.read_text(encoding="utf-8") == before, "a signed ledger must survive a template rebuild"


def test_unsigned_rows_stay_draft_and_are_not_scorable(tmp_path: Path) -> None:
    ledger = tmp_path / "decisions.jsonl"
    _sign(tmp_path, "REAL-V1-001", decision="")
    assert _run(tmp_path, ledger) == 0
    reviewed = load_records(tmp_path / "out/annotations_v1_reviewed.jsonl")
    drafts = load_records(tmp_path / "out/annotations_v1_drafts.jsonl")
    assert reviewed == []
    assert len(drafts) == 27
    assert all(record["annotation"]["review_status"] != REVIEW_STATUS_REVIEWED for record in drafts)


def test_accept_promotes_and_records_both_people(tmp_path: Path) -> None:
    ledger = tmp_path / "decisions.jsonl"
    _sign(tmp_path, "REAL-V1-001")
    assert _run(tmp_path, ledger) == 0
    reviewed = load_records(tmp_path / "out/annotations_v1_reviewed.jsonl")
    assert [record["sample_id"] for record in reviewed] == ["REAL-V1-001"]
    provenance = reviewed[0]["annotation"]
    assert provenance["review_status"] == REVIEW_STATUS_REVIEWED
    assert provenance["reviewed_by"] == "Synthetic Test Reviewer"
    assert provenance["promoted_by"] == "Synthetic Test Reviewer"
    # The AI pre-review must not be laundered into a human annotation.
    assert provenance["source"] == "llm_candidate"
    assert provenance["annotator"] != provenance["reviewed_by"]


def test_reject_never_enters_the_scored_dataset(tmp_path: Path) -> None:
    ledger = tmp_path / "decisions.jsonl"
    _sign(tmp_path, "REAL-V1-023", decision="REJECT")
    assert _run(tmp_path, ledger) == 0
    assert load_records(tmp_path / "out/annotations_v1_reviewed.jsonl") == []
    rejected = load_records(tmp_path / "out/annotations_v1_rejected.jsonl")
    assert [record["sample_id"] for record in rejected] == ["REAL-V1-023"]
    assert all(record["annotation"]["review_status"] != REVIEW_STATUS_REVIEWED for record in rejected)


def test_modify_applies_relevance_change_and_removal(tmp_path: Path) -> None:
    candidate = _candidate("REAL-V1-021")
    article_004 = next(a for a in candidate["annotations"] if a["_locator"] == "第四条")
    ledger = tmp_path / "decisions.jsonl"
    _sign(
        tmp_path,
        "REAL-V1-021",
        decision="MODIFY",
        removed_passages=[{"chunk_id": article_004["chunk_id"]}],
        relevance_changes=[{"chunk_id": article_004["chunk_id"], "from": 1, "to": 0}],
    )
    assert _run(tmp_path, ledger) == 0
    reviewed = load_records(tmp_path / "out/annotations_v1_reviewed.jsonl")
    assert len(reviewed) == 1
    grades = {a["relevance"] for a in reviewed[0]["annotations"]}
    assert 0 in grades, "a considered-and-excluded passage must be graded 0, not deleted"


def test_missing_reviewer_is_refused_not_silently_drafted(tmp_path: Path) -> None:
    ledger = tmp_path / "decisions.jsonl"
    _sign(tmp_path, "REAL-V1-001", reviewer="")
    assert _run(tmp_path, ledger) == 2
    assert not (tmp_path / "out/annotations_v1_reviewed.jsonl").exists()


def test_missing_reviewed_at_is_refused_and_never_defaulted(tmp_path: Path) -> None:
    ledger = tmp_path / "decisions.jsonl"
    _sign(tmp_path, "REAL-V1-001", reviewed_at="")
    assert _run(tmp_path, ledger) == 2
    assert not (tmp_path / "out/annotations_v1_reviewed.jsonl").exists()


def test_self_review_is_refused(tmp_path: Path) -> None:
    candidate = _candidate("REAL-V1-001")
    annotator = candidate["annotation"]["annotator"]
    ledger = tmp_path / "decisions.jsonl"
    _sign(tmp_path, "REAL-V1-001", reviewer=annotator, promoted_by=annotator)
    assert _run(tmp_path, ledger) == 2


def test_llm_promotion_without_promoted_by_is_refused(tmp_path: Path) -> None:
    ledger = tmp_path / "decisions.jsonl"
    _sign(tmp_path, "REAL-V1-001", promoted_by="")
    assert _run(tmp_path, ledger) == 2


def test_decision_bound_to_stale_candidate_bytes_is_refused(tmp_path: Path) -> None:
    ledger = tmp_path / "decisions.jsonl"
    _sign(tmp_path, "REAL-V1-001", candidate_sha256="0" * 64)
    assert _run(tmp_path, ledger) == 2
    assert not (tmp_path / "out/annotations_v1_reviewed.jsonl").exists()


def test_ai_fields_in_the_decision_ledger_are_refused(tmp_path: Path) -> None:
    ledger = tmp_path / "decisions.jsonl"
    _sign(tmp_path, "REAL-V1-001", ai_suggestion="ACCEPT")
    assert _run(tmp_path, ledger) == 2


def test_promoted_record_satisfies_the_derived_v1_schema(tmp_path: Path) -> None:
    """The projection must satisfy the V1 schema without relaxing it.

    The check is written against the schema's own ``properties`` and
    ``additionalProperties`` rather than a JSON Schema library, because
    ``jsonschema`` is not a declared dependency of this repository and CI cannot
    import it. A test that only runs on one machine is not a test. The optional
    library cross-check below confirms this reasoning still holds where the
    library exists.
    """
    ledger = tmp_path / "decisions.jsonl"
    _sign(tmp_path, "REAL-V1-001")
    assert _run(tmp_path, ledger) == 0
    schema = json.loads((tmp_path / "out/annotations_v1.schema.json").read_text(encoding="utf-8"))
    for record in load_records(tmp_path / "out/annotations_v1_reviewed.jsonl"):
        assert set(record) <= set(schema["properties"]), "record carries a key the schema forbids"
        assert set(schema["required"]) <= set(record), "record is missing a schema-required key"
        assert re.fullmatch(schema["properties"]["sample_id"]["pattern"], record["sample_id"])
        assert record["corpus_sha256"] and re.fullmatch(r"[0-9a-f]{64}", record["corpus_sha256"])
        assert record["business_type"] in schema["properties"]["business_type"]["enum"]
        assert record["difficulty"] in schema["properties"]["difficulty"]["enum"]
        assert record["complexity_label"] in schema["properties"]["complexity_label"]["enum"]
        for annotation in record["annotations"]:
            assert set(annotation) <= set(schema["properties"]["annotations"]["items"]["properties"])
            assert {"doc_id", "chunk_id", "text"} <= set(annotation)


def test_v1_schema_relaxes_only_the_sample_id_pattern() -> None:
    v2 = json.loads(V2_SCHEMA.read_text(encoding="utf-8"))
    v1 = importer.build_v1_schema()
    differences = importer.schema_diff(v1, v2)
    assert set(differences) == importer.ALLOWED_SCHEMA_DIFF
    importer.assert_schema_not_relaxed(v1, v2)


def test_promoted_record_also_satisfies_the_untouched_v2_schema_under_the_id_map(
    tmp_path: Path,
) -> None:
    """The V1 identifier change must not be a way around the V2 gates.

    ``REAL-V1-001`` cannot satisfy ``^[0-9]{4}$``, so the same reviewed content is
    re-checked against the untouched V2 schema using the emitted map. If any other
    field had been relaxed for V1, this would fail.
    """
    ledger = tmp_path / "decisions.jsonl"
    _sign(tmp_path, "REAL-V1-001")
    assert _run(tmp_path, ledger) == 0
    v2 = json.loads(V2_SCHEMA.read_text(encoding="utf-8"))
    mapping = {r["v1_sample_id"]: r["v2_sample_id"] for r in load_records(tmp_path / "out/sample_id_map_v2.jsonl")}
    assert len(set(mapping.values())) == len(mapping), "the id mapping must be injective to be lossless"
    for record in load_records(tmp_path / "out/annotations_v1_reviewed.jsonl"):
        mapped = {**record, "sample_id": mapping[record["sample_id"]]}
        assert set(mapped) <= set(v2["properties"]), "V2 would reject a key V1 allowed"
        assert re.fullmatch(v2["properties"]["sample_id"]["pattern"], mapped["sample_id"])
        for annotation in mapped["annotations"]:
            assert set(annotation) <= set(v2["properties"]["annotations"]["items"]["properties"])


def test_full_json_schema_validation_when_the_library_is_available(tmp_path: Path) -> None:
    """Cross-check the library-free reasoning above against a real validator."""
    jsonschema = pytest.importorskip("jsonschema", reason="jsonschema is not a declared dependency")
    ledger = tmp_path / "decisions.jsonl"
    _sign(tmp_path, "REAL-V1-001")
    _sign_again(tmp_path, "REAL-V1-011")
    assert _run(tmp_path, ledger) == 0
    v1_schema = json.loads((tmp_path / "out/annotations_v1.schema.json").read_text(encoding="utf-8"))
    v2_schema = json.loads(V2_SCHEMA.read_text(encoding="utf-8"))
    mapping = {r["v1_sample_id"]: r["v2_sample_id"] for r in load_records(tmp_path / "out/sample_id_map_v2.jsonl")}
    for record in load_records(tmp_path / "out/annotations_v1_reviewed.jsonl"):
        jsonschema.validate(record, v1_schema)
        jsonschema.validate({**record, "sample_id": mapping[record["sample_id"]]}, v2_schema)


def test_v1_schema_relaxation_is_detected() -> None:
    """Proves the drift check is load-bearing rather than decorative."""
    v2 = json.loads(V2_SCHEMA.read_text(encoding="utf-8"))
    v1 = importer.build_v1_schema()
    v1["properties"]["annotation"]["allOf"] = []
    with pytest.raises(importer.ImportRefused):
        importer.assert_schema_not_relaxed(v1, v2)


def test_projection_drops_review_only_fields(tmp_path: Path) -> None:
    ledger = tmp_path / "decisions.jsonl"
    _sign(tmp_path, "REAL-V1-001")
    assert _run(tmp_path, ledger) == 0
    record = load_records(tmp_path / "out/annotations_v1_reviewed.jsonl")[0]
    assert "contexts" not in record
    assert all(not key.startswith("_") for key in record["annotations"][0])


def test_workpackage_builder_refuses_to_overwrite_a_signed_worksheet(tmp_path: Path) -> None:
    """Regenerating the worksheet must not erase a reviewer's decision."""
    work_dir = tmp_path / "ws"
    work_dir.mkdir()
    target = work_dir / "retrieval_review_workpackage.jsonl"
    rows = load_records(WORKPACKAGE)
    target.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n", encoding="utf-8"
    )
    rows[0]["review"]["decision"] = "ACCEPT"
    rows[0]["review"]["reviewer"] = "Synthetic Test Reviewer"
    rows[0]["review"]["reviewed_at"] = "2026-10-19"
    signed = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n"
    target.write_text(signed, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "scripts/validation/build_real_golden_set_v1_workpackage.py", "--out-dir", str(work_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "already carries a human review decision" in (result.stderr + result.stdout)
    assert target.read_text(encoding="utf-8") == signed, "the signed worksheet must be byte-identical afterwards"


def test_workpackage_builder_still_regenerates_an_unsigned_worksheet(tmp_path: Path) -> None:
    work_dir = tmp_path / "ws2"
    work_dir.mkdir()
    result = subprocess.run(
        [sys.executable, "scripts/validation/build_real_golden_set_v1_workpackage.py", "--out-dir", str(work_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (work_dir / "retrieval_review_workpackage.jsonl").exists()
