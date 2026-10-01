"""Tests for the repository consistency drift guard."""

from __future__ import annotations

from scripts.check_repo_consistency import (
    REQUIRED_AUDIT_AREAS,
    check_docs_index,
    check_documented_offline_commands,
    check_forbidden_current_claims,
    check_metrics_auth_contract,
    check_metrics_route_contract,
    check_ragas_unavailable_contract,
    check_rbac_mask_contract,
    check_stale_offline_claims,
    check_truth_audit,
    post_merge_phase_drift_claims,
    retired_topology_claims,
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


def test_truth_audit_accepts_iso_date(tmp_path):
    audit = tmp_path / "audit.md"
    audit.write_text(
        "Reconciled candidate: `HEAD`\nPost-reconciliation verification date: 2026-10-02\n",
        encoding="utf-8",
    )
    errors: list[str] = []
    check_truth_audit(errors, audit)
    assert not any("verification date" in error for error in errors)


def test_truth_audit_rejects_non_iso_date(tmp_path):
    audit = tmp_path / "audit.md"
    audit.write_text(
        "Reconciled candidate: `HEAD`\nPost-reconciliation verification date: 02-10-2026\n",
        encoding="utf-8",
    )
    errors: list[str] = []
    check_truth_audit(errors, audit)
    assert any("ISO YYYY-MM-DD" in error for error in errors)


def test_truth_audit_rejects_compact_iso_date(tmp_path):
    """fromisoformat also accepts 20261002; the guard must require YYYY-MM-DD."""
    audit = tmp_path / "audit.md"
    audit.write_text(
        "Reconciled candidate: `HEAD`\nPost-reconciliation verification date: 20261002\n",
        encoding="utf-8",
    )
    errors: list[str] = []
    check_truth_audit(errors, audit)
    assert any("ISO YYYY-MM-DD" in error for error in errors)


def test_truth_audit_rejects_iso_week_date(tmp_path):
    audit = tmp_path / "audit.md"
    audit.write_text(
        "Reconciled candidate: `HEAD`\nPost-reconciliation verification date: 2026-W40-5\n",
        encoding="utf-8",
    )
    errors: list[str] = []
    check_truth_audit(errors, audit)
    assert any("ISO YYYY-MM-DD" in error for error in errors)


def test_truth_audit_rejects_missing_date(tmp_path):
    audit = tmp_path / "audit.md"
    audit.write_text("Reconciled candidate: `HEAD`\n", encoding="utf-8")
    errors: list[str] = []
    check_truth_audit(errors, audit)
    assert any("verification date is missing" in error for error in errors)


def test_superseded_contract_claim_is_flagged(tmp_path):
    doc = tmp_path / "contract.md"
    doc.write_text("Qdrant uses an IVF index with nlist and nprobe tuning.", encoding="utf-8")
    errors: list[str] = []
    check_forbidden_current_claims(doc, errors)
    assert errors

    governance = tmp_path / "governance.md"
    governance.write_text("PR #3 remains open and must be merged.", encoding="utf-8")
    governance_errors: list[str] = []
    check_forbidden_current_claims(governance, governance_errors)
    assert governance_errors

    clean = tmp_path / "clean.md"
    clean.write_text("Qdrant uses Cosine distance; masks are uint32.", encoding="utf-8")
    clean_errors: list[str] = []
    check_forbidden_current_claims(clean, clean_errors)
    assert clean_errors == []


def test_qdrant_param_disclaimer_is_not_flagged(tmp_path):
    """A truthful 'not supported' disclaimer must not trip the drift guard."""
    disclaimer = tmp_path / "disclaimer.md"
    disclaimer.write_text(
        "Qdrant 使用 Cosine 距离；本仓库不支持 nlist / nprobe 调优参数。",
        encoding="utf-8",
    )
    errors: list[str] = []
    check_forbidden_current_claims(disclaimer, errors)
    assert errors == []


def test_current_metrics_and_rbac_contracts_pass():
    errors: list[str] = []
    check_metrics_route_contract(errors)
    check_rbac_mask_contract(errors)
    assert errors == []


def test_required_audit_areas_are_present():
    from pathlib import Path

    audit = Path("docs/repository-truth-audit.md").read_text(encoding="utf-8")
    for area in REQUIRED_AUDIT_AREAS:
        assert f"| {area} |" in audit, f"audit missing required area {area}"


def test_post_merge_phase_drift_is_flagged_and_historical_is_exempt():
    assert post_merge_phase_drift_claims("The candidate must pass all checks before merge.")
    assert post_merge_phase_drift_claims("This branch is awaiting merge.")
    assert post_merge_phase_drift_claims("Verified on the latest merged `main` (PR #7).")
    # Historical narration is exempt.
    assert post_merge_phase_drift_claims("Historical note: at that time it was awaiting merge.") == []


def test_retired_topology_claim_is_flagged_but_history_is_exempt():
    assert retired_topology_claims("Current runtime uses vLLM-Rewrite and vLLM-Gen-4B.")
    assert retired_topology_claims("The config key vllm_rewrite still exists.")
    # Explicit historical/target-design markers exempt the line.
    assert retired_topology_claims("Historical design: vLLM-Rewrite plus vLLM-Gen-4B.") == []
    assert retired_topology_claims("The retired vLLM-Rewrite instance is obsolete.") == []


def test_docs_index_includes_current_canonical_docs():
    errors: list[str] = []
    check_docs_index(errors)
    assert errors == []


def test_ragas_unavailable_disclaimer_contract():
    errors: list[str] = []
    check_ragas_unavailable_contract(errors)
    assert errors == []


def test_metrics_auth_contract():
    errors: list[str] = []
    check_metrics_auth_contract(errors)
    assert errors == []


def test_uvicorn_proxy_headers_disabled_guard(tmp_path, monkeypatch):
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    (tmp_path / "app.py").write_text("uvicorn.run('app:app')\n", encoding="utf-8")
    errors: list[str] = []
    guard.check_uvicorn_proxy_headers_disabled(errors)
    assert errors

    (tmp_path / "app.py").write_text("uvicorn.run('app:app', proxy_headers=False)\n", encoding="utf-8")
    ok: list[str] = []
    guard.check_uvicorn_proxy_headers_disabled(ok)
    assert ok == []
