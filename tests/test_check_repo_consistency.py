"""Tests for the repository consistency drift guard."""

from __future__ import annotations

from scripts.check_repo_consistency import (
    REQUIRED_AUDIT_AREAS,
    check_documented_offline_commands,
    check_stale_offline_claims,
    check_truth_audit,
    run_offline_subcommands,
)


def test_run_offline_subcommands_are_discovered():
    subcommands = run_offline_subcommands()
    assert {"create-index", "ingest", "ingest-text", "incremental-build", "full-rebuild", "seal-epoch"} <= subcommands


def test_stale_offline_claim_is_flagged(tmp_path):
    stale = tmp_path / "stale.md"
    stale.write_text("当前只支持 UTF-8 TXT，PDF/DOCX/XLSX 尚未实现。", encoding="utf-8")
    errors: list[str] = []
    check_stale_offline_claims(stale, errors)
    assert errors

    clean = tmp_path / "clean.md"
    clean.write_text("离线管线支持 TXT/PDF/DOCX/XLSX，并需要外部模型资产。", encoding="utf-8")
    clean_errors: list[str] = []
    check_stale_offline_claims(clean, clean_errors)
    assert clean_errors == []


def test_documented_unknown_subcommand_is_flagged(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text("python3 run_offline.py reindex-everything", encoding="utf-8")
    errors: list[str] = []
    check_documented_offline_commands(doc, run_offline_subcommands(), errors)
    assert errors

    known = tmp_path / "known.md"
    known.write_text("python3 run_offline.py full-rebuild --epoch phase_2", encoding="utf-8")
    known_errors: list[str] = []
    check_documented_offline_commands(known, run_offline_subcommands(), known_errors)
    assert known_errors == []


def test_current_truth_audit_is_valid():
    errors: list[str] = []
    check_truth_audit(errors)
    assert errors == []


def test_required_audit_areas_are_present():
    from pathlib import Path

    audit = Path("docs/repository-truth-audit.md").read_text(encoding="utf-8")
    for area in REQUIRED_AUDIT_AREAS:
        assert f"| {area} |" in audit, f"audit missing required area {area}"
