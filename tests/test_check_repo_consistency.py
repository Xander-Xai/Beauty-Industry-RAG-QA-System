"""Tests for the repository consistency drift guard."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_repo_consistency import (
    CANONICAL,
    CANONICAL_DOCS,
    HISTORICAL,
    REQUIRED_AUDIT_AREAS,
    UNCLASSIFIED,
    archive_banner_errors,
    benchmark_artifact_exists,
    benchmark_classification_errors,
    check_docs_classification,
    check_docs_index,
    check_documented_offline_commands,
    check_forbidden_current_claims,
    check_frontend_evidence_classification,
    check_local_runtime_validation_contract,
    check_metrics_auth_contract,
    check_metrics_route_contract,
    check_prd_design_targets,
    check_qdrant_evidence_reconciliation,
    check_ragas_failure_contract,
    check_rbac_mask_contract,
    check_stale_offline_claims,
    check_truth_audit,
    check_version_label_semantics,
    ci_builds_frontend,
    doc_classification,
    docs_classification_errors,
    forbidden_evidence_claims,
    post_merge_phase_drift_claims,
    retired_topology_claims,
    run_offline_subcommands,
    v25_release_claims,
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


def test_ragas_failure_contract_passes():
    errors: list[str] = []
    check_ragas_failure_contract(errors)
    assert errors == []


def test_ragas_zero_fallback_claim_is_rejected():
    """The retired zero + `_warning` fallback must not pass as current behavior."""
    from scripts.check_repo_consistency import ragas_zero_fallback_claims

    stale = "缺少 RAGAS 时 evaluator 返回零分并附 _warning。"
    assert ragas_zero_fallback_claims(stale)

    english = "returns zero score when ragas unavailable and adds a _warning fallback."
    assert ragas_zero_fallback_claims(english)


def test_unrelated_zero_wording_is_not_a_ragas_fallback():
    """Generic zero wording without a RAGAS reference is not this claim."""
    from scripts.check_repo_consistency import ragas_zero_fallback_claims

    assert ragas_zero_fallback_claims("The cache hit rate returns zero when no requests exist.") == []


def test_no_longer_zero_fallback_is_not_historically_exempt():
    """Ambiguous transition words must not suppress the zero-fallback guard."""
    from scripts.check_repo_consistency import ragas_zero_fallback_claims

    assert ragas_zero_fallback_claims("RAGAS unavailable no longer fails fast and returns zero scores")
    # Genuine historical narration stays exempt.
    assert ragas_zero_fallback_claims("Historical note: RAGAS previously returned zero scores.") == []


def test_historical_exemption_is_clause_scoped():
    """A historical clause must not exempt a neighboring current-state clause."""
    from scripts.check_repo_consistency import ragas_zero_fallback_claims

    assert ragas_zero_fallback_claims("Historical behavior failed fast; currently RAGAS missing returns zero scores")


def test_later_affirmative_fallback_is_not_hidden_by_earlier_denial():
    """An earlier truthful denial must not mask a later affirmative fallback."""
    from scripts.check_repo_consistency import ragas_zero_fallback_claims

    assert ragas_zero_fallback_claims("RAGAS does not return zero on success, but returns zero when unavailable")


def test_zero_non_score_values_are_not_fallbacks():
    """Zero counts/statuses are not the retired score fallback."""
    from scripts.check_repo_consistency import ragas_zero_fallback_claims

    assert ragas_zero_fallback_claims("RAGAS returns zero failed samples when every sample succeeds") == []
    assert ragas_zero_fallback_claims("RAGAS returns zero exit status on success") == []


def test_denial_in_previous_clause_does_not_hide_later_fallback():
    """A denial in one clause must not suppress an affirmative in the next."""
    from scripts.check_repo_consistency import ragas_zero_fallback_claims

    assert ragas_zero_fallback_claims("RAGAS does not return zero, returns zero score when unavailable")


def test_ragas_fallback_claim_fails_consistency_guard(tmp_path, monkeypatch):
    """A canonical doc with the retired fallback claim must fail the full guard."""
    import scripts.check_repo_consistency as guard

    stale = tmp_path / "stale.md"
    stale.write_text("缺少 RAGAS 时 evaluator 返回零分并附 _warning。", encoding="utf-8")
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [stale])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", [])

    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert errors


def test_ragas_failure_status_and_no_report_are_independent_contracts(tmp_path, monkeypatch):
    """Status and report-suppression are checked independently, not as one OR."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    doc = tmp_path / "doc.md"

    doc.write_text("RAGAS unavailable fails fast with a non-zero exit.", encoding="utf-8")
    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert any("does not produce a quality report" in error for error in errors)
    assert not any("unsuccessful/non-zero" in error for error in errors)

    doc.write_text("RAGAS unavailable 不生成质量报告。", encoding="utf-8")
    errors = []
    guard.check_ragas_failure_contract(errors)
    assert any("unsuccessful/non-zero" in error for error in errors)
    assert not any("does not produce a quality report" in error for error in errors)

    doc.write_text("RAGAS unavailable fails fast with a non-zero exit and no quality report.", encoding="utf-8")
    errors = []
    guard.check_ragas_failure_contract(errors)
    assert errors == []


def test_ragas_unavailable_condition_does_not_count_as_negation():
    """The 不 in 不可用 negates the condition, not the fallback action."""
    from scripts.check_repo_consistency import ragas_zero_fallback_claims

    assert ragas_zero_fallback_claims("RAGAS 不可用时返回零分")


def test_ragas_not_installed_condition_does_not_count_as_negation():
    """The not in 'not installed' negates the condition, not the fallback."""
    from scripts.check_repo_consistency import ragas_zero_fallback_claims

    assert ragas_zero_fallback_claims("When RAGAS is not installed it returns zero scores.")


def test_explicit_zero_fallback_denial_is_allowed():
    from scripts.check_repo_consistency import ragas_zero_fallback_claims

    assert ragas_zero_fallback_claims("RAGAS unavailable 时不会返回零分质量报告。") == []
    assert ragas_zero_fallback_claims("The CLI never returns zero-score reports.") == []


def test_ragas_failfast_statement_is_accepted():
    """A truthful fail-fast statement must satisfy both contracts independently."""
    from scripts import check_repo_consistency as guard

    positive = "RAGAS unavailable 时 evaluator fail fast，返回非零状态且不生成 quality report。"
    assert guard.ragas_zero_fallback_claims(positive) == []
    assert guard.RAGAS_FAILURE_STATUS_REQUIRED_RE.search(positive)
    assert guard.RAGAS_NO_REPORT_REQUIRED_RE.search(positive)

    # A bare UNAVAILABLE marker must not satisfy the failure-status contract.
    assert not guard.RAGAS_FAILURE_STATUS_REQUIRED_RE.search("RAGAS UNAVAILABLE")


def test_ragas_contracts_are_scoped_to_ragas_clause(tmp_path, monkeypatch):
    """Unrelated fail-fast wording must not satisfy the RAGAS status contract."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    doc = tmp_path / "doc.md"

    doc.write_text(
        "Docker Compose will fail-fast on missing env.\nRAGAS unavailable produces no quality report.",
        encoding="utf-8",
    )
    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert any("unsuccessful/non-zero" in error for error in errors)

    doc.write_text(
        "Docker Compose will fail-fast on missing env.\n"
        "RAGAS unavailable returns a non-zero status and no quality report.",
        encoding="utf-8",
    )
    errors = []
    guard.check_ragas_failure_contract(errors)
    assert errors == []


def test_ragas_guarantees_must_be_tied_to_failure_clause(tmp_path, monkeypatch):
    """An unrelated RAGAS line must not satisfy the no-report guarantee."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    (tmp_path / "doc.md").write_text(
        "RAGAS unavailable returns a non-zero status.\nThere is no quality report from last month RAGAS benchmark.",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert any("does not produce a quality report" in error for error in errors)


def test_ragas_report_guarantee_must_share_failure_clause(tmp_path, monkeypatch):
    """A non-failure RAGAS clause must not satisfy the no-report guarantee."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    (tmp_path / "doc.md").write_text(
        "RAGAS unavailable returns a non-zero status; the RAGAS dry-run produces no quality report.",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert any("does not produce a quality report" in error for error in errors)


def test_negated_ragas_failure_status_is_rejected(tmp_path, monkeypatch):
    """A negated fail-fast/non-zero statement must not satisfy the status half."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    doc = tmp_path / "doc.md"

    doc.write_text(
        "RAGAS unavailable does not fail fast and produces no quality report.",
        encoding="utf-8",
    )
    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert any("unsuccessful/non-zero" in error for error in errors)

    doc.write_text(
        "RAGAS unavailable will not return a non-zero status, but no quality report is produced.",
        encoding="utf-8",
    )
    errors = []
    guard.check_ragas_failure_contract(errors)
    assert any("unsuccessful/non-zero" in error for error in errors)


def test_failure_to_return_is_status_negation(tmp_path, monkeypatch):
    """`fails to return` / `unable to return` negate the status guarantee."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    doc = tmp_path / "doc.md"

    doc.write_text(
        "RAGAS unavailable fails to return a non-zero status and produces no quality report.",
        encoding="utf-8",
    )
    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert any("unsuccessful/non-zero" in error for error in errors)

    doc.write_text(
        "RAGAS unavailable is unable to return a non-zero status and produces no quality report.",
        encoding="utf-8",
    )
    errors = []
    guard.check_ragas_failure_contract(errors)
    assert any("unsuccessful/non-zero" in error for error in errors)


def test_contracted_status_negation_is_rejected(tmp_path, monkeypatch):
    """Contracted auxiliaries (`can't`, `won't`) negate the status guarantee."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    doc = tmp_path / "doc.md"

    doc.write_text(
        "RAGAS unavailable can't return a non-zero status and produces no quality report.",
        encoding="utf-8",
    )
    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert any("unsuccessful/non-zero" in error for error in errors)

    doc.write_text(
        "RAGAS unavailable won't return a non-zero status and produces no quality report.",
        encoding="utf-8",
    )
    errors = []
    guard.check_ragas_failure_contract(errors)
    assert any("unsuccessful/non-zero" in error for error in errors)


def test_non_guaranteed_status_is_rejected(tmp_path, monkeypatch):
    """`not guaranteed to return` negates the non-zero status guarantee."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    (tmp_path / "doc.md").write_text(
        "RAGAS unavailable is not guaranteed to return a non-zero status and produces no quality report.",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert any("unsuccessful/non-zero" in error for error in errors)


def test_ragas_report_guarantee_must_share_failure_clause_with_comma(tmp_path, monkeypatch):
    """A comma-joined non-failure RAGAS clause must not satisfy the no-report half."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    (tmp_path / "doc.md").write_text(
        "RAGAS unavailable returns a non-zero status, the RAGAS dry-run produces no quality report.",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert any("does not produce a quality report" in error for error in errors)


def test_negated_no_report_assertion_is_rejected(tmp_path, monkeypatch):
    """A denied report-suppression statement must not satisfy the no-report half."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    (tmp_path / "doc.md").write_text(
        "RAGAS unavailable returns a non-zero status but this does not mean no quality report.",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert any("does not produce a quality report" in error for error in errors)


def test_non_guarantee_of_report_suppression_is_rejected(tmp_path, monkeypatch):
    """`does not guarantee/assert/imply` negates the no-report guarantee."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    doc = tmp_path / "doc.md"

    doc.write_text(
        "RAGAS unavailable returns a non-zero status but does not guarantee no quality report.",
        encoding="utf-8",
    )
    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert any("does not produce a quality report" in error for error in errors)

    doc.write_text(
        "RAGAS unavailable returns a non-zero status but does not imply no quality report.",
        encoding="utf-8",
    )
    errors = []
    guard.check_ragas_failure_contract(errors)
    assert any("does not produce a quality report" in error for error in errors)


def test_successful_run_does_not_bootstrap_failure_scope(tmp_path, monkeypatch):
    """Guarantee wording alone must not classify a segment as evaluator failure."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    doc = tmp_path / "doc.md"

    doc.write_text("A successful RAGAS run will fail fast and produce no quality report.", encoding="utf-8")
    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert any("unsuccessful/non-zero" in error for error in errors)
    assert any("does not produce a quality report" in error for error in errors)

    doc.write_text("RAGAS 正常可用时返回非零状态且不生成质量报告。", encoding="utf-8")
    errors = []
    guard.check_ragas_failure_contract(errors)
    assert any("unsuccessful/non-zero" in error for error in errors)
    assert any("does not produce a quality report" in error for error in errors)


def test_unrelated_missing_data_does_not_bootstrap_failure_scope(tmp_path, monkeypatch):
    """Availability wording must bind to the evaluator, not unrelated data."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    (tmp_path / "doc.md").write_text(
        "A successful RAGAS run with missing optional metadata returns a non-zero status and no quality report.",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert any("unsuccessful/non-zero" in error for error in errors)
    assert any("does not produce a quality report" in error for error in errors)


def test_conditional_ragas_clause_keeps_guarantees(tmp_path, monkeypatch):
    """A leading `When ...` condition must not be split from its guarantees."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    (tmp_path / "doc.md").write_text(
        "When RAGAS is unavailable, the CLI fails fast with a non-zero status and produces no quality report.",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert errors == []


def test_missing_ragas_is_a_failure_condition(tmp_path, monkeypatch):
    """Directly-bound `RAGAS is missing` counts as evaluator failure."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    (tmp_path / "doc.md").write_text(
        "When RAGAS is missing, the CLI returns a non-zero status and produces no quality report.",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert errors == []


def test_coordinated_ragas_clauses_keep_leading_condition(tmp_path, monkeypatch):
    """A leading condition governs every coordinated clause in the sentence."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    (tmp_path / "doc.md").write_text(
        "When RAGAS is unavailable, the CLI returns a non-zero status, and it produces no quality report.",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert errors == []


def test_contrast_clause_does_not_inherit_leading_condition(tmp_path, monkeypatch):
    """A contrast conjunction must not inherit the leading condition."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    (tmp_path / "doc.md").write_text(
        "When RAGAS is unavailable, the CLI returns a non-zero status,"
        " but a successful RAGAS dry-run produces no quality report.",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert any("does not produce a quality report" in error for error in errors)


def test_additive_budan_is_not_contrast(tmp_path, monkeypatch):
    """Additive `不但` must not be split as a contrast conjunction."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    (tmp_path / "doc.md").write_text(
        "当 RAGAS 不可用时，CLI 不但返回非零状态且不生成质量报告。",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert errors == []


def test_although_is_scope_changing(tmp_path, monkeypatch):
    """`although` changes scope, so the leading condition must not propagate."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    (tmp_path / "doc.md").write_text(
        "When RAGAS is unavailable, the CLI returns a non-zero status,"
        " although a successful RAGAS dry-run produces no quality report.",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert any("does not produce a quality report" in error for error in errors)


def test_bare_non_zero_is_not_a_failure_status(tmp_path, monkeypatch):
    """`non-zero scores` is not a non-zero exit/status guarantee."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    (tmp_path / "doc.md").write_text(
        "When RAGAS is unavailable, it produces non-zero scores and no quality report.",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert any("unsuccessful/non-zero" in error for error in errors)


def test_inflected_fail_fast_is_accepted(tmp_path, monkeypatch):
    """`fails fast` is an ordinary affirmative form of the status guarantee."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    (tmp_path / "doc.md").write_text(
        "When RAGAS is unavailable, the evaluator fails fast and produces no quality report.",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert errors == []


def test_common_report_suppression_forms_are_accepted(tmp_path, monkeypatch):
    """`must not produce` / `without producing` are affirmative suppression forms."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    doc = tmp_path / "doc.md"

    doc.write_text(
        "When RAGAS is unavailable, the evaluator fails fast and must not produce a quality report.",
        encoding="utf-8",
    )
    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert errors == []

    doc.write_text(
        "When RAGAS is unavailable, the evaluator fails fast without producing a quality report.",
        encoding="utf-8",
    )
    errors = []
    guard.check_ragas_failure_contract(errors)
    assert errors == []


def test_future_tense_report_suppression_is_accepted(tmp_path, monkeypatch):
    """`will not produce` / `won't produce` are affirmative suppression forms."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    doc = tmp_path / "doc.md"

    doc.write_text(
        "When RAGAS is unavailable, the CLI returns a non-zero status and will not produce a quality report.",
        encoding="utf-8",
    )
    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert errors == []

    doc.write_text(
        "When RAGAS is unavailable, the CLI returns a non-zero status and won't produce a quality report.",
        encoding="utf-8",
    )
    errors = []
    guard.check_ragas_failure_contract(errors)
    assert errors == []


def test_contracted_report_suppression_is_accepted(tmp_path, monkeypatch):
    """`doesn't produce` is an affirmative report-suppression form."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    (tmp_path / "doc.md").write_text(
        "When RAGAS is unavailable, the CLI returns a non-zero status and doesn't produce a quality report.",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert errors == []


def test_additive_not_only_is_not_negation(tmp_path, monkeypatch):
    """`not only returns a non-zero status` does not negate the status guarantee."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "CANONICAL_DOCS", [])
    monkeypatch.setattr(guard, "RAGAS_REQUIRED_DOCS", ["doc.md"])
    (tmp_path / "doc.md").write_text(
        "When RAGAS is unavailable, the CLI not only returns a non-zero status and produces no quality report.",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_ragas_failure_contract(errors)
    assert errors == []


def test_local_runtime_validation_contract():
    errors: list[str] = []
    check_local_runtime_validation_contract(errors)
    assert errors == []


def test_local_validation_marker_must_be_affirmative(tmp_path, monkeypatch):
    """A bare marker with no validated-dependency statement must be rejected."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "LOCAL_VALIDATION_DOCS", ["doc.md"])
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    (tmp_path / "doc.md").write_text(
        "LOCAL_REAL_VALIDATION is an evidence category.\n",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_local_runtime_validation_contract(errors)
    assert any("affirmatively" in error for error in errors)


def test_local_validation_marker_requires_non_negated_assertion(tmp_path, monkeypatch):
    """A negated `not yet validated` dependency must not satisfy the marker."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "LOCAL_VALIDATION_DOCS", ["doc.md"])
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    (tmp_path / "doc.md").write_text(
        "LOCAL_REAL_VALIDATION is an evidence category.\nProduction Redis is not yet validated.\n",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_local_runtime_validation_contract(errors)
    assert any("affirmatively" in error for error in errors)


def test_local_validation_requires_all_dependencies(tmp_path, monkeypatch):
    """A single unrelated dependency must not stand in for the required set."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "LOCAL_VALIDATION_DOCS", ["doc.md"])
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    (tmp_path / "doc.md").write_text(
        "LOCAL_REAL_VALIDATION is an evidence category. Redis documentation is available."
        " The Qdrant integration is validated.\n",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_local_runtime_validation_contract(errors)
    assert any("affirmatively" in error for error in errors)


def test_passive_local_validation_negation_is_rejected(tmp_path, monkeypatch):
    """`have not been validated` must not satisfy the local-validation contract."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "LOCAL_VALIDATION_DOCS", ["doc.md"])
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    (tmp_path / "doc.md").write_text(
        "LOCAL_REAL_VALIDATION: Redis, nginx, Elasticsearch, and Prometheus have not been validated locally.\n",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_local_runtime_validation_contract(errors)
    assert errors


def test_english_sentence_boundary_scopes_validation(tmp_path, monkeypatch):
    """ASCII sentence boundaries must separate unrelated validation sentences."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "LOCAL_VALIDATION_DOCS", ["doc.md"])
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    (tmp_path / "doc.md").write_text(
        "LOCAL_REAL_VALIDATION is an evidence category."
        " Redis, nginx, Elasticsearch, and Prometheus are listed dependencies."
        " The Qdrant integration is validated.\n",
        encoding="utf-8",
    )

    errors: list[str] = []
    guard.check_local_runtime_validation_contract(errors)
    assert any("affirmatively" in error for error in errors)


def test_stale_local_validation_claim_is_rejected(tmp_path, monkeypatch):
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "LOCAL_VALIDATION_DOCS", ["stale.md"])
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    (tmp_path / "stale.md").write_text(
        "真实 Redis 多 worker、反向代理、认证 ES 与 Prometheus 抓取仍需部署环境验收。\nLOCAL_REAL_VALIDATION\n",
        encoding="utf-8",
    )
    errors: list[str] = []
    guard.check_local_runtime_validation_contract(errors)
    assert errors


def test_completed_local_redis_validation_cannot_regress():
    from scripts.check_repo_consistency import stale_local_validation_claims

    assert stale_local_validation_claims("真实 Redis 多进程行为尚未验证。")
    assert stale_local_validation_claims("真实反向代理 nginx 客户端 IP 尚未验证。")
    assert stale_local_validation_claims("认证 Elasticsearch 本地集成尚未验证。")
    assert stale_local_validation_claims("真实 Prometheus authenticated scrape 尚未验证。")


def test_es_substring_is_not_matched_as_elasticsearch():
    """The ES abbreviation must be a standalone token, not a word suffix."""
    from scripts.check_repo_consistency import stale_local_validation_claims

    assert stale_local_validation_claims("RAGAS scores 尚未验证") == []
    assert stale_local_validation_claims("认证 ES 本地集成尚未验证。")


def test_redis_cluster_boundary_is_allowed():
    from scripts.check_repo_consistency import stale_local_validation_claims

    assert stale_local_validation_claims("Redis Cluster/Sentinel 生产拓扑尚未验证。") == []
    assert stale_local_validation_claims("Redis Cluster 仍需验证。") == []


def test_multinode_es_tls_boundary_is_allowed():
    from scripts.check_repo_consistency import stale_local_validation_claims

    assert stale_local_validation_claims("多节点 Elasticsearch/TLS 尚未验证。") == []
    assert stale_local_validation_claims("multi-node Elasticsearch TLS not yet validated.") == []


def test_long_run_prometheus_grafana_boundary_is_allowed():
    from scripts.check_repo_consistency import stale_local_validation_claims

    assert stale_local_validation_claims("长期 Prometheus/Grafana 运维尚未验证。") == []
    assert stale_local_validation_claims("production Prometheus HA/SLO not yet validated.") == []


def test_production_boundary_exemption_is_clause_scoped():
    """A production boundary in one clause must not hide a stale claim elsewhere."""
    from scripts.check_repo_consistency import stale_local_validation_claims

    assert stale_local_validation_claims("真实 Redis 多进程行为尚未验证；production cluster 也未验证")


def test_production_boundary_exemption_splits_commas():
    """Comma-delimited clauses are split before applying the boundary exemption."""
    from scripts.check_repo_consistency import stale_local_validation_claims

    assert stale_local_validation_claims("真实 Redis 多进程行为尚未验证，production cluster 也未验证")
    assert stale_local_validation_claims("真实 Redis 多进程行为尚未验证, production cluster 也未验证")


def test_production_boundary_exemption_splits_colons():
    """Colon-delimited clauses are split before applying the boundary exemption."""
    from scripts.check_repo_consistency import stale_local_validation_claims

    assert stale_local_validation_claims("真实 Redis 多进程行为尚未验证：production cluster 也未验证")
    assert stale_local_validation_claims("真实 Redis 多进程行为尚未验证: production cluster 也未验证")


def test_leading_production_qualifier_is_preserved():
    """A leading scope qualifier must govern the clause that follows it."""
    from scripts.check_repo_consistency import stale_local_validation_claims

    assert stale_local_validation_claims("In production, Redis is not yet validated") == []
    assert stale_local_validation_claims("对于生产环境，Redis 尚未验证") == []


def test_tracing_default_is_not_misdescribed_as_local_memory_spans():
    """The default install uses the OTel SDK provider, not the in-memory fallback.

    The wording this test used to pin ("没有配置任何 exporter") was the drift
    Issue #22 removed: it read as "the exporter does not exist", while the truth is
    "the exporter is implemented and disabled by default". The intent is kept and
    the assertion now targets the default-off scope plus the SDK fallback boundary.
    """
    from pathlib import Path

    text = Path("docs/architecture-baseline.md").read_text(encoding="utf-8")
    assert "TracerProvider" in text
    assert "OTel SDK 未安装" in text and "初始化失败" in text
    assert "OTEL_EXPORT_ENABLED=false" in text
    # The superseded denial must not come back.
    assert "没有配置任何 exporter" not in text


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


def test_canonical_docs_include_validation_and_benchmark_contract():
    """Evidence-heavy docs must be held to the same guard as the guides."""
    from pathlib import Path

    from scripts.check_repo_consistency import CANONICAL_DOCS

    relative = {str(path.relative_to(Path.cwd())) for path in CANONICAL_DOCS}
    assert "docs/validation/real-ragas-evaluation.md" in relative
    assert "docs/validation/v2.5-runtime-security-validation.md" in relative
    assert "artifacts/benchmarks/README.md" in relative


def test_forbidden_evidence_claims_flag_unevidenced_results():
    assert forbidden_evidence_claims("The retrieval benchmark reports Recall@10 = 0.87.")
    assert forbidden_evidence_claims("HitRate@5: 0.61 is the measured result.")
    assert forbidden_evidence_claims("RAGAS faithfulness score 0.82 was produced.")
    assert forbidden_evidence_claims("真实 BGE 模型已完成验证，可直接用于生产。")
    assert forbidden_evidence_claims("Real configured CLIP has been validated.")
    assert forbidden_evidence_claims("PaddleOCR 已运行通过全流程验证。")
    assert forbidden_evidence_claims("OpenTelemetry/Jaeger 导出闭环已完成。")
    assert forbidden_evidence_claims("OTel export closed-loop verified with a live collector.")
    assert forbidden_evidence_claims("压测 P95 吞吐已达标，满足 QPS 要求。")


def test_forbidden_evidence_claims_allow_required_denials():
    """The docs are required to write the denial, so denials must not trip the guard."""
    assert forbidden_evidence_claims("Retrieval benchmark result: PENDING — no artifact exists.") == []
    assert (
        forbidden_evidence_claims(
            "- Claiming OpenTelemetry/Jaeger export is closed-loop when the default is the\n"
            "  OTel SDK provider with no exporter configured."
        )
        == []
    )
    assert (
        forbidden_evidence_claims(
            'Say "tracing hook wired, no exporter configured", not "OTel/Jaeger export closed loop".'
        )
        == []
    )
    assert forbidden_evidence_claims("真实 BGE smoke 未执行时为 EXTERNAL_MODEL_ASSET_REQUIRED。") == []
    assert forbidden_evidence_claims("PRD P95/P99/QPS values are design targets.") == []
    # Metric *names* are legitimate; only an attached value is a claim.
    assert (
        forbidden_evidence_claims("The harness computes Recall@1/3/5/10, HitRate@1/3/5/10, MRR@10 and NDCG@10.") == []
    )


def test_current_docs_have_no_unevidenced_evidence_claims():
    from scripts.check_repo_consistency import CANONICAL_DOCS

    for path in CANONICAL_DOCS:
        if not path.exists():
            continue
        claims = forbidden_evidence_claims(path.read_text(encoding="utf-8"))
        assert not claims, f"{path} claims unevidenced evidence: {claims}"


def test_benchmark_framework_result_split_is_documented():
    from pathlib import Path

    from scripts.check_repo_consistency import BENCHMARK_CLASSIFICATION_DOCS

    for name in BENCHMARK_CLASSIFICATION_DOCS:
        path = Path(name)
        assert path.exists(), name
        text = path.read_text(encoding="utf-8")
        assert "REPO_VERIFIED" in text, f"{name} must classify the benchmark framework"
        assert "PENDING" in text, f"{name} must classify the benchmark result"


def test_no_committed_benchmark_artifact_means_result_stays_pending():
    """No artifact on disk => the result must not be stated as available."""
    from scripts.check_repo_consistency import BENCHMARK_CLASSIFICATION_DOCS

    if benchmark_artifact_exists():
        return  # an artifact exists; the docs would then be updated instead
    for name in BENCHMARK_CLASSIFICATION_DOCS:
        text = Path(name).read_text(encoding="utf-8")
        assert "PENDING" in text, f"{name} must keep the result PENDING without an artifact"


def test_benchmark_artifact_presence_is_derived_from_disk(monkeypatch, tmp_path):
    """Artifact presence must come from the working tree, never the GitHub API."""
    import scripts.check_repo_consistency as guard

    source = Path(guard.__file__).read_text(encoding="utf-8")
    # No network client, no wall clock: the guard must be offline-deterministic.
    for forbidden in ("import requests", "import urllib.request", "import httpx", "date.today", "datetime.now"):
        assert forbidden not in source, f"guard must not use {forbidden}"

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    assert guard.benchmark_artifact_exists() is False

    run_dir = tmp_path / "artifacts/benchmarks/run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text("{}", encoding="utf-8")
    assert guard.benchmark_artifact_exists() is True


def test_benchmark_classification_requires_a_split_when_state_is_discussed():
    silent = "# Benchmark\n\nThe retrieval benchmark result is excellent.\n"
    assert benchmark_classification_errors("x.md", silent)

    classified = "# Benchmark\n\nRetrieval benchmark framework: REPO_VERIFIED. Result: PENDING.\n"
    assert benchmark_classification_errors("x.md", classified) == []

    # A doc that never discusses the benchmark is out of scope for this guard.
    assert benchmark_classification_errors("x.md", "# Deployment\n\nRun docker compose up.\n") == []


def test_v25_release_claims_are_flagged_but_working_milestone_wording_passes():
    assert v25_release_claims("The repository was released as v2.5 on 2026-06-06.")
    assert v25_release_claims("正式发布版本：v2.5")
    assert v25_release_claims("v2.5 is the current runtime version.")

    assert v25_release_claims("v2.5 is a historical working milestone label, not a release.") == []
    assert v25_release_claims("config.json system.version stays 2.3.0") == []
    assert v25_release_claims("`v2.5` 是历史 working milestone 标签，**不是**正式发布版本") == []


def test_canonical_docs_keep_v25_as_working_milestone():
    errors: list[str] = []
    check_version_label_semantics(errors)
    assert errors == []


def test_version_label_guard_catches_injected_release_claim(tmp_path, monkeypatch):
    import scripts.check_repo_consistency as guard

    (tmp_path / "README.md").write_text("Released as v2.5 last quarter.\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    errors: list[str] = []
    guard.check_version_label_semantics(errors)
    assert errors


def test_prd_design_targets_guard_passes_for_current_prd():
    errors: list[str] = []
    check_prd_design_targets(errors)
    assert errors == []


def test_runtime_version_matches_latest_dated_release():
    """config.json system.version must equal the newest dated CHANGELOG heading."""
    import json
    import re
    from pathlib import Path

    config = json.loads(Path("config.json").read_text(encoding="utf-8"))
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    releases = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)
    assert releases
    assert config["system"]["version"] == releases[0]
    # Unreleased is present and does not assign a version.
    assert "## [Unreleased]" in changelog


def test_no_v25_version_heading_was_invented():
    """A fabricated 2.5.0 release entry would contradict the runtime version."""
    import re
    from pathlib import Path

    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    assert not re.search(r"^## \[2\.5\.0\]", changelog, re.MULTILINE)
    assert "## [2.3.0]" in changelog


# ── enterprise readiness evidence guards ────────────────────────────────────


def test_enterprise_claims_pass_for_current_docs():
    from scripts.check_repo_consistency import check_enterprise_readiness_contracts

    errors: list[str] = []
    check_enterprise_readiness_contracts(errors)
    assert errors == []


def test_enterprise_readiness_coverage_passes():
    from scripts.check_repo_consistency import check_enterprise_readiness_coverage

    errors: list[str] = []
    check_enterprise_readiness_coverage(errors)
    assert errors == []


def test_performance_result_claim_is_flagged_without_an_artifact():
    from scripts.check_repo_consistency import enterprise_claim_errors

    overclaim = "Throughput was validated at 12 QPS on the production configuration.\n"
    assert enterprise_claim_errors("x.md", overclaim)


def test_performance_design_target_wording_is_allowed():
    from scripts.check_repo_consistency import enterprise_claim_errors

    honest = "Throughput target is a DESIGN_TARGET; no performance artifact is committed.\n"
    assert enterprise_claim_errors("x.md", honest) == []


def test_otel_closed_loop_claim_is_flagged_without_runtime_evidence():
    from scripts.check_repo_consistency import enterprise_claim_errors

    overclaim = "The OTLP closed loop was validated against a live collector.\n"
    assert enterprise_claim_errors("x.md", overclaim)


def test_otel_implementation_only_wording_is_allowed():
    from scripts.check_repo_consistency import enterprise_claim_errors

    honest = (
        "The exporter is implemented; the runtime closed loop is PENDING because no "
        "local application to collector to backend run is recorded.\n"
    )
    assert enterprise_claim_errors("x.md", honest) == []


def test_production_fired_alert_claim_is_flagged():
    from scripts.check_repo_consistency import enterprise_claim_errors

    overclaim = "RagHighErrorRate fired in production last quarter.\n"
    assert enterprise_claim_errors("x.md", overclaim)


def test_slo_achieved_claim_is_flagged():
    from scripts.check_repo_consistency import enterprise_claim_errors

    overclaim = "The service currently achieves 99.9% availability.\n"
    assert enterprise_claim_errors("x.md", overclaim)


def test_audit_implemented_claim_is_flagged_when_events_are_absent(monkeypatch, tmp_path):
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/x.md").write_text(
        "Structured enterprise audit is implemented for all admin actions.\n", encoding="utf-8"
    )
    errors = guard.enterprise_claim_errors("x.md", (tmp_path / "docs/x.md").read_text(encoding="utf-8"))
    assert errors


def test_dashboard_claim_is_flagged_when_no_dashboard_exists(monkeypatch, tmp_path):
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    text = "The Grafana dashboard is available for the on-call rotation.\n"
    assert guard.enterprise_claim_errors("x.md", text)


def test_artifact_presence_is_derived_from_disk(monkeypatch, tmp_path):
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    assert guard.performance_artifact_exists() is False
    run = tmp_path / "artifacts/performance/run-1"
    run.mkdir(parents=True)
    (run / "metadata.json").write_text("{}", encoding="utf-8")
    assert guard.performance_artifact_exists() is True

    assert guard.otel_runtime_evidence_exists() is False
    evidence = tmp_path / "monitoring/evidence"
    evidence.mkdir(parents=True)
    # A recorded artifact, not just a file: an empty `{}` is not evidence, which is
    # asserted separately in test_otel_runtime_evidence_requires_a_readable_non_empty_json_object.
    (evidence / "span.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "evidence_type": "otel_closed_loop",
                "status": "EXECUTED",
                "backend": "jaeger",
                "trace_id": "0123456789abcdef0123456789abcdef",
                "queried_span_count": 1,
            }
        ),
        encoding="utf-8",
    )
    assert guard.otel_runtime_evidence_exists() is True


def test_enterprise_capability_probes_reflect_the_repository():
    from scripts.check_repo_consistency import (
        audit_action_events_exist,
        grafana_dashboard_exists,
        otlp_exporter_implemented,
        prometheus_alert_rules_exist,
        slo_runbook_exists,
    )

    assert audit_action_events_exist()
    assert prometheus_alert_rules_exist()
    assert grafana_dashboard_exists()
    assert slo_runbook_exists()
    assert otlp_exporter_implemented()


def test_observability_stack_is_optional():
    from scripts.check_repo_consistency import check_observability_is_optional

    errors: list[str] = []
    check_observability_is_optional(errors)
    assert errors == []


def test_optional_observability_stack_is_not_part_of_canonical_deployment():
    from pathlib import Path

    import yaml

    base = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    overlay = yaml.safe_load(Path("docker-compose.observability.yml").read_text(encoding="utf-8"))
    assert not set(base.get("services", {})) & set(overlay.get("services", {}))


def test_canonical_deployment_has_no_forbidden_platform():
    from scripts.check_repo_consistency import check_canonical_runtime_is_not_observability_gated

    errors: list[str] = []
    check_canonical_runtime_is_not_observability_gated(errors)
    assert errors == []


def test_guards_are_offline_and_deterministic():
    """No network, no wall clock, no hardcoded test counts or PR numbers."""
    from pathlib import Path

    import scripts.check_repo_consistency as guard

    source = Path(guard.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "import requests",
        "import urllib.request",
        "import httpx",
        "date.today",
        "datetime.now",
        "time.time()",
    ):
        assert forbidden not in source, f"consistency guard must not use {forbidden}"


def test_explicit_denial_of_a_claim_is_not_flagged():
    """These documents are required to write the denial; it must not be misread."""
    from scripts.check_repo_consistency import enterprise_claim_errors

    for denial in (
        "No alert has fired in production.",
        "no SLO has been met.",
        "The service has not been validated in production.",
    ):
        assert enterprise_claim_errors("x.md", denial + "\n") == [], denial


# ── post-#21 drift guards ───────────────────────────────────────────────────


def test_emitted_prometheus_metrics_are_derived_from_the_collector():
    """The inventory must come from the collector's own literals, not a hardcoded list."""
    from scripts.check_repo_consistency import emitted_prometheus_metrics, emitted_prometheus_prefixes

    available = emitted_prometheus_metrics()
    # Spot-check the series the alert rules and runbook depend on.
    assert {
        "rag_http_requests",
        "rag_http_rate_limited",
        "rag_http_request_duration_seconds",
        "rag_http_active_requests",
        "rag_redis_degraded_mode",
        "rag_redis_degraded_events",
        "rag_kv_pressure",
        "rag_cache_total",
        "rag_otel_exporter_enabled",
        "rag_uptime_seconds",
    } <= available
    # Names that do not exist. The confirmed pre-#21 drift was operational docs
    # sending operators to these.
    for phantom in ("rag_cache_hit_rate", "rag_rewrite_fallback_rate", "rag_http_responses_total"):
        assert phantom not in available

    # `cache.hit.<level>` and `http.responses.<class>xx` are assembled at runtime, so
    # the exporter has no single spelling for them and only the prefix is canonical.
    prefixes = emitted_prometheus_prefixes()
    assert {"rag_cache_hit_", "rag_http_responses_"} <= prefixes


def test_emitted_metric_inventory_matches_the_live_collector():
    """The static inventory must agree with what the collector really exposes.

    A static derivation is only trustworthy while it tracks the code, so it is
    cross-checked against a live run: every series the collector writes must either
    be named outright, or fall under a declared runtime-assembled prefix.
    """
    import re

    from monitoring.otel_tracer import MetricsCollector
    from scripts.check_repo_consistency import emitted_prometheus_metrics, emitted_prometheus_prefixes

    collector = MetricsCollector()
    collector.set_active_requests(3)
    for status in (200, 401, 429, 500):
        collector.record_http_request(status, 12.5)
    collector.set_redis_degraded(True)
    collector.set_redis_degraded(False)
    collector.increment("cache.hit.L1")
    collector.increment("cache.hit.L2")
    collector.increment("cache.total")
    collector.observe_histogram("latency.rewrite", 8.0)

    live = set(re.findall(r"^(rag_[A-Za-z0-9_]+)", collector.to_prometheus_text(), flags=re.MULTILINE))
    prefixes = emitted_prometheus_prefixes()

    uncovered = {
        name
        for name in live
        if name not in emitted_prometheus_metrics() and not any(name.startswith(prefix) for prefix in prefixes)
    }
    assert not uncovered, f"live series the static inventory cannot explain: {sorted(uncovered)}"

    # And the prefixes must correspond to names the collector really assembles.
    for expected in ("rag_cache_hit_L1", "rag_http_responses_5xx", "rag_latency_rewrite_seconds"):
        assert expected in live, f"fixture no longer produces {expected}"
        assert any(expected.startswith(prefix) for prefix in prefixes), f"{expected} is not covered by a prefix"


def test_summary_series_are_not_invented_for_gauges():
    """Only histograms gain a `_seconds` summary; a gauge must not grow one."""
    from scripts.check_repo_consistency import emitted_prometheus_metrics

    available = emitted_prometheus_metrics()
    assert "rag_http_request_duration_seconds" in available
    assert "rag_redis_degraded_mode_seconds" not in available
    assert "rag_kv_pressure_seconds" not in available


def test_operational_docs_only_cite_emitted_series():
    from scripts.check_repo_consistency import check_operational_metric_references

    errors: list[str] = []
    check_operational_metric_references(errors)
    assert errors == []


def test_non_existent_cache_hit_rate_series_is_flagged():
    """Regression: `rag_cache_hit_rate` sent operators to a target that never existed."""
    from scripts.check_repo_consistency import operational_metric_reference_errors

    text = "先看 `rag_cache_hit_rate`，再看 `rag_kv_pressure`。\n"
    errors = operational_metric_reference_errors("docs/x.md", text)
    assert errors, "a bare reference to a non-emitted series must be flagged"
    assert "rag_cache_hit_rate" in errors[0]


def test_metric_guard_is_inert_without_a_collector(monkeypatch, tmp_path):
    """With no collector on disk there is no inventory, so the guard claims nothing."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/operations-guide.md").write_text("look at `rag_cache_hit_rate`\n", encoding="utf-8")
    errors: list[str] = []
    guard.check_operational_metric_references(errors)
    assert errors == []


def test_derived_ratio_with_promql_is_allowed():
    from scripts.check_repo_consistency import operational_metric_reference_errors

    text = (
        "Cache hit rate:\n\n"
        "```promql\n"
        "rate(rag_cache_hit_L1[5m]) / clamp_min(rate(rag_cache_total[5m]), 0.000001)\n"
        "```\n\n"
        "There is no `rag_cache_hit_rate` series; it is a derived ratio.\n"
    )
    assert operational_metric_reference_errors("docs/x.md", text) == []


def test_stats_pointer_is_enough_to_justify_a_derived_name():
    from scripts.check_repo_consistency import operational_metric_reference_errors

    text = "命中率见 `/api/stats` 的 `cache_hit_rate`；不存在 `rag_cache_hit_rate`。\n"
    assert operational_metric_reference_errors("docs/x.md", text) == []


def test_qdrant_collection_names_are_not_treated_as_metrics():
    from scripts.check_repo_consistency import operational_metric_reference_errors

    text = "核对 Qdrant 集合名：`rag_text_768`、`rag_image_512`\n"
    assert operational_metric_reference_errors("docs/x.md", text) == []


def test_grep_family_prefix_is_not_treated_as_a_series():
    from scripts.check_repo_consistency import operational_metric_reference_errors

    text = "curl -s http://localhost:8000/api/metrics | grep rag_http\n"
    assert operational_metric_reference_errors("docs/x.md", text) == []


def test_glob_family_reference_is_not_treated_as_a_series():
    """`rag_http_*` names a family, not one series, exactly like `grep rag_http` does.

    The token regex absorbs the trailing underscore, so the prefix test compares
    `rag_http__` against the inventory, matches nothing, and would report the doc as
    citing a series that does not exist.
    """
    from scripts.check_repo_consistency import operational_metric_reference_errors

    text = "该端点暴露 `rag_http_*`、`rag_vllm_generation_*` 等系列。\n"
    assert operational_metric_reference_errors("docs/x.md", text) == []


def test_glob_over_a_family_that_is_never_emitted_is_flagged():
    """Accepting the glob form must not accept a family the collector never emits."""
    from scripts.check_repo_consistency import operational_metric_reference_errors

    text = "该端点暴露 `rag_totally_invented_*` 系列。\n"
    errors = operational_metric_reference_errors("docs/x.md", text)
    assert errors
    assert "rag_totally_invented_" in errors[0]


def test_evidence_documents_are_in_the_metric_reference_scope():
    """A phantom series in the evidence map or the truth audit used to pass silently.

    Both enumerate emitted series while classifying evidence, and the README names
    `/api/metrics` as the endpoint carrying them, so none of the three can be exempt.
    """
    from scripts.check_repo_consistency import OPERATIONAL_METRIC_DOCS

    for name in ("README.md", "docs/evidence-map.md", "docs/repository-truth-audit.md"):
        assert name in OPERATIONAL_METRIC_DOCS, f"{name} cites rag_* series and must be scanned"


def test_exporter_truth_contract_passes_for_current_docs():
    from scripts.check_repo_consistency import check_exporter_truth_contract

    errors: list[str] = []
    check_exporter_truth_contract(errors)
    assert errors == []


def test_doc_that_only_denies_an_exporter_is_flagged():
    """The pre-#21 drift: docs described the exporter as absent, not as disabled."""
    from scripts.check_repo_consistency import exporter_truth_claim_errors

    stale = "默认依赖会安装 opentelemetry-sdk，但没有配置任何 exporter，span 既不导出也不保留。\n"
    assert exporter_truth_claim_errors("docs/x.md", stale)

    current = "默认 `OTEL_EXPORT_ENABLED=false`，exporter 实现位于 `monitoring/otel_exporter.py`，默认不启用。\n"
    assert exporter_truth_claim_errors("docs/x.md", current) == []

    # A doc that never discusses tracing is out of scope.
    assert exporter_truth_claim_errors("docs/x.md", "# Deployment\n\nRun docker compose up.\n") == []


def test_architecture_baseline_must_split_exporter_implementation_from_closed_loop():
    from scripts.check_repo_consistency import check_architecture_baseline_exporter_split

    errors: list[str] = []
    check_architecture_baseline_exporter_split(errors)
    assert errors == []


def test_audit_tracker_requires_open_external_validation():
    from scripts.check_repo_consistency import (
        OPEN_EXTERNAL_VALIDATION_TRACKERS,
        _issue_reference,
        _tracker_bullets,
        _tracker_section,
        audit_tracker_errors,
    )

    audit = (Path("docs/repository-truth-audit.md")).read_text(encoding="utf-8")
    assert audit_tracker_errors(audit) == []

    # Derived from the parsed tracker rows rather than hardcoded prose, so adding a
    # tracker or rewording a row cannot silently turn this into a no-op fixture. Every
    # standalone 'open' in the row is flipped, because a row that still says 'open'
    # anywhere has not stopped recording the tracker as open.
    tracker = _tracker_section(audit)
    assert tracker is not None
    bullets = _tracker_bullets(tracker)
    for number in OPEN_EXTERNAL_VALIDATION_TRACKERS:
        bullet = next(item for item in bullets if _issue_reference(number).search(item))
        assert re.search(r"\bopen\b", bullet, re.IGNORECASE), f"#{number} must be recorded as open"

        closed = audit.replace(bullet, re.sub(r"\bopen\b", "closed", bullet))
        assert closed != audit, f"fixture must actually change the state of tracker #{number}"
        assert any(f"#{number}" in error for error in audit_tracker_errors(closed)), (
            f"recording open tracker #{number} as closed must fail the guard"
        )


def test_audit_tracker_keeps_completed_reconciliation_off_the_current_scope():
    """A completed issue described as "the current one" is the drift to prevent."""
    from scripts.check_repo_consistency import (
        COMPLETED_RECONCILIATION_ISSUES,
        _issue_reference,
        _tracker_bullets,
        _tracker_section,
        audit_tracker_errors,
    )

    audit = Path("docs/repository-truth-audit.md").read_text(encoding="utf-8")
    assert audit_tracker_errors(audit) == []

    tracker = _tracker_section(audit)
    assert tracker is not None
    bullets = _tracker_bullets(tracker)

    for number in COMPLETED_RECONCILIATION_ISSUES:
        bullet = next(item for item in bullets if _issue_reference(number).search(item))
        for phrasing in (
            " This is the current one.",
            " This is the current open scope.",
            " This is the current reconciliation scope.",
        ):
            stale = audit.replace(bullet, bullet + phrasing)
            assert stale != audit, f"fixture must actually re-open the completed scope of #{number}"
            assert any(f"#{number}" in error for error in audit_tracker_errors(stale)), (
                f"#{number} is completed and must not also be described as the current scope ({phrasing!r})"
            )


def test_current_scope_matcher_does_not_flag_ordinary_current_wording():
    """The broadened matcher must not swallow 'the current PR' or 'the current implementation'."""
    from scripts.check_repo_consistency import _CURRENT_SCOPE_RE

    assert _CURRENT_SCOPE_RE.search("awaiting the current PR.") is None
    assert _CURRENT_SCOPE_RE.search("the current implementation is fine.") is None
    assert _CURRENT_SCOPE_RE.search("the current evidence map.") is None
    # The adjective between "current" and the scope noun is what the old, stricter
    # pattern missed, so this case is the regression that matters.
    assert _CURRENT_SCOPE_RE.search("described as the current open scope.") is not None
    assert _CURRENT_SCOPE_RE.search("described as the current one.") is not None


def test_no_current_reconciliation_issue_is_a_legal_state():
    """Between reconciliations there is no open scope, and that needs no invented issue."""
    from scripts.check_repo_consistency import CURRENT_RECONCILIATION_ISSUE, audit_tracker_errors

    audit = Path("docs/repository-truth-audit.md").read_text(encoding="utf-8")
    assert CURRENT_RECONCILIATION_ISSUE is None, (
        "the reconciliation model must be able to express 'no open reconciliation issue'"
    )
    assert audit_tracker_errors(audit) == [], (
        "an audit that states no current reconciliation issue must pass without an issue number"
    )


def test_audit_must_state_the_absent_reconciliation_scope_explicitly():
    """Silence is not a legal way to say 'none': the declaration has to be findable."""
    from scripts.check_repo_consistency import (
        _NO_CURRENT_RECONCILIATION_RE,
        CURRENT_RECONCILIATION_ISSUE,
        _tracker_bullets,
        _tracker_section,
        audit_tracker_errors,
    )

    audit = Path("docs/repository-truth-audit.md").read_text(encoding="utf-8")
    assert CURRENT_RECONCILIATION_ISSUE is None

    tracker = _tracker_section(audit)
    assert tracker is not None
    declaration = next(item for item in _tracker_bullets(tracker) if _NO_CURRENT_RECONCILIATION_RE.search(item))

    without = audit.replace(declaration, "", 1)
    assert without != audit, "fixture must actually drop the 'none' declaration"
    assert not _NO_CURRENT_RECONCILIATION_RE.search(_tracker_section(without) or "")
    assert any("no current reconciliation scope" in error for error in audit_tracker_errors(without)), (
        "an audit with no reconciliation scope must say so, not omit the topic"
    )


def test_audit_cannot_declare_none_while_another_row_claims_the_current_scope():
    from scripts.check_repo_consistency import CURRENT_RECONCILIATION_ISSUE, audit_tracker_errors

    audit = Path("docs/repository-truth-audit.md").read_text(encoding="utf-8")
    assert CURRENT_RECONCILIATION_ISSUE is None

    contradictory = audit.replace(
        "\n## Reconciliation lineage invariants",
        "\n- PR #26 — a later reconciliation: this is the current one.\n\n## Reconciliation lineage invariants",
        1,
    )
    assert contradictory != audit, "fixture must actually add a contradictory current-scope row"
    assert any("declares no current reconciliation scope" in error for error in audit_tracker_errors(contradictory))


def _real_audit_text() -> str:
    return Path("docs/repository-truth-audit.md").read_text(encoding="utf-8")


def test_reconciliation_model_rejects_a_completed_issue_as_the_current_scope(monkeypatch):
    """The invariant must not be maintained by reviving a closed issue."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "CURRENT_RECONCILIATION_ISSUE", 24)
    monkeypatch.setattr(guard, "COMPLETED_RECONCILIATION_ISSUES", (16, 20, 22, 24))

    errors = guard.reconciliation_model_errors()
    model_errors = [error for error in errors if error.startswith("CURRENT_RECONCILIATION_ISSUE")]
    assert model_errors, "pointing the current scope at a completed issue must be rejected outright"
    assert any("24" in error for error in model_errors), f"the offending issue number must be named: {model_errors}"
    assert any("None" in error for error in model_errors), "the error must point at the legal alternative"
    assert any("#24" in error for error in guard.audit_tracker_errors(_real_audit_text()))


def test_reconciliation_model_keeps_the_two_lineages_disjoint():
    import scripts.check_repo_consistency as guard

    assert guard.reconciliation_model_errors() == [], "the recorded model must be internally consistent"

    completed = set(guard.COMPLETED_RECONCILIATION_ISSUES)
    trackers = set(guard.OPEN_EXTERNAL_VALIDATION_TRACKERS)
    assert not completed & trackers, "an issue cannot be a completed reconciliation and an open tracker"
    assert guard.CURRENT_RECONCILIATION_ISSUE not in completed


def test_recorded_reconciliation_state_matches_the_audit_snapshot():
    """The recorded numbers are the ones the audit actually states."""
    from scripts.check_repo_consistency import (
        COMPLETED_RECONCILIATION_ISSUES,
        OPEN_EXTERNAL_VALIDATION_TRACKERS,
    )

    audit = Path("docs/repository-truth-audit.md").read_text(encoding="utf-8")
    for number in COMPLETED_RECONCILIATION_ISSUES:
        assert f"issues/{number})" in audit, f"completed issue #{number} must stay recorded in the tracker map"
    for number in OPEN_EXTERNAL_VALIDATION_TRACKERS:
        assert f"issues/{number})" in audit, f"open tracker #{number} must stay recorded in the tracker map"


def test_audit_tracker_requires_the_current_reconciliation_issue_to_be_recorded(monkeypatch):
    """While an issue really is open, the audit must record it as the current scope."""
    import scripts.check_repo_consistency as guard

    audit = Path("docs/repository-truth-audit.md").read_text(encoding="utf-8")
    assert guard.CURRENT_RECONCILIATION_ISSUE is None

    monkeypatch.setattr(guard, "CURRENT_RECONCILIATION_ISSUE", 34)
    monkeypatch.setattr(guard, "COMPLETED_RECONCILIATION_ISSUES", (16, 20, 22, 24))

    errors = guard.audit_tracker_errors(audit)
    assert any("#34" in error for error in errors), "an unrecorded current scope must fail the guard"
    # ...and the audit's 'none' declaration must then contradict it, not be ignored.
    assert any("declares no current reconciliation scope" in error for error in errors)


def test_reconciliation_lineage_is_not_pinned_to_a_pr_number():
    """A newer PR must not falsify the lineage: the invariant is anchored on issues."""
    from scripts.check_repo_consistency import audit_tracker_errors

    audit = Path("docs/repository-truth-audit.md").read_text(encoding="utf-8")

    for extra in (
        "- PR #26 — unrelated follow-up: open.\n",
        "- PR #137 — a much later reconciliation: merged.\n",
    ):
        tracker_marker = "\n## Reconciliation lineage invariants"
        assert tracker_marker in audit
        grown = audit.replace(tracker_marker, extra + tracker_marker, 1)
        assert grown != audit, "fixture must actually add the later PR row"
        assert audit_tracker_errors(grown) == [], f"adding {extra.strip()!r} must not break the lineage"


def test_completed_reconciliation_issues_must_stay_in_the_tracker_map():
    from scripts.check_repo_consistency import COMPLETED_RECONCILIATION_ISSUES, audit_tracker_errors

    audit = Path("docs/repository-truth-audit.md").read_text(encoding="utf-8")
    for number in COMPLETED_RECONCILIATION_ISSUES:
        assert f"issues/{number})" in audit, f"issue #{number} must stay recorded in the tracker map"

    dropped = audit.replace(
        "- [#22](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/22) — post-merge truth\n"
        "  reconciliation: completed, delivered by PR #23.\n",
        "",
    )
    assert dropped != audit, "fixture must actually remove the completed row"
    assert any("#22" in error for error in audit_tracker_errors(dropped))


def test_audit_tracker_bullets_stop_at_a_blank_line():
    """A trailing prose note must not be glued onto the last tracker bullet."""
    from scripts.check_repo_consistency import _tracker_bullets

    tracker = (
        "- [#8](x/issues/8) — umbrella: open.\n"
        "- [#12](x/issues/12) — runtime: open.\n"
        "\n"
        "Closing #8 likewise records nothing.\n"
    )
    bullets = _tracker_bullets(tracker)
    assert [bullet.strip() for bullet in bullets] == [
        "- [#8](x/issues/8) — umbrella: open.",
        "- [#12](x/issues/12) — runtime: open.",
    ]
    assert not any("Closing #8" in bullet for bullet in bullets)


def test_audit_tracker_requires_a_classification_on_delivered_areas():
    from scripts.check_repo_consistency import _CLASSIFICATION_TOKENS, DELIVERED_AUDIT_AREAS, audit_tracker_errors

    audit = Path("docs/repository-truth-audit.md").read_text(encoding="utf-8")
    for area in DELIVERED_AUDIT_AREAS:
        assert area in audit, f"delivered area {area!r} must keep its audit row"

    lines = audit.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("| OTLP export |"):
            stripped = line
            for token in _CLASSIFICATION_TOKENS:
                stripped = stripped.replace(token, "")
            assert stripped != line, "fixture must actually remove the classification"
            lines[index] = stripped
            break
    else:  # pragma: no cover - the row is required by REQUIRED_AUDIT_AREAS
        raise AssertionError("OTLP export row not found")

    assert audit_tracker_errors("".join(lines))


def test_truth_audit_parser_ignores_tables_outside_the_audit():
    """A later, narrower table in the audit is prose, not a malformed audit row."""
    import scripts.check_repo_consistency as guard

    audit = Path("docs/repository-truth-audit.md").read_text(encoding="utf-8")
    assert "|---|---|" in audit.split("## External validation tracker map")[0]
    errors: list[str] = []
    guard.check_truth_audit(errors)
    assert errors == []


def test_capability_rows_are_unique_in_current_evidence_map():
    """The evidence map must give each capability exactly one row."""
    from scripts.check_repo_consistency import check_capability_rows_are_unique

    errors: list[str] = []
    check_capability_rows_are_unique(errors)
    assert errors == []


def test_repeated_capability_row_is_flagged():
    """Two rows for one capability are the drift, even when they agree."""
    from scripts.check_repo_consistency import duplicate_capability_errors

    text = (
        "## Capability evidence\n"
        "\n"
        "| Capability | Level | Repository evidence | Upgrade path |\n"
        "|---|---|---|---|\n"
        "| Some exporter | `REPO_VERIFIED` | a.py | none |\n"
        "| Other thing | `PENDING` | b.py | a real run |\n"
        "| Some exporter | `PENDING` | c.py | a queried span |\n"
    )
    errors = duplicate_capability_errors("docs/x.md", text)
    assert len(errors) == 1
    assert "Some exporter" in errors[0]
    assert "more than one row" in errors[0]


def test_capability_names_are_compared_case_insensitively():
    from scripts.check_repo_consistency import duplicate_capability_errors

    text = (
        "## Capability evidence\n"
        "\n"
        "| Capability | Level | Repository evidence | Upgrade path |\n"
        "|---|---|---|---|\n"
        "| Redis session persistence | `LOCAL_REAL_VALIDATION` | a.py | none |\n"
        "| Redis Session Persistence | `PENDING` | b.py | a real run |\n"
    )
    assert duplicate_capability_errors("docs/x.md", text)


def test_duplicate_detection_ignores_tables_after_the_capability_table():
    """Later tables (e.g. historical business scale) are not capability rows."""
    from scripts.check_repo_consistency import duplicate_capability_errors

    text = (
        "## Capability evidence\n"
        "\n"
        "| Capability | Level | Repository evidence | Upgrade path |\n"
        "|---|---|---|---|\n"
        "| One thing | `REPO_VERIFIED` | a.py | none |\n"
        "\n"
        "## Business scale\n"
        "\n"
        "| Metric | Value | Level | Boundary |\n"
        "|---|---|---|---|\n"
        "| One thing | 3000+ | `HISTORICAL_PRODUCTION` | not in this repository |\n"
    )
    assert duplicate_capability_errors("docs/x.md", text) == []


def test_missing_capability_section_is_flagged(tmp_path, monkeypatch):
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    (tmp_path / guard.EVIDENCE_MAP).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / guard.EVIDENCE_MAP).write_text("# Evidence\n\nNo table here.\n", encoding="utf-8")

    errors: list[str] = []
    guard.check_capability_rows_are_unique(errors)
    assert any("Capability evidence" in error for error in errors)


# ── Markdown emphasis must not change what a negation means ─────────────────
#
# "The OTLP runtime closed loop is not verified." and "is **not** verified" are the
# same denial. The negation matcher required plain whitespace between the negation
# and its verb, so the emphasis delimiters broke the match and a truthful bolded
# denial was reported as an unevidenced closed loop. Emphasis is formatting, not
# meaning, and it must not become a way to phrase either a denial or a claim.


@pytest.mark.parametrize(
    "denial",
    [
        "The OTLP runtime closed loop is not verified.",
        "The OTLP runtime closed loop is **not** verified.",
        "The OTLP runtime closed loop is not **verified**.",
        "The OTLP runtime closed loop is **not** **verified**.",
        "The OTLP runtime closed loop is _not_ validated.",
        "The OTLP runtime closed loop is __not__ __validated__.",
    ],
)
def test_markdown_emphasis_does_not_hide_an_evidence_denial(denial):
    assert forbidden_evidence_claims(denial) == []


@pytest.mark.parametrize(
    "claim",
    [
        "The OTLP runtime closed loop is verified.",
        "The OTLP runtime closed loop is **verified**.",
        "The OTLP runtime closed loop is _verified_.",
        "The OTLP runtime closed loop is validated.",
        "Notably, the OTLP runtime closed loop is verified.",
    ],
)
def test_markdown_emphasis_cannot_launder_an_affirmative_claim(claim):
    """Emphasis is not an exemption: the affirmative claim is still a violation."""
    assert forbidden_evidence_claims(claim)


def test_denial_on_another_line_does_not_excuse_a_claim_on_this_line():
    """The negation window is per line; tolerating emphasis must not widen it."""
    text = "No exporter is configured by default.\nThe OTLP runtime closed loop is verified.\n"
    assert forbidden_evidence_claims(text) == ["OTLP runtime closed loop"]


# ── Some denials carry no space at all ──────────────────────────────────────
#
# _EMPHASIS_GAP requires at least one separator, which is exactly right between a
# negation and its complement ("not verified", "**not** verified") but wrong for the
# two forms that carry no separator by construction: the contraction "doesn't" and
# the `un` prefix in "unconfigured". Requiring a gap there turned a truthful denial
# into a reported claim and let a correct document fail the CI consistency check.
# The gap must therefore stay mandatory everywhere except these two junctions,
# where the zero-space form is the common spelling rather than the rare one.


@pytest.mark.parametrize(
    "denial",
    [
        "Real configured BGE does not count as validated.",
        "Real configured BGE does **not** count as validated.",
        "Real configured BGE doesn't count as validated.",
        "The OTel closed loop needs an exporter, which is not configured.",
        "The OTel closed loop needs an exporter, which is **not** configured.",
        "The OTel closed loop needs an exporter, which is unconfigured.",
    ],
)
def test_unspaced_negation_form_is_still_a_denial(denial):
    assert forbidden_evidence_claims(denial) == []


@pytest.mark.parametrize(
    "claim",
    [
        "Real configured BGE counts as validated.",
        "Real configured BGE **counts** as validated.",
        "The OTel closed loop is verified: the exporter is configured and running.",
        "The OTel closed loop is validated end to end.",
    ],
)
def test_unspaced_negation_fix_does_not_excuse_an_affirmative_claim(claim):
    """Negative control: only the denial is exempt, never the claim."""
    assert forbidden_evidence_claims(claim)


@pytest.mark.parametrize(
    "denial",
    [
        "The OTel exporter is unconfigured by default.",
        "Real configured BGE doesn't count as validated.",
    ],
)
def test_unspaced_denial_does_not_carry_to_another_line(denial):
    """A zero-gap denial is still scoped to its own line."""
    text = f"{denial}\nThe OTLP runtime closed loop is verified.\n"
    assert forbidden_evidence_claims(text) == ["OTLP runtime closed loop"]


# ── Capability rows may only use evidence levels the vocabulary defines ──────
#
# The classification vocabulary is the source of truth for which evidence levels
# exist. A capability row that invents a level, or drops a level the vocabulary has
# not caught up with, makes the document's own taxonomy contradict itself — the
# exact failure that let `HISTORICAL` ship undocumented in the Jaeger row. The set
# of legal levels is parsed out of the vocabulary table so adding a level is a
# documentation-only change, never a code change.


def _evidence_map(vocabulary_rows: str, capability_rows: str) -> str:
    """Build a minimal evidence map with a vocabulary and a capability table."""
    return (
        "# Evidence Map\n\n"
        "## Classification vocabulary\n\n"
        "| Level | Meaning | Safe framing | Must not say |\n"
        "|---|---|---|---|\n"
        f"{vocabulary_rows}"
        "\n"
        "## Capability evidence\n\n"
        "| Capability | Level | Repository evidence | Upgrade path |\n"
        "|---|---|---|---|\n"
        f"{capability_rows}"
        "\n"
        "## Known claim risks to avoid\n\n"
        "MAGIC_VERIFIED and `MAGIC_VERIFIED` appear in prose and in a later table, "
        "and neither is a capability row.\n"
        "\n"
        "| Metric | Value | Level | Boundary |\n"
        "|---|---|---|---|\n"
        "| Documents | 3000+ | `MAGIC_VERIFIED` | historical business context |\n"
    )


_TWO_LEVELS = "| `REPO_VERIFIED` | implemented | yes | no |\n| `PENDING` | pending | yes | no |\n"


def test_evidence_levels_are_defined_in_the_current_map():
    from scripts.check_repo_consistency import check_evidence_levels_are_defined

    errors: list[str] = []
    check_evidence_levels_are_defined(errors)
    assert errors == []


@pytest.mark.parametrize(
    ("capability_rows", "label"),
    [
        ("| Example | `REPO_VERIFIED` | x | y |\n", "single defined level"),
        ("| Example | `REPO_VERIFIED` (framework) / `PENDING` (result) | x | y |\n", "two defined levels"),
        ("| Example | `PENDING` (adapter contract `REPO_VERIFIED`) | x | y |\n", "level inside a qualifier"),
    ],
)
def test_capability_levels_drawn_from_the_vocabulary_pass(capability_rows, label):
    from scripts.check_repo_consistency import undefined_evidence_level_errors

    text = _evidence_map(_TWO_LEVELS, capability_rows)
    assert undefined_evidence_level_errors("docs/x.md", text) == [], label


def test_a_level_added_only_to_the_vocabulary_is_accepted():
    """A new level is a documentation change; the guard must not hardcode the list."""
    from scripts.check_repo_consistency import undefined_evidence_level_errors

    vocabulary = _TWO_LEVELS + "| `SOME_FUTURE_LEVEL` | defined later | yes | no |\n"
    text = _evidence_map(vocabulary, "| Example | `SOME_FUTURE_LEVEL` | x | y |\n")
    assert undefined_evidence_level_errors("docs/x.md", text) == []


def test_capability_row_using_an_undefined_level_is_flagged():
    from scripts.check_repo_consistency import undefined_evidence_level_errors

    text = _evidence_map(_TWO_LEVELS, "| Example | `MAGIC_VERIFIED` | x | y |\n")
    errors = undefined_evidence_level_errors("docs/x.md", text)
    assert len(errors) == 1
    # The message must name the level, the document and the table it came from.
    assert "MAGIC_VERIFIED" in errors[0]
    assert "docs/x.md" in errors[0]
    assert "Capability evidence" in errors[0]


def test_one_defined_level_does_not_excuse_an_undefined_one_in_the_same_cell():
    from scripts.check_repo_consistency import undefined_evidence_level_errors

    text = _evidence_map(_TWO_LEVELS, "| Example | `REPO_VERIFIED` / `MAGIC_VERIFIED` | x | y |\n")
    errors = undefined_evidence_level_errors("docs/x.md", text)
    # Exactly one error, so the defined level in the same cell was not flagged too.
    assert len(errors) == 1
    assert "MAGIC_VERIFIED" in errors[0]


def test_unparsable_vocabulary_fails_instead_of_skipping_validation():
    """Deleting the vocabulary table must not silently disable the guard."""
    from scripts.check_repo_consistency import undefined_evidence_level_errors

    text = "## Classification vocabulary\n\nNo table here.\n\n## Capability evidence\n\n" + (
        "| Capability | Level | Repository evidence | Upgrade path |\n"
        "|---|---|---|---|\n"
        "| Example | `MAGIC_VERIFIED` | x | y |\n"
    )
    errors = undefined_evidence_level_errors("docs/x.md", text)
    assert len(errors) == 1
    assert "vocabulary" in errors[0].lower()


def test_unparsable_capability_table_fails_instead_of_skipping_validation():
    from scripts.check_repo_consistency import undefined_evidence_level_errors

    text = _evidence_map(_TWO_LEVELS, "") + "\n"
    errors = undefined_evidence_level_errors("docs/x.md", text)
    assert len(errors) == 1
    assert "Capability evidence" in errors[0]


def test_levels_outside_the_capability_level_column_are_not_validated():
    """Prose and other tables are out of scope: only the main table's Level cell is."""
    from scripts.check_repo_consistency import undefined_evidence_level_errors

    text = _evidence_map(_TWO_LEVELS, "| Example | `REPO_VERIFIED` | uses `OTEL_EXPORT_ENABLED` | y |\n")
    assert undefined_evidence_level_errors("docs/x.md", text) == []


# ── one canonical evidence taxonomy for every current document ─────────────
#
# The evidence map owns the vocabulary. A second status vocabulary in another
# current document is what let `docs/repository-truth-audit.md` carry
# `VERIFIED`/`PARTIAL`/`STALE` beside the evidence map's own levels, so these
# tests pin both the one-way derivation of the legal levels and the cross-document
# guard that keeps a new vocabulary from reappearing.

_CANONICAL_LEVELS = {
    "HISTORICAL_PRODUCTION",
    "HISTORICAL",
    "REPO_VERIFIED",
    "LOCAL_REAL_VALIDATION",
    "DESIGN_TARGET",
    "PENDING",
}

#: The retired audit vocabulary, which must never classify anything again.
_RETIRED_STATUSES = ("VERIFIED", "PARTIAL", "PLANNED", "BROKEN", "STALE")

_CLASSIFYING_DOC = "# Doc\n\n| Area | Status |\n|---|---|\n| One | {status} |\n"


def test_canonical_levels_come_from_the_evidence_map_not_a_python_copy():
    from scripts.check_repo_consistency import _CLASSIFICATION_TOKENS, canonical_evidence_levels

    parsed = canonical_evidence_levels()
    assert parsed == _CANONICAL_LEVELS
    # The module-level set the guards actually use is derived, not hand-listed, so
    # adding a level stays a documentation-only edit.
    assert _CLASSIFICATION_TOKENS == parsed


def test_current_documents_use_only_canonical_evidence_levels():
    from scripts.check_repo_consistency import check_evidence_vocabulary_is_canonical

    errors: list[str] = []
    check_evidence_vocabulary_is_canonical(errors)
    assert errors == []


def test_current_documents_are_the_ones_guarded():
    from scripts.check_repo_consistency import EVIDENCE_VOCABULARY_DOCS

    assert "docs/repository-truth-audit.md" in EVIDENCE_VOCABULARY_DOCS
    assert "docs/evidence-map.md" in EVIDENCE_VOCABULARY_DOCS
    # Historical plans are not evidence and must not be dragged into the taxonomy.
    assert not any(name.startswith("docs/superpowers/") for name in EVIDENCE_VOCABULARY_DOCS)


@pytest.mark.parametrize("status", _RETIRED_STATUSES)
def test_a_retired_status_word_is_not_a_canonical_level(status):
    from scripts.check_repo_consistency import canonical_evidence_levels, evidence_classification_errors

    assert status not in canonical_evidence_levels()
    text = _CLASSIFYING_DOC.format(status=f"`{status}`")
    errors = evidence_classification_errors("docs/x.md", text, _CANONICAL_LEVELS)
    assert errors, f"{status} must not classify evidence any more"
    assert status in errors[0]


def test_an_invented_level_in_any_current_doc_is_flagged():
    from scripts.check_repo_consistency import evidence_classification_errors

    text = _CLASSIFYING_DOC.format(status="`RUNTIME_VERIFIED` (real Prometheus scrape)")
    errors = evidence_classification_errors("docs/x.md", text, _CANONICAL_LEVELS)
    assert len(errors) == 1
    # The message must name the offending token and where it may be declared.
    assert "RUNTIME_VERIFIED" in errors[0]
    assert "Run outcomes that are not evidence levels" in errors[0]


@pytest.mark.parametrize(
    "status",
    [
        "`REPO_VERIFIED`",
        "`REPO_VERIFIED` (framework) / `PENDING` (result)",
        "`REPO_VERIFIED` (implementation) / `PENDING` (real weights + evaluation)",
        "`DESIGN_TARGET` (PRD objectives) / `PENDING` (measured result)",
        "`REPO_VERIFIED` (document) / `DESIGN_TARGET` (objectives)",
    ],
)
def test_compound_canonical_levels_pass(status):
    from scripts.check_repo_consistency import evidence_classification_errors

    assert evidence_classification_errors("docs/x.md", _CLASSIFYING_DOC.format(status=status), _CANONICAL_LEVELS) == []


def test_a_classification_cell_must_name_a_canonical_level():
    """An opaque status in any language is a level nobody can audit."""
    from scripts.check_repo_consistency import evidence_classification_errors

    for status in ("基本可用（可选能力）", "Guarded by the consistency script", "n/a", ""):
        errors = evidence_classification_errors("docs/x.md", _CLASSIFYING_DOC.format(status=status), _CANONICAL_LEVELS)
        assert errors, f"{status!r} must not stand in for an evidence level"


def test_a_document_with_no_classification_column_is_flagged():
    """Renaming the column away is itself the drift: the guard must not be dodged."""
    from scripts.check_repo_consistency import evidence_classification_errors

    text = "# Doc\n\n| Area | Notes |\n|---|---|\n| One | `REPO_VERIFIED` |\n"
    errors = evidence_classification_errors("docs/x.md", text, _CANONICAL_LEVELS)
    assert len(errors) == 1
    assert "no table with an evidence classification column" in errors[0]


def test_a_chinese_classification_column_is_guarded_too():
    from scripts.check_repo_consistency import evidence_classification_errors

    good = "# Doc\n\n| 等级 | 含义 |\n|---|---|\n| `REPO_VERIFIED` | 主链路 |\n"
    assert evidence_classification_errors("docs/x.md", good, _CANONICAL_LEVELS) == []

    drifted = "# Doc\n\n| 等级 | 含义 |\n|---|---|\n| 基本可用 | 可选能力 |\n"
    errors = evidence_classification_errors("docs/x.md", drifted, _CANONICAL_LEVELS)
    assert len(errors) == 1
    assert "names no canonical evidence level" in errors[0]


def test_prose_and_non_classification_columns_are_not_validated():
    """Only classification cells are evidence levels; honest prose must survive."""
    from scripts.check_repo_consistency import evidence_classification_errors

    text = (
        "# Doc\n\n"
        "Status words in prose such as PARTIAL or a real `EXTERNAL_MODEL_ASSET_REQUIRED`\n"
        "reason are not classifications.\n\n"
        "| Area | Status | Evidence basis |\n"
        "|---|---|---|\n"
        "| One | `REPO_VERIFIED` | run status `PARTIAL` from `OTEL_EXPORT_ENABLED` |\n"
    )
    assert evidence_classification_errors("docs/x.md", text, _CANONICAL_LEVELS) == []


def test_run_outcomes_are_declared_and_are_not_evidence_levels():
    from scripts.check_repo_consistency import canonical_evidence_levels, run_outcome_tokens

    outcomes = run_outcome_tokens()
    assert {"EXECUTED", "PARTIAL", "BLOCKED", "PASS", "NOT RUN"} <= outcomes
    # Declaring a run outcome must not quietly widen the evidence vocabulary.
    assert outcomes & canonical_evidence_levels() == set()


def test_a_run_outcome_token_does_not_satisfy_a_classification_cell():
    """`PARTIAL` in an evidence cell is exactly the drift this reconciliation removed."""
    from scripts.check_repo_consistency import evidence_classification_errors, run_outcome_tokens

    assert "PARTIAL" in run_outcome_tokens()
    errors = evidence_classification_errors("docs/x.md", _CLASSIFYING_DOC.format(status="`PARTIAL`"), _CANONICAL_LEVELS)
    assert len(errors) == 1
    assert "PARTIAL" in errors[0]
    assert "REPO_VERIFIED" in errors[0]


def test_unreadable_vocabulary_fails_closed_instead_of_accepting_anything():
    from scripts.check_repo_consistency import evidence_classification_errors

    errors = evidence_classification_errors("docs/x.md", _CLASSIFYING_DOC.format(status="`REPO_VERIFIED`"), set())
    assert len(errors) == 1
    assert "missing or unparsable" in errors[0]


def test_current_truth_audit_status_column_uses_canonical_levels():
    """The retired vocabulary must not survive anywhere in the audit table."""
    from scripts.check_repo_consistency import canonical_evidence_levels

    audit = Path("docs/repository-truth-audit.md").read_text(encoding="utf-8")
    lines = audit.splitlines()
    header = next(index for index, line in enumerate(lines) if line.startswith("| Area |"))
    columns = [part.strip().lower() for part in lines[header].strip("|").split("|")]
    status_column = columns.index("status")

    checked = 0
    for line in lines[header + 1 :]:
        if not line.startswith("|"):
            if line.strip():
                break
            continue
        if "---" in line:
            continue
        cells = [part.strip() for part in line.strip("|").split("|")]
        checked += 1
        levels = re.findall(r"`([A-Z][A-Z0-9_]+)`", cells[status_column])
        assert levels, f"{cells[0]} has no canonical evidence level"
        assert set(levels) <= canonical_evidence_levels(), f"{cells[0]} uses a non-canonical level: {levels}"
    assert checked >= len(REQUIRED_AUDIT_AREAS)


def test_truth_audit_rejects_a_retired_status_word(tmp_path):
    audit = tmp_path / "audit.md"
    audit.write_text(
        "Reconciled candidate: `HEAD`\nPost-reconciliation verification date: 2026-10-02\n\n"
        "| Area | Claim | Status |\n|---|---|---|\n"
        "| Application | claim | `PARTIAL` |\n",
        encoding="utf-8",
    )
    errors: list[str] = []
    check_truth_audit(errors, audit)
    assert any("PARTIAL" in error for error in errors)


def test_truth_audit_rejects_a_status_that_names_no_level(tmp_path):
    audit = tmp_path / "audit.md"
    audit.write_text(
        "Reconciled candidate: `HEAD`\nPost-reconciliation verification date: 2026-10-02\n\n"
        "| Area | Claim | Status |\n|---|---|---|\n"
        "| Application | claim | Implemented and tested |\n",
        encoding="utf-8",
    )
    errors: list[str] = []
    check_truth_audit(errors, audit)
    assert any("names no canonical evidence level" in error for error in errors)


def test_truth_audit_rejects_an_existing_offline_capability_as_historical(tmp_path):
    audit = tmp_path / "audit.md"
    audit.write_text(
        "Reconciled candidate: `HEAD`\nPost-reconciliation verification date: 2026-10-02\n\n"
        "| Area | Claim | Status |\n|---|---|---|\n"
        "| Qdrant text | claim | `HISTORICAL` |\n",
        encoding="utf-8",
    )
    errors: list[str] = []
    check_truth_audit(errors, audit)
    assert any("Qdrant text" in error and "HISTORICAL" in error for error in errors)


def test_truth_audit_accepts_canonical_levels(tmp_path):
    audit = tmp_path / "audit.md"
    audit.write_text(
        "Reconciled candidate: `HEAD`\nPost-reconciliation verification date: 2026-10-02\n\n"
        "| Area | Claim | Status |\n|---|---|---|\n"
        "| Qdrant text | claim | `REPO_VERIFIED` (writer) / `PENDING` (real service) |\n",
        encoding="utf-8",
    )
    errors: list[str] = []
    check_truth_audit(errors, audit)
    assert not any("status" in error for error in errors)


# ── the docs index inventory must equal the canonical vocabulary ───────────

_DOCS_INDEX = (
    "# Index\n\n"
    "## Evidence vocabulary\n\n"
    "{bullets}\n\n"
    "Prose that names run outcomes such as `PARTIAL` and `NOT RUN` is not inventory.\n\n"
    "## Canonical / Current\n"
)


def test_docs_index_inventory_matches_the_canonical_vocabulary():
    from scripts.check_repo_consistency import check_docs_index_vocabulary

    errors: list[str] = []
    check_docs_index_vocabulary(errors)
    assert errors == []


def test_docs_index_inventory_must_list_every_level():
    from scripts.check_repo_consistency import docs_index_vocabulary_errors

    bullets = "".join(f"- `{level}` — meaning\n" for level in sorted(_CANONICAL_LEVELS - {"HISTORICAL"}))
    errors = docs_index_vocabulary_errors("docs/README.md", _DOCS_INDEX.format(bullets=bullets), _CANONICAL_LEVELS)
    assert len(errors) == 1
    assert "HISTORICAL" in errors[0]


def test_docs_index_inventory_must_not_invent_a_level():
    from scripts.check_repo_consistency import docs_index_vocabulary_errors

    bullets = "".join(f"- `{level}` — meaning\n" for level in sorted(_CANONICAL_LEVELS))
    bullets += "- `PARTIALLY_VERIFIED` — invented\n"
    errors = docs_index_vocabulary_errors("docs/README.md", _DOCS_INDEX.format(bullets=bullets), _CANONICAL_LEVELS)
    assert len(errors) == 1
    assert "PARTIALLY_VERIFIED" in errors[0]


def test_docs_index_inventory_ignores_run_outcome_prose():
    """The inventory is the bullet list; naming run outcomes in prose is allowed."""
    from scripts.check_repo_consistency import docs_index_vocabulary_errors

    bullets = "".join(f"- `{level}` — meaning\n" for level in sorted(_CANONICAL_LEVELS))
    assert docs_index_vocabulary_errors("docs/README.md", _DOCS_INDEX.format(bullets=bullets), _CANONICAL_LEVELS) == []


def test_docs_index_inventory_fails_closed_without_the_vocabulary():
    from scripts.check_repo_consistency import docs_index_vocabulary_errors

    errors = docs_index_vocabulary_errors("docs/README.md", _DOCS_INDEX.format(bullets=""), set())
    assert len(errors) == 1
    assert "missing or unparsable" in errors[0]


def test_docs_index_without_a_vocabulary_section_is_flagged():
    from scripts.check_repo_consistency import docs_index_vocabulary_errors

    errors = docs_index_vocabulary_errors("docs/README.md", "# Index\n\n## Canonical / Current\n", _CANONICAL_LEVELS)
    assert len(errors) == 1
    assert "Evidence vocabulary" in errors[0]


# ── the retired Jaeger thrift-agent config must not come back ──────────────
#
# The guard targets the agent *configuration*, never the word "jaeger". The
# optional Jaeger backend in docker-compose.observability.yml is a current part of
# the OTLP path, so a guard that matched the bare token would delete a working
# capability along with the dead config. Both directions are pinned here.


def test_legacy_jaeger_agent_config_is_absent_from_the_current_repo():
    from scripts.check_repo_consistency import check_legacy_jaeger_agent_config_is_absent

    errors: list[str] = []
    check_legacy_jaeger_agent_config_is_absent(errors)
    assert errors == []


def test_config_json_no_longer_declares_a_jaeger_block():
    import json

    config = json.loads((Path("config.json")).read_text(encoding="utf-8"))
    assert "monitoring" not in config, (
        "config.json must not keep an empty monitoring object after the jaeger block was removed"
    )


@pytest.mark.parametrize(
    ("name", "text"),
    [
        (".env.example", "JAEGER_AGENT_HOST=localhost\nJAEGER_AGENT_PORT=6831\n"),
        (".env.example", "# 旧的 Jaeger thrift agent 配置\nJAEGER_AGENT_HOST=localhost\n"),
        ("config.json", '{\n  "monitoring": {\n    "jaeger": {\n      "agent_host": "localhost"\n    }\n  }\n}\n'),
        (
            "config.json",
            '{\n  "monitoring": {\n    "jaeger": {\n      "enabled": false,\n      "agent_port": 6831\n    }\n  }\n}\n',
        ),
        ("docs/x.md", "The legacy Jaeger thrift agent path was `config.json` -> `monitoring.jaeger`.\n"),
        ("docs/x.md", "Set `agent_host` and `agent_port` to reach the collector.\n"),
        ("docs/x.md", "Install `opentelemetry-exporter-jaeger` to enable export.\n"),
    ],
)
def test_legacy_jaeger_agent_config_is_rejected(name, text):
    from scripts.check_repo_consistency import legacy_jaeger_agent_errors

    errors = legacy_jaeger_agent_errors(name, text)
    assert errors, f"{name} must be rejected for reintroducing the agent config"
    assert all(error.startswith(f"{name}:") for error in errors)
    assert all("Jaeger thrift-agent" in error for error in errors)
    # One error per offending line, and no line reported twice.
    reported = [error.split(":")[1] for error in errors]
    assert len(reported) == len(set(reported))


@pytest.mark.parametrize(
    ("name", "text"),
    [
        # The working OTLP backend: same product, different transport. Must survive.
        ("docker-compose.observability.yml", "services:\n  jaeger:\n    image: jaegertracing/all-in-one\n"),
        ("docker-compose.observability.yml", "    ports:\n      - '4317:4317'\n      - '4318:4318'\n"),
        (".env.example", "OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318\n"),
        (".env.example", "OTEL_EXPORT_ENABLED=false\nOTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf\n"),
        ("docs/operations-guide.md", "Start the overlay to get the Jaeger UI behind OTLP on 4318.\n"),
        ("docs/operations-guide.md", "Spans reach the Jaeger backend over OTLP; the closed loop is PENDING.\n"),
        ("docs/x.md", "Prometheus, Jaeger and Grafana are optional overlay services.\n"),
    ],
)
def test_current_jaeger_otlp_backend_is_not_flagged(name, text):
    from scripts.check_repo_consistency import legacy_jaeger_agent_errors

    assert legacy_jaeger_agent_errors(name, text) == [], name


def test_observability_overlay_keeps_the_jaeger_backend():
    """A cleanup aimed at the dead config must not remove the working backend."""
    import yaml

    overlay = yaml.safe_load(Path("docker-compose.observability.yml").read_text(encoding="utf-8"))
    assert "jaeger" in overlay.get("services", {})


# ── an evidence directory is not an evidence artifact ──────────────────────
#
# `monitoring/evidence/` is where a real OTLP closed-loop run would be recorded.
# Its mere presence says nothing: an empty directory, a stray scratch file or a
# truncated/empty JSON file must not read as "a closed loop was recorded", because
# that predicate gates whether the repository may claim the runtime closed loop at
# all. Only a committed, readable, non-empty JSON object directly inside the
# directory counts. Discovery is deliberately non-recursive: a hand-made
# monitoring/evidence/tmp/debug/foo.json must not silently change top-level state.


def test_otel_runtime_evidence_is_false_without_the_directory(tmp_path, monkeypatch):
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    assert not (tmp_path / "monitoring" / "evidence").exists()
    assert guard.otel_runtime_evidence_exists() is False


#: A complete, contract-shaped artifact reused by the discovery tests below.
_CONTRACT_JSON = json.dumps(
    {
        "schema_version": 1,
        "evidence_type": "otel_closed_loop",
        "status": "EXECUTED",
        "backend": "jaeger",
        "trace_id": "0123456789abcdef0123456789abcdef",
        "queried_span_count": 1,
    }
)


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([], False),
        ([("run-1/", None)], False),
        ([("run.json", "")], False),
        ([("run.json", "{broken")], False),
        ([("run.json", "{}")], False),
        ([("README.md", "notes"), ("note.txt", "notes")], False),
        ([("otel-run.json", _CONTRACT_JSON)], True),
        ([("bad.json", "{oops"), ("ok.json", _CONTRACT_JSON)], True),
    ],
    ids=[
        "empty-directory",
        "only-a-subdirectory",
        "zero-byte-json",
        "invalid-json",
        "empty-json-object",
        "unrelated-files-only",
        "valid-non-empty-object",
        "invalid-plus-valid",
    ],
)
def test_otel_runtime_evidence_requires_a_readable_non_empty_json_object(tmp_path, monkeypatch, entries, expected):
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    evidence = tmp_path / "monitoring" / "evidence"
    evidence.mkdir(parents=True)
    for name, content in entries:
        path = evidence / name
        if content is None:  # a directory, not a file
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.write_text(content, encoding="utf-8")
    assert guard.otel_runtime_evidence_exists() is expected


def test_otel_runtime_evidence_is_false_for_this_repository():
    """This repository has recorded no closed-loop run, so it must stay PENDING."""
    from scripts.check_repo_consistency import otel_runtime_evidence_exists

    assert otel_runtime_evidence_exists() is False


# ── minimum structural contract for an OTLP queried-span artifact ───────────
#
# A readable non-empty JSON object is still not evidence of anything: {"x": 1}
# satisfies that bar and used to be accepted as a recorded closed loop. The
# candidate must additionally claim, in a machine-checkable way, that it is an
# OTLP closed-loop artifact, that the run completed, which backend answered,
# which trace it belongs to, and that the query returned at least one span.
#
# This is a structural floor, not provenance. It cannot show the file was not
# hand-written, that the backend was really reached, or that the trace
# corresponds to a real request; it only stops arbitrary JSON from promoting the
# closed loop. Extra fields are allowed so the contract can grow without
# invalidating artifacts written today.

_OTEL_EVIDENCE_CONTRACT = {
    "schema_version": 1,
    "evidence_type": "otel_closed_loop",
    "status": "EXECUTED",
    "backend": "jaeger",
    "trace_id": "0123456789abcdef0123456789abcdef",
    "queried_span_count": 1,
}

_TRACE_ID = "0123456789abcdef0123456789abcdef"


def _otel_payload(**overrides) -> dict:
    payload = dict(_OTEL_EVIDENCE_CONTRACT)
    payload.update(overrides)
    return {k: v for k, v in payload.items() if v is not _OMIT}


class _Omit:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<omit>"


_OMIT = _Omit()


def _write_evidence(tmp_path, monkeypatch, payloads):
    """Point the guard at a temp ROOT holding the given artifacts."""
    import scripts.check_repo_consistency as guard

    monkeypatch.setattr(guard, "ROOT", tmp_path)
    evidence = tmp_path / "monitoring" / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        (evidence / name).write_text(
            payload if isinstance(payload, str) else __import__("json").dumps(payload),
            encoding="utf-8",
        )
    return guard


def test_arbitrary_json_object_is_not_otel_evidence(tmp_path, monkeypatch):
    """The core regression: any non-empty object used to count as evidence."""
    guard = _write_evidence(tmp_path, monkeypatch, {"a.json": {"x": 1}})
    assert guard.otel_runtime_evidence_exists() is False


def test_minimal_valid_contract_is_accepted(tmp_path, monkeypatch):
    guard = _write_evidence(tmp_path, monkeypatch, {"run.json": dict(_OTEL_EVIDENCE_CONTRACT)})
    assert guard.otel_runtime_evidence_exists() is True


def test_structural_contract_predicate_directly():
    from scripts.check_repo_consistency import is_valid_otel_runtime_evidence as valid

    assert valid(_OTEL_EVIDENCE_CONTRACT) is True
    assert valid({"x": 1}) is False
    assert valid({}) is False
    assert valid(None) is False
    assert valid("string") is False
    assert valid([_OTEL_EVIDENCE_CONTRACT]) is False


@pytest.mark.parametrize("field", sorted(_OTEL_EVIDENCE_CONTRACT))
def test_every_required_field_is_required(field):
    from scripts.check_repo_consistency import is_valid_otel_runtime_evidence as valid

    payload = dict(_OTEL_EVIDENCE_CONTRACT)
    del payload[field]
    assert valid(payload) is False, f"missing {field} must not validate"


@pytest.mark.parametrize("value", [0, 2, True, "1", 1.0, None])
def test_schema_version_must_be_integer_one(value):
    from scripts.check_repo_consistency import is_valid_otel_runtime_evidence as valid

    assert valid(_otel_payload(schema_version=value)) is False


@pytest.mark.parametrize(
    "value",
    ["otel", "tracing", "performance", "benchmark", "otel_closed_loop ", "OTEL_CLOSED_LOOP", ""],
)
def test_evidence_type_must_be_exact(value):
    from scripts.check_repo_consistency import is_valid_otel_runtime_evidence as valid

    assert valid(_otel_payload(evidence_type=value)) is False


@pytest.mark.parametrize("value", ["PENDING", "PARTIAL", "BLOCKED", "FAILED", "executed", "", None])
def test_status_must_be_executed(value):
    from scripts.check_repo_consistency import is_valid_otel_runtime_evidence as valid

    assert valid(_otel_payload(status=value)) is False


@pytest.mark.parametrize(
    "value",
    ["jaeger", "tempo", "other-otel-backend", "OTLP-compatible/1.0", "x"],
)
def test_backend_accepts_any_non_empty_string(value):
    from scripts.check_repo_consistency import is_valid_otel_runtime_evidence as valid

    assert valid(_otel_payload(backend=value)) is True


@pytest.mark.parametrize("value", ["", "   ", "\t", None, 1, [], {}])
def test_backend_rejects_blank_and_non_string(value):
    from scripts.check_repo_consistency import is_valid_otel_runtime_evidence as valid

    assert valid(_otel_payload(backend=value)) is False


@pytest.mark.parametrize(
    "value",
    [
        "abc",
        "0123456789abcdef0123456789abcde",  # 31 hex
        "0123456789abcdef0123456789abcdef0",  # 33 hex
        "0123456789abcdef0123456789abcdeg",  # 32 chars, non-hex
        "",
        " 0123456789abcdef0123456789abcdef",
        None,
        1,
        "0123456789ABCDEF0123456789ABCDEF".replace("ABCDEF", "ABCDEG"),
    ],
)
def test_invalid_trace_id_is_rejected(value):
    from scripts.check_repo_consistency import is_valid_otel_runtime_evidence as valid

    assert valid(_otel_payload(trace_id=value)) is False


def test_uppercase_hex_trace_id_is_accepted():
    from scripts.check_repo_consistency import is_valid_otel_runtime_evidence as valid

    assert valid(_otel_payload(trace_id="0123456789ABCDEF0123456789ABCDEF")) is True


@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, "1", None, [1]])
def test_queried_span_count_must_be_a_positive_int(value):
    from scripts.check_repo_consistency import is_valid_otel_runtime_evidence as valid

    assert valid(_otel_payload(queried_span_count=value)) is False


@pytest.mark.parametrize("value", [1, 2, 999])
def test_queried_span_count_accepts_positive_ints(value):
    from scripts.check_repo_consistency import is_valid_otel_runtime_evidence as valid

    assert valid(_otel_payload(queried_span_count=value)) is True


def test_extra_fields_are_allowed():
    from scripts.check_repo_consistency import is_valid_otel_runtime_evidence as valid

    payload = dict(_OTEL_EVIDENCE_CONTRACT, request_id="abc", notes="local validation", extra=[1, 2])
    assert valid(payload) is True


def test_invalid_artifact_does_not_block_a_valid_one(tmp_path, monkeypatch):
    guard = _write_evidence(
        tmp_path,
        monkeypatch,
        {"bad.json": {"x": 1}, "good.json": dict(_OTEL_EVIDENCE_CONTRACT)},
    )
    assert guard.otel_runtime_evidence_exists() is True


def test_all_invalid_artifacts_stay_false(tmp_path, monkeypatch):
    guard = _write_evidence(
        tmp_path,
        monkeypatch,
        {"a.json": {"x": 1}, "b.json": _otel_payload(status="BLOCKED"), "c.json": "not json at all"},
    )
    assert guard.otel_runtime_evidence_exists() is False


# ── Qdrant evidence reconciliation ───────────────────────────────────────────
#
# Qdrant has two independent evidence states that the repository must be able to
# hold at once: current reproducible coverage is the in-process `QdrantClient`,
# while the PR #6/#7 development record contains a real local Qdrant
# service/container execution. Neither "only in memory ever happened" nor "a real
# service is currently verified" may be asserted, and the two must never be held
# together.

_QDRANT_ONLY_IN_MEMORY_CLAIMS = [
    "Qdrant has only ever been tested in memory.",
    "Only in-memory Qdrant was ever exercised.",
    "Qdrant was never run against a real service.",
    "The offline writers were never run against a real Qdrant service.",
    "Qdrant 从未被真实服务验证过。",
]

_QDRANT_CURRENTLY_VERIFIED_CLAIMS = [
    "The real Qdrant service was validated and verified end to end.",
    "Qdrant is currently verified against a live container.",
    "Qdrant is `LOCAL_REAL_VALIDATION` for the current tree.",
    "本地真实 Qdrant 服务已验证通过。",
]

_QDRANT_HONEST_WORDING = [
    # Current coverage, honestly scoped.
    "Current deterministic coverage is the in-memory `QdrantClient`; no checked-in test targets a real Qdrant service.",
    "The suite never connects to a real Qdrant service.",
    # The required prohibition framing.
    'Do not say "Qdrant has only ever been tested in memory." The historical run happened.',
    # Lineage, which is neither a current result nor an erasure.
    "Historically, PR #6/#7 ran the writers against a real local Qdrant service/container.",
    # Absent result.
    "The Qdrant service run carries no committed artifact, so it is not current evidence.",
    "Upgrading the current evidence requires a new real Qdrant service run with a committed artifact.",
]


def test_qdrant_evidence_reconciliation_passes_for_current_docs():
    errors: list[str] = []
    check_qdrant_evidence_reconciliation(errors)
    assert errors == []


@pytest.mark.parametrize("claim", _QDRANT_ONLY_IN_MEMORY_CLAIMS)
def test_only_in_memory_ever_claim_is_flagged(claim):
    from scripts.check_repo_consistency import qdrant_only_ever_claims

    assert qdrant_only_ever_claims(claim)


@pytest.mark.parametrize("claim", _QDRANT_CURRENTLY_VERIFIED_CLAIMS)
def test_currently_verified_claim_is_flagged_without_an_artifact(claim):
    from scripts.check_repo_consistency import qdrant_evidence_claim_errors

    assert qdrant_evidence_claim_errors("doc.md", claim, artifact_exists=False)
    # A committed real-service run is what would have to license the claim.
    assert qdrant_evidence_claim_errors("doc.md", claim, artifact_exists=True) == []


@pytest.mark.parametrize("text", _QDRANT_HONEST_WORDING)
def test_two_state_wording_is_allowed(text):
    from scripts.check_repo_consistency import (
        qdrant_currently_verified_claims,
        qdrant_only_ever_claims,
    )

    assert qdrant_only_ever_claims(text) == []
    assert qdrant_currently_verified_claims(text) == []


def test_a_denial_without_qdrant_is_not_matched():
    from scripts.check_repo_consistency import qdrant_only_ever_claims

    assert qdrant_only_ever_claims("Redis was never validated in production and QPS is unmeasured.") == []


def test_holding_both_contradictory_qdrant_claims_is_rejected():
    from scripts.check_repo_consistency import qdrant_contradiction_errors, qdrant_doc_claims

    denials, verified = qdrant_doc_claims(
        [
            ("README.md", "Qdrant has only ever been tested in memory."),
            ("docs/repository-truth-audit.md", "The real Qdrant service was validated and verified."),
        ]
    )
    assert denials and verified
    errors = qdrant_contradiction_errors(denials, verified)
    assert errors
    assert "self-contradictory" in errors[0]


def test_one_direction_alone_is_not_a_contradiction():
    from scripts.check_repo_consistency import qdrant_contradiction_errors, qdrant_doc_claims

    denials, verified = qdrant_doc_claims([("README.md", "Qdrant has only ever been tested in memory.")])
    assert verified == []
    assert qdrant_contradiction_errors(denials, verified) == []


def test_the_denial_direction_is_still_rejected_on_its_own():
    from scripts.check_repo_consistency import qdrant_contradiction_errors, qdrant_doc_claims

    denials, _ = qdrant_doc_claims([("README.md", "Qdrant has only ever been tested in memory.")])
    assert qdrant_contradiction_errors(denials, []) == []
    from scripts.check_repo_consistency import qdrant_evidence_claim_errors

    assert qdrant_evidence_claim_errors("README.md", "Qdrant has only ever been tested in memory.", False)


def test_qdrant_runtime_artifact_is_committed():
    # VAL-STORE-001 committed a real single-host Qdrant run; the guard derives
    # the affirmative wording from this artifact being on disk.
    from scripts.check_repo_consistency import qdrant_runtime_artifact_exists

    assert qdrant_runtime_artifact_exists() is True


def test_a_qdrant_runtime_artifact_is_derived_from_disk(tmp_path):
    from scripts.check_repo_consistency import qdrant_runtime_artifact_exists

    run_dir = tmp_path / "artifacts" / "qdrant" / "2026-10-02-local"
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text("{}", encoding="utf-8")
    assert qdrant_runtime_artifact_exists(tmp_path) is True


def test_lineage_anchor_requires_the_historical_run():
    from scripts.check_repo_consistency import qdrant_lineage_errors

    errors = qdrant_lineage_errors(
        "docs/repository-truth-audit.md",
        "Current Qdrant coverage is the in-memory client and no artifact is committed.",
        artifact_exists=False,
    )
    assert any("PR #6/#7" in error for error in errors)


def test_lineage_anchor_requires_the_artifact_gap_and_upgrade_path():
    from scripts.check_repo_consistency import qdrant_lineage_errors

    text = "PR #6/#7 ran the writers against a real local Qdrant service/container with Elasticsearch."
    errors = qdrant_lineage_errors("docs/repository-truth-audit.md", text, artifact_exists=False)
    assert any("new real-service run" in error for error in errors)
    assert qdrant_lineage_errors("docs/repository-truth-audit.md", text, artifact_exists=True) == []


def test_current_evidence_documents_satisfy_the_lineage_anchor():
    from scripts.check_repo_consistency import ROOT, qdrant_lineage_errors

    # The committed artifact (VAL-STORE-001) is the live state, so the lineage
    # docs are checked with artifact_exists=True.
    for name in ("docs/evidence-map.md", "docs/repository-truth-audit.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert qdrant_lineage_errors(name, text, artifact_exists=True) == []


def test_restated_question_count_is_flagged():
    from scripts.check_repo_consistency import enumerated_count_errors

    text = (
        "## 2-minute Review Guide\n\n"
        "给技术评审的六个问题与本仓库可支撑的答案。\n\n"
        "**Q1 · a?**\n**Q2 · b?**\n**Q3 · c?**\n"
        "**Q4 · d?**\n**Q5 · e?**\n**Q6 · f?**\n**Q7 · g?**\n"
    )
    errors = enumerated_count_errors("README.md", text)
    assert len(errors) == 1
    # The message names the section, the offending phrase and the real item count.
    assert "2-minute Review Guide" in errors[0]
    assert "六个问题" in errors[0]
    assert "7 items" in errors[0]


def test_deleting_the_count_clears_the_error():
    from scripts.check_repo_consistency import enumerated_count_errors

    text = (
        "## 2-minute Review Guide\n\n"
        "给技术评审的问题清单与本仓库可支撑的答案。\n\n"
        "**Q1 · a?**\n**Q2 · b?**\n**Q3 · c?**\n"
    )
    assert enumerated_count_errors("README.md", text) == []


def test_english_count_restatement_is_also_flagged():
    from scripts.check_repo_consistency import enumerated_count_errors

    text = "## Guide\n\nSix questions for the reviewer.\n\n**Q1 · a?**\n**Q2 · b?**\n"
    assert enumerated_count_errors("README.md", text)
    assert enumerated_count_errors("README.md", "## Guide\n\n**Q1 · a?**\n**Q2 · b?**\n") == []


def test_spurious_numbered_reference_is_not_an_enumerated_item():
    from scripts.check_repo_consistency import enumerated_count_errors

    # "Q12" in prose and a "## Q12 ..." heading are references, not list items, so
    # the section carries no enumeration and the count rule never inspects it.
    text = "## Q12 标准回答\n\nThis answers the twelve questions asked.\n\n## Other\n\nNo list here.\n"
    assert enumerated_count_errors("docs/architecture-baseline.md", text) == []


def test_prose_mentioning_a_count_without_an_enumeration_is_not_flagged():
    from scripts.check_repo_consistency import enumerated_count_errors

    text = "## Design\n\nWe collected 12 个问题 from users and fixed 3 of them.\n"
    assert enumerated_count_errors("PRD.md", text) == []


def test_skipped_or_duplicated_marker_is_flagged():
    from scripts.check_repo_consistency import enumerated_count_errors

    skipped = "## G\n\n**Q1 · a?**\n**Q2 · b?**\n**Q4 · d?**\n"
    assert "contiguous" in enumerated_count_errors("README.md", skipped)[0]
    duplicated = "## G\n\n**Q1 · a?**\n**Q2 · b?**\n**Q2 · b again?**\n"
    assert "contiguous" in enumerated_count_errors("README.md", duplicated)[0]


def test_enumeration_before_the_first_level_two_heading_is_still_checked():
    from scripts.check_repo_consistency import enumerated_count_errors

    text = "给技术评审的六个问题。\n\n**Q1 · a?**\n**Q2 · b?**\n\n## Later\n\nNo items.\n"
    assert enumerated_count_errors("README.md", text)


def test_current_documents_do_not_restate_their_own_enumeration_counts():
    from scripts.check_repo_consistency import ROOT, enumerated_count_errors

    for name in ("README.md", "PRD.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert enumerated_count_errors(name, text) == []


def test_guard_fails_when_the_question_count_drifts(tmp_path, monkeypatch):
    from scripts.check_repo_consistency import check_enumerated_section_counts

    readme = tmp_path / "README.md"
    readme.write_text(
        "## 2-minute Review Guide\n\n"
        "给技术评审的六个问题与本仓库可支撑的答案。\n\n"
        "**Q1 · a?**\n**Q2 · b?**\n**Q3 · c?**\n"
        "**Q4 · d?**\n**Q5 · e?**\n**Q6 · f?**\n**Q7 · g?**\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "scripts.check_repo_consistency.CANONICAL_DOCS",
        [readme],
    )
    monkeypatch.setattr("scripts.check_repo_consistency.ROOT", tmp_path)
    errors: list[str] = []
    check_enumerated_section_counts(errors)
    assert len(errors) == 1
    assert "六个问题" in errors[0]


def test_bold_cross_reference_is_not_an_enumerated_item():
    from scripts.check_repo_consistency import enumerated_count_errors

    # A reference to an item is prose, not a second item. Reading "**Q7**" as a
    # marker made the section enumerate [1, 7] and reported a broken sequence,
    # so ordinary documentation wording could fail the consistency check.
    text = "## G\n\n**Q1 · a?**\n**Q2 · b?**\n\nSee **Q7** in the architecture note.\n"
    assert enumerated_count_errors("README.md", text) == []
    bare = "## G\n\n**Q1 · a?**\n**Q2 · b?**\n\n**Q7**\n"
    assert enumerated_count_errors("README.md", bare) == []


def test_english_count_beyond_ten_is_flagged():
    from scripts.check_repo_consistency import enumerated_count_errors

    # The marker syntax accepts any Q<n>, so a list that outgrew "ten" must not
    # outgrow the prose rule: "Twelve questions" over Q1..Q12 is the same drift.
    items = "".join(f"**Q{n} · q?**\n" for n in range(1, 13))
    errors = enumerated_count_errors("README.md", f"## G\n\nTwelve questions follow.\n\n{items}")
    assert len(errors) == 1
    assert "Twelve questions" in errors[0]
    assert "12 items" in errors[0]
    assert enumerated_count_errors("README.md", f"## G\n\n{items}") == []


def test_compound_english_count_is_flagged():
    from scripts.check_repo_consistency import enumerated_count_errors

    text = "## G\n\nThe guide answers twenty-one questions.\n\n**Q1 · a?**\n**Q2 · b?**\n"
    assert "twenty-one questions" in enumerated_count_errors("README.md", text)[0]


# ── the SLO objective count is derived, never restated ──────────────────
def test_runbook_objective_count_comes_from_the_objective_rows():
    from scripts.check_repo_consistency import slo_objective_count

    runbook = (
        "| # | Objective | Definition | Target | Class |\n"
        "|---|---|---|---|---|\n"
        "| SLO-1 | Availability | ... | 99.5% | `DESIGN_TARGET` |\n"
        "| SLO-2 | Error rate | ... | < 1% | `DESIGN_TARGET` |\n"
        "| SLO-3 | Latency | ... | 2000 ms | `DESIGN_TARGET` |\n"
    )
    assert slo_objective_count(runbook) == 3


def test_a_prose_reference_to_an_objective_is_not_another_objective():
    """`SLO-3` mentioned in a sentence must not be counted as a fourth row."""
    from scripts.check_repo_consistency import slo_objective_count

    runbook = (
        "| SLO-1 | a | x | y | `DESIGN_TARGET` |\n"
        "| SLO-2 | b | x | y | `DESIGN_TARGET` |\n"
        "\nSLO-1 and SLO-2 are the only objectives; see also SLO-99 for the draft.\n"
    )
    assert slo_objective_count(runbook) == 2


@pytest.mark.parametrize(
    "claim",
    [
        "5 个 SLO 目标与告警阈值都是 `DESIGN_TARGET`",
        "| SLO 目标（5 个） | `REPO_VERIFIED`（文档） |",
        "5 个 SLO 目标（均为 `DESIGN_TARGET`）+ 8 个处置流程",
        "There are five SLO objectives.",
    ],
)
def test_every_phrasing_the_repository_uses_is_understood(claim):
    """Each accepted phrasing must parse to 5, or the guard cannot verify it.

    A guard that reports "cannot verify" on the project's own wording is worse
    than no guard: it turns a documentation truth into a CI failure nobody can
    fix without editing the checker.
    """
    from scripts.check_repo_consistency import slo_count_errors

    assert slo_count_errors("README.md", claim, 5) == []


@pytest.mark.parametrize(
    ("claim", "stated"),
    [
        ("十个 SLO 目标与告警阈值", 10),
        ("| SLO 目标（10 个） | x |", 10),
        ("There are twelve SLO objectives.", 12),
    ],
)
def test_a_count_that_contradicts_the_runbook_is_flagged(claim, stated):
    """The regression: the README claimed ten objectives next to two correct
    fives, inside a section presented as repository-reproducible evidence."""
    from scripts.check_repo_consistency import slo_count_errors

    errors = slo_count_errors("README.md", claim, expected=5)
    assert len(errors) == 1
    assert f"states {stated} SLO objectives" in errors[0]
    assert "defines 5" in errors[0]


def test_adding_an_objective_to_the_runbook_does_not_silently_stale_the_summary():
    """The guard's whole reason to exist: the count is derived, so growing the
    runbook turns the summary red instead of leaving it quietly wrong."""
    from scripts.check_repo_consistency import slo_count_errors

    summary = "5 个 SLO 目标与告警阈值都是 `DESIGN_TARGET`"
    assert slo_count_errors("README.md", summary, expected=5) == []
    assert slo_count_errors("README.md", summary, expected=6)


def test_current_documents_state_the_objective_count_the_runbook_defines():
    from scripts.check_repo_consistency import ROOT, slo_count_errors, slo_objective_count

    runbook = (ROOT / "docs" / "slo-runbook.md").read_text(encoding="utf-8")
    expected = slo_objective_count(runbook)
    assert expected > 0, "docs/slo-runbook.md must define at least one SLO objective"
    for name in ("README.md", "PRD.md"):
        assert slo_count_errors(name, (ROOT / name).read_text(encoding="utf-8"), expected) == []


def test_guard_reports_a_missing_runbook_objective_table(tmp_path, monkeypatch):
    """Silently skipping a runbook with no objective rows would turn the guard
    into a no-op the next time the runbook is restructured."""
    from scripts.check_repo_consistency import check_slo_objective_counts

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "slo-runbook.md").write_text("# Runbook\n\nNo table here.\n", encoding="utf-8")
    monkeypatch.setattr("scripts.check_repo_consistency.ROOT", tmp_path)

    errors: list[str] = []
    check_slo_objective_counts(errors, root=tmp_path)
    assert len(errors) == 1
    assert "no `| SLO-<n> |` objective rows" in errors[0]


def test_guard_fails_end_to_end_when_the_summary_count_drifts(tmp_path, monkeypatch):
    """Wiring check: `main()` must actually call the guard, or the pure
    function above is decoration."""
    from scripts.check_repo_consistency import check_slo_objective_counts

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "slo-runbook.md").write_text(
        "| SLO-1 | a | x | y | `DESIGN_TARGET` |\n| SLO-2 | b | x | y | `DESIGN_TARGET` |\n",
        encoding="utf-8",
    )
    readme = tmp_path / "README.md"
    readme.write_text("5 个 SLO 目标与告警阈值。\n", encoding="utf-8")
    monkeypatch.setattr("scripts.check_repo_consistency.ROOT", tmp_path)
    monkeypatch.setattr("scripts.check_repo_consistency.CANONICAL_DOCS", [readme])

    errors: list[str] = []
    check_slo_objective_counts(errors, root=tmp_path)
    assert len(errors) == 1
    assert "states 5 SLO objectives but docs/slo-runbook.md defines 2" in errors[0]


# ── the English count parser, exercised directly ────────────────────────
@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("0", 0),
        ("5", 5),
        ("ten", 10),
        ("nineteen", 19),
        ("twenty", 20),
        # Tens are values, not positions: "thirty" is the 22nd word in the list
        # but the number 30. An index here silently reported 21.
        ("thirty", 30),
        ("forty", 40),
        ("ninety", 90),
        ("hundred", 100),
        ("thousand", 1000),
        # Compounds add ...
        ("twenty-one", 21),
        ("twenty one", 21),
        ("forty-five", 45),
        ("ninety-nine", 99),
        # Unhyphenated British spelling of the same compound.
        ("forty five", 45),
        ("twenty-two", 22),
        ("thirty-three", 33),
        ("ninety-nine", 99),
        # A unit never precedes another unit additively, so neither the spaced
        # "one two" nor the hyphenated "two-three" is a number.
        ("two three", None),
        ("two-three", None),
        ("one-two", None),
        # An additive compound is a tens word plus a unit 1..9. Each of these
        # used to compute to a plausible number — 20, 31, 50, 101 — which is
        # exactly how a typo could be certified as a verified count.
        ("twenty-zero", None),
        ("twenty-eleven", None),
        ("twenty-thirty", None),
        ("hundred-one", None),
        ("thirty-zero", None),
        ("ninety-ten", None),
        # ... except a scale word, which multiplies.
        ("five hundred", 500),
        ("two hundred", 200),
        # Hyphenated spelling of the same thing; the separator loop tries "-" first.
        ("one-hundred", 100),
        ("twelve thousand", 12_000),
    ],
)
def test_english_count_tokens_parse_to_the_number_they_spell(token, expected):
    from scripts.check_repo_consistency import _parse_stated_count

    assert _parse_stated_count(token) == expected


@pytest.mark.parametrize("token", ["", "forty five hundred", "many", "SLO"])
def test_an_unparseable_token_returns_none_rather_than_a_guess(token):
    """`None` makes the guard say "cannot verify"; a wrong int makes it
    confidently reject a correct document."""
    from scripts.check_repo_consistency import _parse_stated_count

    assert _parse_stated_count(token) is None


def test_every_regex_count_word_is_parseable():
    """The regex and the parser were separate lists, so "thirty" was matchable
    but unparseable. One tuple now feeds both; this asserts they agree."""
    from scripts.check_repo_consistency import _COUNT_WORD_LIST, _EN_COUNT_WORDS

    assert set(_COUNT_WORD_LIST) == set(_EN_COUNT_WORDS)
    for word in _COUNT_WORD_LIST:
        assert _EN_COUNT_WORDS[word] > 0 or word == "zero"


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        ("There are twenty-one SLO objectives.", 21),
        ("Forty-five SLO objectives.", 45),
        ("Thirty SLO objectives.", 30),
        ("Five hundred SLO objectives.", 500),
        ("twelve thousand SLO objectives", 12_000),
    ],
)
def test_a_correct_english_compound_count_is_not_flagged(claim, expected):
    """The regression: the `english` group captured only the first word, so
    "twenty-one SLO objectives" read as 20 and the guard rejected a runbook
    that had exactly 21 objectives."""
    from scripts.check_repo_consistency import slo_count_errors

    assert slo_count_errors("README.md", claim, expected) == []


def test_a_compound_count_that_drifts_is_still_flagged():
    """Parsing the whole compound must not weaken detection."""
    from scripts.check_repo_consistency import slo_count_errors

    errors = slo_count_errors("README.md", "twenty-one SLO objectives", expected=5)
    assert len(errors) == 1
    assert "states 21 SLO objectives but docs/slo-runbook.md defines 5" in errors[0]


def test_the_match_cannot_start_inside_a_compound():
    """`-` is a word boundary as far as `re` is concerned, so without a guard
    "twenty-one" matched as "one" and the guard reported 1."""
    from scripts.check_repo_consistency import _SLO_COUNT_RE

    matches = [match.group("english") for match in _SLO_COUNT_RE.finditer("twenty-one SLO objectives")]
    assert matches == ["twenty-one"], matches


def test_prose_without_a_number_before_slo_is_not_a_count_claim():
    """The guard looks for a number the document chose to state. "five
    objectives" and a bare "SLO objectives" are not claims about how many."""
    from scripts.check_repo_consistency import slo_count_errors

    for prose in ("The system has five objectives.", "SLO objectives are documented.", "See SLO objectives."):
        assert slo_count_errors("README.md", prose, expected=5) == []


def test_readme_recheck_commands_avoid_undeclared_system_packages():
    """The README's own reproduction commands must not need a package the
    repository never installs. `bc` was the one that broke it."""
    from scripts.check_repo_consistency import ROOT

    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for line in text.splitlines():
        if "def test_" in line and "git ls-files" in line:
            assert "| bc" not in line, line
            assert "awk" in line, line


# ── objective identifiers must be unique and contiguous ────────────────
def _runbook(*numbers: int) -> str:
    return "".join(f"| SLO-{n} | objective {n} | x | y | `DESIGN_TARGET` |\n" for n in numbers)


def test_the_identifier_list_is_not_deduplicated():
    """The regression: counting distinct ids hid the duplicate row. Six rows
    where two share `SLO-5` must count as six rows *and* be rejected, not
    quietly collapse to five and let every "5 个 SLO 目标" keep passing."""
    from scripts.check_repo_consistency import slo_objective_count, slo_objective_rows

    text = _runbook(1, 2, 3, 4, 5, 5)
    assert slo_objective_rows(text) == [1, 2, 3, 4, 5, 5]
    assert slo_objective_count(text) == 6


def test_a_duplicate_identifier_is_rejected():
    from scripts.check_repo_consistency import slo_objective_id_errors

    errors = slo_objective_id_errors("runbook.md", _runbook(1, 2, 3, 4, 5, 5))
    assert any("duplicate objective identifiers [5]" in error for error in errors), errors


def test_a_gap_in_the_identifiers_is_rejected():
    """Contiguity is what makes `SLO-6` mean the sixth objective. A gap means a
    row was inserted or deleted and every stated count is now ambiguous."""
    from scripts.check_repo_consistency import slo_objective_id_errors

    errors = slo_objective_id_errors("runbook.md", _runbook(1, 2, 4, 5))
    assert any("instead of a contiguous 1..4" in error for error in errors), errors


def test_a_reordered_runbook_is_rejected():
    from scripts.check_repo_consistency import slo_objective_id_errors

    assert slo_objective_id_errors("runbook.md", _runbook(2, 1, 3))


def test_a_clean_runbook_has_no_identifier_errors():
    from scripts.check_repo_consistency import slo_objective_id_errors

    assert slo_objective_id_errors("runbook.md", _runbook(1, 2, 3, 4, 5)) == []


def test_a_runbook_without_objectives_is_left_to_the_count_guard():
    """No rows means no ids to validate; reporting "duplicate []" here would be
    noise on top of the count guard's own message."""
    from scripts.check_repo_consistency import slo_objective_id_errors

    assert slo_objective_id_errors("runbook.md", "# Runbook\n\nNo table.\n") == []


def test_the_guard_reports_a_duplicate_even_when_the_summary_agrees(tmp_path, monkeypatch):
    """End-to-end: a runbook with a duplicated id plus a matching "6" summary
    must still fail, because the duplication is the defect."""
    from scripts.check_repo_consistency import check_slo_objective_counts

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "slo-runbook.md").write_text(_runbook(1, 2, 3, 4, 5, 5), encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text("6 个 SLO 目标与告警阈值。\n", encoding="utf-8")
    monkeypatch.setattr("scripts.check_repo_consistency.ROOT", tmp_path)
    monkeypatch.setattr("scripts.check_repo_consistency.CANONICAL_DOCS", [readme])

    errors: list[str] = []
    check_slo_objective_counts(errors, root=tmp_path)
    assert any("duplicate objective identifiers [5]" in error for error in errors), errors
    assert not any("states 6 SLO objectives" in error for error in errors), errors


def test_the_current_runbook_has_unique_contiguous_identifiers():
    from scripts.check_repo_consistency import ROOT, slo_objective_id_errors

    text = (ROOT / "docs/slo-runbook.md").read_text(encoding="utf-8")
    assert slo_objective_id_errors("docs/slo-runbook.md", text) == []


# ── Chinese numerals are words, not characters ──────────────────────────
@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("一", 1),
        ("两", 2),
        ("九", 9),
        # 十 is structural: an omitted leading 一 means one ten.
        ("十", 10),
        ("十一", 11),
        ("十二", 12),
        ("十九", 19),
        ("二十", 20),
        ("二十一", 21),
        ("三十", 30),
        ("九十九", 99),
    ],
)
def test_chinese_count_tokens_parse_to_the_number_they_spell(token, expected):
    """The regression: the character class matched one character, so
    `十二个 SLO 目标` matched `二个` and read as 2 — rejecting a correct
    document — and `SLO 目标（十二个）` did not match at all."""
    from scripts.check_repo_consistency import _parse_stated_count

    assert _parse_stated_count(token) == expected


@pytest.mark.parametrize("token", ["一百", "二百一十", "一千", "十十", "零"])
def test_a_chinese_numeral_beyond_the_supported_range_is_unverifiable(token):
    """Past 99 the notation stops being compositional, so guessing would be
    worse than declining: `None` makes the guard say "cannot verify"."""
    from scripts.check_repo_consistency import _parse_stated_count

    assert _parse_stated_count(token) is None


@pytest.mark.parametrize(
    ("claim", "stated"),
    [
        ("十二个 SLO 目标与告警阈值", 12),
        ("| SLO 目标（十二个） | `DESIGN_TARGET` |", 12),
        ("十一个 SLO 目标", 11),
        ("二十一个 SLO 目标", 21),
    ],
)
def test_a_multi_character_chinese_count_is_not_flagged_when_correct(claim, stated):
    from scripts.check_repo_consistency import slo_count_errors

    assert slo_count_errors("README.md", claim, stated) == []


def test_a_multi_character_chinese_count_that_drifts_is_flagged():
    from scripts.check_repo_consistency import slo_count_errors

    errors = slo_count_errors("README.md", "十二个 SLO 目标", expected=5)
    assert len(errors) == 1
    assert "states 12 SLO objectives but docs/slo-runbook.md defines 5" in errors[0]


def test_a_count_the_parser_cannot_read_is_reported_not_ignored():
    """百/千 are matched on purpose so the claim surfaces as unverifiable
    rather than slipping past the guard entirely."""
    from scripts.check_repo_consistency import slo_count_errors

    for claim in ("一百个 SLO 目标", "| SLO 目标（一千个） |"):
        errors = slo_count_errors("README.md", claim, expected=100)
        assert len(errors) == 1, (claim, errors)
        assert "cannot verify" in errors[0], (claim, errors)


def test_digit_counts_still_win_over_the_character_class():
    from scripts.check_repo_consistency import slo_count_errors

    assert slo_count_errors("README.md", "12 个 SLO 目标", expected=12) == []
    assert slo_count_errors("README.md", "5 个 SLO 目标", expected=5) == []


# ── the count parsers must be total ─────────────────────────────────────
@pytest.mark.parametrize(
    "token",
    ["二一十个", "十一二个", "十十", "一一十", "二十一十一", "二二", "", "十百"],
)
def test_a_malformed_chinese_numeral_returns_none_and_never_raises(token):
    """The regression: indexing the digit map with a multi-character side raised
    KeyError, so a documentation typo aborted the whole consistency check with a
    traceback — hiding every other problem with the repository along with it.
    This function runs in CI, so it has to be total."""
    from scripts.check_repo_consistency import _parse_stated_count

    assert _parse_stated_count(token) is None


@pytest.mark.parametrize(
    "token",
    ["", "   ", "abc", "5x", "SLO", "-", "forty five hundred", "1-2-3", "one two", "two-three"],
)
def test_unparseable_count_tokens_return_none_rather_than_a_guess(token):
    """`None` makes the guard say "cannot verify". A wrong int would make it
    confidently reject a correct document."""
    from scripts.check_repo_consistency import _parse_stated_count

    assert _parse_stated_count(token) is None


@pytest.mark.parametrize("claim", ["二一十个 SLO 目标", "十一二个 SLO 目标", "十十个 SLO 目标"])
def test_a_malformed_chinese_count_in_a_document_is_reported_not_crashed(claim):
    """The end-to-end shape of the finding: the guard emits its intended
    'cannot verify' error instead of taking the process down."""
    from scripts.check_repo_consistency import slo_count_errors

    errors = slo_count_errors("README.md", claim, expected=5)
    assert len(errors) == 1, (claim, errors)
    assert "cannot verify" in errors[0], (claim, errors)


def test_the_consistency_script_still_runs_with_a_malformed_count_in_a_document(tmp_path, monkeypatch):
    """The reason totality matters: one typo must not stop the other checks."""
    from scripts.check_repo_consistency import check_slo_objective_counts

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "slo-runbook.md").write_text(
        "| SLO-1 | a | x | y | `DESIGN_TARGET` |\n| SLO-2 | b | x | y | `DESIGN_TARGET` |\n",
        encoding="utf-8",
    )
    readme = tmp_path / "README.md"
    readme.write_text("二一十个 SLO 目标。\n", encoding="utf-8")
    monkeypatch.setattr("scripts.check_repo_consistency.ROOT", tmp_path)
    monkeypatch.setattr("scripts.check_repo_consistency.CANONICAL_DOCS", [readme])

    errors: list[str] = []
    check_slo_objective_counts(errors, root=tmp_path)
    assert any("cannot verify" in error for error in errors), errors


# ── the count token must be matched whole, not by its tail ──────────────
@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        # Chinese numerals are contiguous ideographs, so the class could match
        # `五个` inside `一百零五个` — reading a claim of 105 as 5, which passes
        # when the runbook has five objectives.
        ("一百零五个 SLO 目标", 5),
        ("| SLO 目标（一百零五个） |", 5),
        ("零五个 SLO 目标", 5),
        ("一百零五个 SLO 目标", 100),
    ],
)
def test_a_chinese_count_is_not_read_as_its_trailing_digits(claim, expected):
    from scripts.check_repo_consistency import slo_count_errors

    errors = slo_count_errors("README.md", claim, expected=expected)
    assert len(errors) == 1, (claim, errors)
    assert "cannot verify" in errors[0], (claim, errors)


@pytest.mark.parametrize("claim", ["十二个 SLO 目标", "| SLO 目标（十二个） |", "5 个 SLO 目标", "十一个 SLO 目标"])
def test_the_whole_token_guard_does_not_break_the_counts_it_should_accept(claim):
    """Anchoring the match must not cost the cases that genuinely work."""
    from scripts.check_repo_consistency import slo_count_errors

    stated = 11 if "十一" in claim else (12 if "十二" in claim else 5)
    assert slo_count_errors("README.md", claim, stated) == []


def test_a_hyphenated_unit_pair_is_a_typo_not_a_sum():
    """`two-three` must not parse as 5, or a documentation typo passes as a
    verified count — which is the opposite of what this parser is for."""
    from scripts.check_repo_consistency import _parse_stated_count, slo_count_errors

    assert _parse_stated_count("two-three") is None
    assert _parse_stated_count("one-two") is None

    errors = slo_count_errors("README.md", "two-three SLO objectives", expected=5)
    assert len(errors) == 1
    assert "cannot verify" in errors[0], errors


@pytest.mark.parametrize("token", ["二十", "二十一", "二十二", "三十", "九十"])
def test_the_tens_head_rule_still_accepts_every_real_compound(token):
    from scripts.check_repo_consistency import _parse_stated_count

    assert _parse_stated_count(token) is not None


def test_a_numeral_with_unrecognised_characters_is_never_read_as_its_tail():
    """Pins the CJK lookbehind specifically.

    `零` in the character class is what makes 一百零五 parse as one token. The
    lookbehind is the other half: without it, any numeral whose leading
    characters fall outside the class would still match its tail, so a claim of
    105 could be certified as a statement of 5. With it the claim is not matched
    at all — unchecked, which is the honest outcome, rather than wrong.
    """
    from scripts.check_repo_consistency import _SLO_COUNT_RE

    claim = "贰佰零五个 SLO 目标"
    matches = [match.group("lead") for match in _SLO_COUNT_RE.finditer(claim)]

    assert matches == [], f"the tail of an unrecognised numeral was matched as a count: {matches}"


def test_a_malformed_compound_is_never_certified_as_the_real_count():
    """The shape of the finding: a typo whose computed value coincides with the
    runbook's count must not be reported as verified. Each token here computes
    to 5 under a naive additive rule, against a five-objective runbook."""
    from scripts.check_repo_consistency import slo_count_errors

    for token in ("twenty-zero", "two-three", "one-two", "hundred-one"):
        errors = slo_count_errors("README.md", f"{token} SLO objectives", expected=5)
        assert len(errors) == 1, (token, errors)
        assert "cannot verify" in errors[0], (token, errors)


# ── a numeric count is captured whole, punctuation included ──────────────
@pytest.mark.parametrize(
    ("token", "expected"),
    [("5", 5), ("1005", 1005), ("1,005", 1005), ("1,234,567", 1_234_567)],
)
def test_grouped_and_plain_integers_parse_to_the_same_number(token, expected):
    """`1,005` and `1005` are one number; the separator is not a place to start
    reading."""
    from scripts.check_repo_consistency import _parse_stated_count

    assert _parse_stated_count(token) == expected


@pytest.mark.parametrize("token", ["1,00", "105.5", "1,00,5", ",5", "5,", "1,005.5", "5.0"])
def test_a_malformed_or_fractional_numeric_count_is_unverifiable(token):
    """A count of objectives is an integer written in valid grouping. Anything
    else declines rather than rounding to something plausible."""
    from scripts.check_repo_consistency import _parse_stated_count

    assert _parse_stated_count(token) is None


@pytest.mark.parametrize(
    "claim",
    [
        "1,005 SLO objectives",
        "There are 1,005 SLO objectives.",
        "1,005 个 SLO 目标",
        "| SLO 目标（1,005 个） |",
    ],
)
def test_a_grouped_count_that_drifts_is_flagged_not_read_as_its_suffix(claim):
    """The regression: `\\d+` started after the comma, so `1,005` was read as
    `005` = 5 — a false pass against a five-objective runbook, which is the one
    outcome worse than not checking at all."""
    from scripts.check_repo_consistency import slo_count_errors

    errors = slo_count_errors("README.md", claim, expected=5)
    assert len(errors) == 1, (claim, errors)
    assert "states 1005 SLO objectives" in errors[0], (claim, errors)


def test_a_fractional_count_is_reported_as_unverifiable():
    from scripts.check_repo_consistency import slo_count_errors

    errors = slo_count_errors("README.md", "105.5 SLO objectives", expected=5)
    assert len(errors) == 1
    assert "cannot verify" in errors[0], errors


def test_a_correct_grouped_count_is_accepted():
    """The fix must not turn a legitimate formatted count into a finding."""
    from scripts.check_repo_consistency import slo_count_errors

    assert slo_count_errors("README.md", "1,005 SLO objectives", expected=1005) == []
    assert slo_count_errors("README.md", "1,005 个 SLO 目标", expected=1005) == []


@pytest.mark.parametrize("token", ["-5", "-1,005", "+0"])
def test_a_signed_count_is_judged_whole_and_refused(token):
    """The regression the sweep found: a bare `\\d+` starts after the sign, so
    `-5` matched `5` and was certified as a statement of five objectives. The
    sign is captured instead, so the parser sees `-5`, declines it, and the
    guard says "cannot verify" — unchecked is safe, wrong is not."""
    from scripts.check_repo_consistency import slo_count_errors

    for claim in (f"{token} SLO objectives", f"{token} 个 SLO 目标", f"SLO 目标（{token} 个）"):
        errors = slo_count_errors("README.md", claim, expected=5)
        assert errors, (claim, "a signed count was accepted as a bare number")
        assert "cannot verify" in errors[0], (claim, errors)


@pytest.mark.parametrize("token", ["5", "05", "1005"])
def test_a_plain_integer_count_is_still_read_correctly(token):
    """The sign fix must not cost the ordinary readings."""
    from scripts.check_repo_consistency import _parse_stated_count, slo_count_errors

    assert _parse_stated_count(token) == int(token)
    assert slo_count_errors("README.md", f"{token} SLO objectives", expected=int(token)) == []


def test_no_numeric_token_shape_is_certified_as_the_wrong_count():
    """A sweep in place of another hand-written case.

    Two digits plus punctuation across every combination, three-digit sequences
    with grouping, and every ordered pair of CJK numeral characters — checked in
    all four claim formats the guard recognises. A token denoting five must be
    accepted, and a token denoting anything else must never pass unnoticed —
    a false pass is the whole reason this guard exists. Rejecting a wrong count is
    the guard working, so that is expected rather than a failure.
    """
    import itertools

    from scripts.check_repo_consistency import _parse_stated_count, slo_count_errors

    numerals = "一二两三四五六七八九十百千零"
    punctuation = "0123456789,.+-"

    tokens = {t for t in punctuation}
    tokens |= {"".join(pair) for pair in itertools.product(punctuation, repeat=2)}
    tokens |= {"".join(triple) for triple in itertools.product("0123456789", ",.", ",.")}
    tokens |= {"".join(pair) for pair in itertools.product(numerals, repeat=2)}

    # Multi-word numerals, which are where a match can start mid-phrase: the
    # engine reaches the final word and reads it as the whole count.
    words = ("one", "five", "twenty", "twenty-one", "hundred", "thousand", "and")
    tokens |= {f"{a} {b}" for a, b in itertools.product(words, repeat=2)}
    tokens |= {f"{a} and {b}" for a in words for b in words}

    # A silent pass means the guard read the token as five. That is only correct if
    # the token really does denote five, so the invariant is: if a claim passes
    # and the token is parseable at all, the parsed value must be exactly five.
    # Rejecting a wrong count is the guard working, not failing — "0 SLO
    # objectives" being flagged is the desired outcome, not a defect.
    false_passes: list[str] = []
    wrongly_rejected: list[str] = []
    for token in sorted(tokens):
        parsed = _parse_stated_count(token)
        # A Chinese numeral is only a claim in the Chinese claim forms; the guard
        # does not read `一十 SLO objectives` as English, and an unchecked claim
        # is the safe outcome. Mixing them here would be asserting coverage the
        # guard never claimed.
        if not token.isascii():
            forms = [f"{token} 个 SLO 目标", f"SLO 目标（{token} 个）"]
        elif " " in token:
            # Multi-word tokens are English numerals; pairing them with the
            # Chinese claim forms would only assert coverage the guard never
            # claimed, since that branch matches CJK and would leave them
            # unchecked.
            forms = [f"{token} SLO objectives", f"There are {token} SLO objectives."]
        else:
            forms = [
                f"{token} SLO objectives",
                f"There are {token} SLO objectives.",
                f"{token} 个 SLO 目标",
                f"SLO 目标（{token} 个）",
            ]
        for claim in forms:
            errors = slo_count_errors("README.md", claim, expected=5)
            if not errors:
                if parsed is not None and parsed != 5:
                    false_passes.append(f"{token!r} parses as {parsed} but passed as five, in {claim!r}")
            elif parsed == 5 and "cannot verify" not in errors[0]:
                wrongly_rejected.append(f"{token!r} is five but was rejected: {errors[0]}")

    assert false_passes == [], false_passes[:20]
    assert wrongly_rejected == [], wrongly_rejected[:20]


@pytest.mark.parametrize(
    "claim",
    [
        "There are one hundred and five SLO objectives.",
        "one hundred and five SLO objectives",
        "five hundred and five SLO objectives",
        "one thousand and twenty SLO objectives",
    ],
)
def test_a_match_may_not_start_inside_a_longer_numeral(claim):
    """The regression: the lookbehind permits whitespace, so the engine reached
    the final `five` of `one hundred and five` and read it as a statement of
    five — which passes against a five-objective runbook.

    `re` cannot express "not preceded by an arbitrarily long numeral phrase", so
    the preceding context is walked in Python. A claim that continues a larger
    numeral is unverifiable rather than silently wrong.
    """
    from scripts.check_repo_consistency import slo_count_errors

    errors = slo_count_errors("README.md", claim, expected=5)
    assert len(errors) == 1, (claim, errors)
    assert "continues a longer numeral" in errors[0], (claim, errors)


@pytest.mark.parametrize(
    ("claim", "stated"),
    [
        ("Five SLO objectives", 5),
        ("There are five SLO objectives.", 5),
        ("five SLO objectives", 5),
        ("twenty-one SLO objectives", 21),
        ("There are twenty-one SLO objectives.", 21),
        ("Five hundred SLO objectives", 500),
        ("1,005 SLO objectives", 1005),
        ("There are five SLO objectives and more", 5),
        # A conjunction with no numeral behind it introduces a count.
        ("and five SLO objectives", 5),
    ],
)
def test_the_continuation_check_does_not_swallow_ordinary_claims(claim, stated):
    """The guard has to tell "five" in "one hundred and five" from "five" in
    "There are five". A check that flagged both would be useless."""
    from scripts.check_repo_consistency import slo_count_errors

    assert slo_count_errors("README.md", claim, expected=stated) == []


def test_a_conjunction_after_a_numeral_continues_it_but_on_its_own_does_not():
    """The distinction the check turns on, named so the intent is legible."""
    from scripts.check_repo_consistency import _continues_a_larger_numeral

    def _at(text: str, word: str) -> int:
        """Start offset of `word` within `text`."""
        return text.index(word)

    assert _continues_a_larger_numeral("one hundred and five", _at("one hundred and five", "five"))
    assert _continues_a_larger_numeral("twenty and five", _at("twenty and five", "five"))
    assert not _continues_a_larger_numeral("and five", _at("and five", "five"))
    assert not _continues_a_larger_numeral("There are five", _at("There are five", "five"))
    assert not _continues_a_larger_numeral("five", 0)
    assert not _continues_a_larger_numeral("SLO objectives are five", _at("SLO objectives are five", "five"))


# ── documentation classification invariant ────────────────────────────────
# A Markdown file under `docs/` has exactly two legal states: current/canonical
# (listed in CANONICAL_DOCS, so the consistency guards actually check it) or
# historical (under `docs/archive/`, behind a banner that refuses it as a
# current source). The unguarded third state is what these tests pin down.

#: A banner satisfying both halves of the contract: the historical marker and
#: the refusal to be used as a current capability / architecture / metric /
#: validation source.
ARCHIVE_BANNER = (
    "> **HISTORICAL — ARCHIVED. SUPERSEDED BY THE CURRENT DOCUMENTATION.**\n"
    "> This file is kept for lineage only. It must not be used as a source for\n"
    "> current capability, architecture, metric or validation claims.\n"
)


def _write(root: Path, relative: str, text: str) -> Path:
    """Write `text` to `root/<relative>`, creating parents, and return the path."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_unclassified_nested_markdown_fails(tmp_path):
    """1. A Markdown file under docs/ that is neither canonical nor archived.

    This is the failure the invariant exists for: `docs/notes/draft.md` sits
    among the guides, is not listed in CANONICAL_DOCS, and is not in the
    archive, so no guard reads it and nothing declares it history. Its claims
    would then be indistinguishable from current truth.
    """
    draft = _write(tmp_path, "docs/notes/draft.md", "# Draft\n\nNotes kept while working.\n")
    canonical = [_write(tmp_path, "docs/guide.md", "# Guide\n")]

    assert doc_classification(draft, canonical, tmp_path) == UNCLASSIFIED

    errors = docs_classification_errors(tmp_path, canonical)
    assert len(errors) == 1, errors
    assert "docs/notes/draft.md: unclassified documentation" in errors[0]
    # The message has to name both legal homes, or it cannot be acted on.
    assert "listed in CANONICAL_DOCS" in errors[0]
    assert "docs/archive/" in errors[0]


def test_archive_doc_without_banner_fails(tmp_path):
    """2. An archived doc with no banner at all."""
    plan = _write(tmp_path, "docs/archive/old-plan.md", "# Old plan\n\nIt ran two 4B instances.\n")

    assert doc_classification(plan, [], tmp_path) == HISTORICAL

    errors = docs_classification_errors(tmp_path, [])
    assert len(errors) == 2, errors
    assert "no historical/superseded marker in its first 20 lines" in errors[0]
    assert "must declare that it is not a current capability / architecture / metric / validation source" in errors[1]


def test_archive_doc_with_banner_passes(tmp_path):
    """3. The same archived doc, behind a complete banner."""
    _write(tmp_path, "docs/archive/old-plan.md", f"{ARCHIVE_BANNER}\n# Old plan\n\nIt ran two 4B instances.\n")

    assert docs_classification_errors(tmp_path, []) == []


def test_canonical_doc_passes(tmp_path):
    """4. A current doc is owned by the existing guards and is left alone."""
    guide = _write(tmp_path, "docs/guide.md", "# Guide\n")

    assert doc_classification(guide, [guide], tmp_path) == CANONICAL
    assert docs_classification_errors(tmp_path, [guide]) == []


def test_validation_doc_passes(tmp_path):
    """5. A validation record is current documentation, not history.

    `docs/validation/` is enumerated in CANONICAL_DOCS precisely because these
    records make the same evidence claims as the guides. Filing one under the
    archive would exempt exactly the docs most able to drift.
    """
    record = _write(tmp_path, "docs/validation/real-ragas-evaluation.md", "# Validation record\n")

    assert doc_classification(record, [record], tmp_path) == CANONICAL
    assert docs_classification_errors(tmp_path, [record]) == []


def test_nested_archive_subdirectory_is_still_classified_as_historical(tmp_path):
    """Depth does not change the rule: docs/archive/<anything>/ is historical."""
    plan = _write(tmp_path, "docs/archive/2024/old-plan.md", f"{ARCHIVE_BANNER}\n# Old plan\n")

    assert doc_classification(plan, [], tmp_path) == HISTORICAL
    assert docs_classification_errors(tmp_path, []) == []


def test_a_historical_marker_alone_is_not_a_banner(tmp_path):
    """`Historical` without the refusal still reads as a source.

    The retired dual-4B topology and the missing-RAGAS zero fallback are both
    accurate about the past, which is exactly why a marker on its own is
    insufficient: it labels the file without telling the reader to stop
    trusting it.
    """
    _write(
        tmp_path,
        "docs/archive/old-plan.md",
        "# Old plan\n\nHistorical. It ran two 4B instances.\n",
    )

    errors = docs_classification_errors(tmp_path, [])
    assert len(errors) == 1, errors
    assert "must declare that it is not a current capability" in errors[0]


def test_a_refusal_without_a_historical_marker_is_not_a_banner(tmp_path):
    """The other half alone is equally insufficient."""
    _write(
        tmp_path,
        "docs/archive/old-plan.md",
        "# Old plan\n\nThis must not be used as a source for capability claims.\n",
    )

    errors = docs_classification_errors(tmp_path, [])
    assert len(errors) == 1, errors
    assert "no historical/superseded marker" in errors[0]


def test_a_banner_below_the_opening_lines_does_not_count(tmp_path):
    """The banner has to be met before the claims it qualifies.

    A reader who scrolls into the body first has already read `It ran two 4B
    instances` as present tense by the time the disclaimer appears.
    """
    padding = "\n".join(f"Body line {index}." for index in range(30))
    _write(tmp_path, "docs/archive/old-plan.md", f"{padding}\n\n{ARCHIVE_BANNER}\n")

    errors = docs_classification_errors(tmp_path, [])
    assert len(errors) == 2, errors


def test_the_archive_banner_is_written_in_chinese_too(tmp_path):
    """The contract is wording-agnostic, not English-only."""
    _write(
        tmp_path,
        "docs/archive/old-plan.md",
        "> **历史文档 — 已归档，已被取代。**\n"
        "> 本文件仅用于追溯，不得作为当前能力、架构、指标或验证结论的依据。\n\n"
        "# 旧方案\n",
    )

    assert docs_classification_errors(tmp_path, []) == []


def test_an_archived_doc_may_not_also_be_listed_as_canonical(tmp_path):
    """Claiming a file is both states is a contradiction, not a third option.

    Listing it in CANONICAL_DOCS would put it under the current-claim guards
    while its own banner refuses to be a current source.
    """
    plan = _write(tmp_path, "docs/archive/old-plan.md", f"{ARCHIVE_BANNER}\n# Old plan\n")

    errors = docs_classification_errors(tmp_path, [plan])
    assert len(errors) == 1, errors
    assert "both canonical and historical" in errors[0]


def test_the_invariant_never_reads_the_contents_of_a_canonical_doc(tmp_path):
    """A current doc may quote history; the guard must not call that drift.

    This is what keeps the invariant from duplicating the claim scanners: the
    existing guards already decide which current sentences are stale, and they
    exempt lines carrying an explicit historical marker. Classification only
    asks where a file lives.
    """
    guide = _write(
        tmp_path,
        "docs/guide.md",
        "# Guide\n\n"
        "Historical: the runtime used to run two 4B instances.\n"
        "历史：运行时过去部署两个 4B 实例。\n"
        "Removed, superseded, retired, obsolete, deprecated, no longer current.\n",
    )

    assert docs_classification_errors(tmp_path, [guide]) == []


def test_the_invariant_never_scans_changelog_history(tmp_path):
    """`CHANGELOG.md` records releases and is history by construction.

    Its release narration describes claims that were true then and are not now,
    so treating it as a current source would produce a permanent false failure.
    It is also outside `docs/`, and the invariant's scope is `docs/**/*.md`.
    """
    changelog = _write(
        tmp_path,
        "CHANGELOG.md",
        "# Changelog\n\n## [2.3.0]\n\n- ran two 4B instances\n- RAGAS returned zero\n",
    )
    _write(tmp_path, "docs/guide.md", "# Guide\n")

    assert changelog.exists()
    assert docs_classification_errors(tmp_path, [tmp_path / "docs/guide.md"]) == []


def test_check_docs_classification_is_wired_into_main():
    """Wiring check: a pure function nobody calls is decoration.

    `main()` is expected to return 0 because the repository is currently
    consistent; the sentinel records that the classification check actually
    ran, which is the part that regresses silently.
    """
    from scripts import check_repo_consistency as guard

    calls: list[int] = []
    original = guard.check_docs_classification

    def _record(errors, root=None):
        calls.append(1)
        return original(errors, root=root)

    guard.check_docs_classification = _record
    try:
        assert guard.main() == 0
    finally:
        guard.check_docs_classification = original
    assert calls == [1]


def test_every_markdown_file_in_docs_is_classified():
    """The repository itself has no third-category file.

    This is the check that would have caught `docs/demo/README.md` sitting in a
    nested directory that no glob in CANONICAL_DOCS covered. It runs the real
    entry point, so it also covers the default ROOT / CANONICAL_DOCS wiring.
    """
    errors: list[str] = []
    check_docs_classification(errors)
    assert errors == [], errors


def test_the_archive_is_itself_classified_and_documents_the_banner():
    """docs/archive/ exists to hold history, so it carries the banner too."""
    from scripts.check_repo_consistency import ROOT

    index = ROOT / "docs/archive/README.md"
    assert index.is_file()
    assert archive_banner_errors("docs/archive/README.md", index.read_text(encoding="utf-8")) == []


def test_the_docs_index_states_the_two_way_split():
    """The index is where a reader learns where a doc belongs."""
    from scripts.check_repo_consistency import ROOT

    text = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    errors: list[str] = []
    check_docs_index(errors)
    assert errors == [], errors
    assert "docs/archive/" in text


# ── Frontend evidence: CI build != end-to-end runtime != deployment ──────────
#
# `.github/workflows/ci.yml` has carried a `frontend-build` job (`npm ci` then
# `npm run build`) since PR #19, while the truth audit still said "CI does not
# build the frontend here" and filed the built frontend as PENDING. The tests
# below pin both halves of the correction: the CI build is REPO_VERIFIED, and it
# still cannot be read as end-to-end runtime or deployment evidence.


def test_ci_workflow_really_does_build_the_frontend():
    """The guard's premise, asserted against the workflow rather than assumed.

    If the job is ever removed or renamed, this fails and the classification
    decision gets revisited deliberately instead of silently going stale.
    """
    from scripts.check_repo_consistency import FRONTEND_CI_WORKFLOW

    workflow = FRONTEND_CI_WORKFLOW.read_text(encoding="utf-8")
    assert ci_builds_frontend(workflow), "ci.yml no longer installs+builds the frontend"
    assert "npm ci" in workflow
    assert "npm run build" in workflow


def test_ci_build_detection_requires_both_lockfile_install_and_build():
    """`npm ci` alone, or a build with no lockfile install, is not the gate."""
    only_install = "jobs:\n  a:\n    steps:\n      - run: npm ci\n"
    only_build = "jobs:\n  a:\n    steps:\n      - run: npm run build\n"
    neither = "jobs:\n  a:\n    steps:\n      - run: pytest\n"
    assert ci_builds_frontend(only_install) is False
    assert ci_builds_frontend(only_build) is False
    assert ci_builds_frontend(neither) is False


def test_ci_build_detection_treats_an_unparseable_workflow_as_building():
    """Fail closed: an unreadable workflow must not silence the denial scanner."""
    assert ci_builds_frontend("jobs: [this is not: valid: yaml") is True


#: The retired sentence, assembled from fragments. Spelling it out literally here
#: would leave the phrase in the tree and defeat the repo-wide check that asks
#: for it to be gone, so the tests match the real text without storing it.
_RETIRED_FRONTEND_CI_DENIAL = "does not " + "build the frontend"


def test_repository_does_not_deny_the_ci_frontend_build():
    """The exact drift sentence, across every canonical frontend-evidence doc."""
    from scripts.check_repo_consistency import FRONTEND_EVIDENCE_DOCS, ROOT

    errors: list[str] = []
    check_frontend_evidence_classification(errors)
    assert errors == [], errors

    offenders = [
        name
        for name in FRONTEND_EVIDENCE_DOCS
        if _RETIRED_FRONTEND_CI_DENIAL in (ROOT / name).read_text(encoding="utf-8").lower()
    ]
    assert offenders == [], offenders


def test_no_canonical_document_still_denies_the_ci_frontend_build():
    """Repo-wide, not just the docs the guard scans."""
    from scripts.check_repo_consistency import CANONICAL_DOCS, ROOT

    offenders = [
        str(path.relative_to(ROOT))
        for path in CANONICAL_DOCS
        if _RETIRED_FRONTEND_CI_DENIAL in path.read_text(encoding="utf-8").lower()
    ]
    assert offenders == [], offenders


def test_the_denial_survives_an_unrelated_pending_elsewhere_in_the_row():
    """A `PENDING` status cell must not excuse a denial of the CI build.

    This is the false negative that let the drift sit in the tree: the audit
    table row is a single physical line, so a negation window that runs to the
    end of the line reads the row's own `PENDING` classification as a denial of
    the sentence before it.
    """
    from scripts.check_repo_consistency import _FRONTEND_BUILD_DENIAL_PATTERNS, _scan_frontend_patterns

    row = (
        "| Frontend contract | client | app.py | tests | config.json | "
        f"CI {_RETIRED_FRONTEND_CI_DENIAL} here | "
        "`REPO_VERIFIED` (client) / `PENDING` (runtime) | keep apart |"
    )
    errors: list[str] = []
    _scan_frontend_patterns(
        "audit.md",
        row,
        _FRONTEND_BUILD_DENIAL_PATTERNS,
        "denies the CI frontend build",
        errors,
        mode="denial",
    )
    assert errors, "a PENDING status cell must not excuse denying the CI frontend build"


def test_frontend_ci_build_is_classified_repo_verified_and_runtime_stays_pending():
    """The audit row must keep all four states apart, in both directions."""
    from scripts.check_repo_consistency import ROOT

    audit = (ROOT / "docs" / "repository-truth-audit.md").read_text(encoding="utf-8")
    row = next(line for line in audit.splitlines() if line.startswith("| Frontend contract |"))
    status = [part.strip() for part in row.strip("|").split("|")][6]

    # (B) the CI build is verified; (C)/(D) are not.
    assert "REPO_VERIFIED" in status
    assert "PENDING" in status
    # And the row must not collapse them into a single claim.
    assert "built, integrated frontend" not in row
    assert "frontend build is separate" not in row


def test_demoting_the_ci_build_to_pending_is_rejected():
    """Filing the CI build as PENDING is the original drift in the other direction."""
    from scripts.check_repo_consistency import frontend_audit_classification_errors as classify

    assert (
        classify(
            "`REPO_VERIFIED` (A client + metadata contract) / `REPO_VERIFIED` (B CI build gate) / "
            "`PENDING` (C end-to-end runtime integration) / `PENDING` (D production deployment)",
            builds=True,
            has_e2e_artifact=False,
        )
        == []
    )

    # Demoting the CI build back to PENDING is rejected...
    demoted = classify("`PENDING` (built frontend)", builds=True, has_e2e_artifact=False)
    assert demoted, "demoting the CI build to PENDING must be rejected"

    # ...and so is upgrading runtime integration while no artifact is committed.
    upgraded = classify("`REPO_VERIFIED` (A, C, D)", builds=True, has_e2e_artifact=False)
    assert upgraded, "runtime integration must stay PENDING without a committed artifact"


def test_end_to_end_runtime_upgrade_requires_a_committed_artifact():
    """Runtime may upgrade with an artifact; deployment never may.

    The second half is the correction for the review finding: an earlier version
    of this test asserted that *both* claims could upgrade together once an
    artifact existed, which is exactly the (C)/(D) conflation the guard exists
    to prevent.
    """
    from scripts.check_repo_consistency import frontend_audit_classification_errors as classify

    without_artifact = classify(
        "`REPO_VERIFIED` (A client + metadata contract) / `REPO_VERIFIED` (B CI build gate) / "
        "`REPO_VERIFIED` (C end-to-end runtime integration) / `PENDING` (D production deployment)",
        builds=True,
        has_e2e_artifact=False,
    )
    assert without_artifact, "runtime integration must stay PENDING without a committed artifact"

    assert (
        classify(
            "`REPO_VERIFIED` (A client + metadata contract) / `REPO_VERIFIED` (B CI build gate) / "
            "`REPO_VERIFIED` (C end-to-end runtime integration) / `PENDING` (D production deployment)",
            builds=True,
            has_e2e_artifact=True,
        )
        == []
    ), "a committed artifact is what would license the runtime claim"


def test_the_level_must_be_attached_to_the_claim_it_describes():
    """A bare `REPO_VERIFIED` elsewhere in the row does not cover the CI build.

    This is the false negative that survived the first version of the guard: the
    row already said `REPO_VERIFIED` for the client contract, so deleting the
    build's own level left a row that still "contained REPO_VERIFIED" and read
    as correct to anything that did not parse the qualifiers.
    """
    from scripts.check_repo_consistency import frontend_audit_classification_errors as classify

    client_only = (
        "`REPO_VERIFIED` (A client + metadata contract) / "
        "`PENDING` (C end-to-end runtime integration) / `PENDING` (D production deployment)"
    )
    assert classify(client_only, builds=True, has_e2e_artifact=False), (
        "the client contract's REPO_VERIFIED must not stand in for the CI build"
    )

    # Same for PENDING: an unqualified or unrelated pending claim does not cover
    # end-to-end runtime integration.
    vague = "`REPO_VERIFIED` (B CI build gate) / `PENDING` (later)"
    assert classify(vague, builds=True, has_e2e_artifact=False), (
        "PENDING must be qualified by the runtime/deployment claim it covers"
    )


def test_the_corrected_row_classifies_cleanly():
    """The real audit row satisfies both halves of the rule."""
    from scripts.check_repo_consistency import ROOT
    from scripts.check_repo_consistency import frontend_audit_classification_errors as classify

    audit = (ROOT / "docs" / "repository-truth-audit.md").read_text(encoding="utf-8")
    row = next(line for line in audit.splitlines() if line.startswith("| Frontend contract |"))
    status = [part.strip() for part in row.strip("|").split("|")][6]
    assert classify(status, builds=True, has_e2e_artifact=False) == []


def test_ci_build_is_never_escalated_to_e2e_or_deployment():
    """The overclaims requirement 5 forbids, in both languages."""
    from scripts.check_repo_consistency import (
        _FRONTEND_DEPLOYMENT_ESCALATION_PATTERNS,
        _FRONTEND_RUNTIME_ESCALATION_PATTERNS,
        _check_frontend_runtime_cooccurrence,
        _scan_frontend_patterns,
    )

    def flag(text: str) -> list[str]:
        errors: list[str] = []
        for patterns in (_FRONTEND_RUNTIME_ESCALATION_PATTERNS, _FRONTEND_DEPLOYMENT_ESCALATION_PATTERNS):
            _scan_frontend_patterns(
                "doc.md",
                text,
                patterns,
                "escalates a CI build to end-to-end or deployment evidence",
                errors,
                mode="escalation",
            )
        _check_frontend_runtime_cooccurrence("doc.md", text, errors)
        return errors

    overclaims = [
        "The frontend build is end-to-end validated against the real backend.",
        "CI build proves end-to-end validation.",
        "CI frontend build proves production validated deployment.",
        "The frontend is deployed to production.",
        "Browser runtime verified against the real backend for the frontend.",
        "Playwright drove the frontend against the real backend.",
        "前端构建是端到端验证通过的。",
        "前端已在生产部署。",
        "浏览器运行时已针对真实后端验证前端。",
    ]
    for line in overclaims:
        assert flag(line), f"not flagged: {line}"


def test_states_that_runtime_and_deployment_are_pending_are_allowed():
    """The guard must not fire on the corrected, honest documentation."""
    from scripts.check_repo_consistency import _FRONTEND_RUNTIME_ESCALATION_PATTERNS, _scan_frontend_patterns

    allowed = [
        "End-to-end runtime integration is PENDING; no browser run against the real backend exists.",
        "Never say the frontend is validated end-to-end or browser-verified.",
        "A green `frontend-build` proves the bundle compiles and nothing more.",
        "The frontend builds in CI; production deployment stays deployment-specific.",
        "Frontend + real backend end-to-end runtime integration — the CI gate compiles the bundle and nothing more.",
    ]
    for line in allowed:
        errors: list[str] = []
        _scan_frontend_patterns(
            "doc.md",
            line,
            _FRONTEND_RUNTIME_ESCALATION_PATTERNS,
            "escalates a CI build to end-to-end or deployment evidence",
            errors,
            mode="escalation",
        )
        assert errors == [], f"false positive on: {line}"


def test_mock_backed_demo_capture_is_not_end_to_end_evidence():
    """The committed browser run drives the real UI against `mock_api.py`."""
    from scripts.check_repo_consistency import _FRONTEND_MOCK_AS_E2E_PATTERNS, _scan_frontend_patterns

    errors: list[str] = []
    _scan_frontend_patterns(
        "doc.md",
        "The demo capture is end-to-end evidence for the real backend.",
        _FRONTEND_MOCK_AS_E2E_PATTERNS,
        "presents the demo capture as end-to-end evidence",
        errors,
        mode="escalation",
    )
    assert errors


def test_readme_and_evidence_map_agree_with_the_audit_on_the_ci_frontend_build():
    """README / evidence map / truth audit must not disagree about the CI build."""
    from scripts.check_repo_consistency import ROOT

    audit = (ROOT / "docs" / "repository-truth-audit.md").read_text(encoding="utf-8")
    assert "`REPO_VERIFIED` (B CI build gate)" in audit

    # None of the three may imply the frontend is deployed or end-to-end validated.
    for name in ("README.md", "docs/evidence-map.md", "docs/repository-truth-audit.md"):
        text = (ROOT / name).read_text(encoding="utf-8").lower()
        for phrase in (
            "frontend is deployed",
            "frontend was deployed",
            "frontend e2e validated",
            "frontend is production validated",
            "frontend is production-validated",
        ):
            assert phrase not in text, f"{name} states {phrase!r}"


# ── Removed plan path: docs/superpowers/ is history, not a directory ────────
#
# PR #33 deleted `docs/superpowers/` from the branch; nothing was relocated, so
# the plans survive only in Git history. The failure these tests pin is a
# canonical doc naming that path in the present tense, which tells a reader to
# open a directory that is not there.


def test_the_removed_plan_directory_is_not_in_the_working_tree():
    """The premise. If the directory returns, the rule below stands down."""
    from scripts.check_repo_consistency import REMOVED_PLAN_PATH, ROOT

    assert not (ROOT / REMOVED_PLAN_PATH).exists()
    # ...and it was not relocated into a substitute the reader could open either.
    assert not (ROOT / "docs/superpowers").exists()


def test_no_canonical_document_names_the_removed_plan_path_in_the_present_tense():
    from scripts.check_repo_consistency import check_removed_plan_path_is_historical

    errors: list[str] = []
    check_removed_plan_path_is_historical(errors)
    assert errors == [], errors


def test_a_present_tense_reference_to_the_removed_path_is_flagged():
    from scripts.check_repo_consistency import _REMOVED_PLAN_HISTORICAL_FRAME_RE, REMOVED_PLAN_PATH

    def framed(sentence: str) -> bool:
        return bool(REMOVED_PLAN_PATH in sentence and _REMOVED_PLAN_HISTORICAL_FRAME_RE.search(sentence))

    drift = [
        "Historical plans under `docs/superpowers/` are not evidence.",
        "Superseded implementation plans under `docs/superpowers/` are not current evidence.",
        "Implementation plans are documented in `docs/superpowers/`.",
        "The plan of record lives in `docs/superpowers/`.",
        "Plans in `docs/superpowers/` describe the current architecture.",
        "计划见 `docs/superpowers/`。",
    ]
    for sentence in drift:
        assert not framed(sentence), f"not flagged as drift: {sentence}"

    allowed = [
        "Superseded plans, formerly stored under `docs/superpowers/`, are not evidence.",
        "The former `docs/superpowers/` directory was removed; its files live in Git history.",
        "Plans under `docs/superpowers/` were deleted and are preserved in Git history.",
        "原先存储在 `docs/superpowers/` 的计划已被删除，仅保留在 Git 历史中。",
    ]
    for sentence in allowed:
        assert framed(sentence), f"false positive on legitimate history: {sentence}"


def test_a_historical_frame_does_not_carry_to_the_next_sentence():
    """Sentence scope is the point: a paragraph-wide frame waves through drift."""
    from scripts.check_repo_consistency import _REMOVED_PLAN_HISTORICAL_FRAME_RE, REMOVED_PLAN_PATH

    first = "Plans, formerly stored under `docs/superpowers/`, were removed from the branch."
    second = "Plans under `docs/superpowers/` are current."
    assert _REMOVED_PLAN_HISTORICAL_FRAME_RE.search(first)
    assert REMOVED_PLAN_PATH in second
    assert not _REMOVED_PLAN_HISTORICAL_FRAME_RE.search(second)


def test_a_deleted_plan_file_reference_now_fails_the_path_check():
    """The dead `docs/superpowers/` exclusion used to mask exactly this.

    `check_documented_paths` used to skip any `docs/superpowers/...` reference, so
    a document could point at a plan file that had been deleted for months and
    the guard stayed silent. The exclusion is gone; the reference now fails.
    """
    from scripts.check_repo_consistency import ROOT, check_documented_paths

    # `check_documented_paths` reports via `path.relative_to(ROOT)`, so the probe
    # document has to live in the tree rather than in tmp_path. It is removed in
    # the finally block, and only this one check is invoked, so no other guard
    # observes the file.
    probe = ROOT / "_removed_plan_path_probe.md"
    assert not probe.exists()
    probe.write_text("See `docs/superpowers/plans/retrieval.md` for the design.\n", encoding="utf-8")
    errors: list[str] = []
    try:
        check_documented_paths(probe, errors)
    finally:
        probe.unlink()

    assert any("does not exist" in error for error in errors), errors
    assert not probe.exists()


# ── Review findings on PR #34 ────────────────────────────────────────────────
#
# An automated reviewer raised three findings against this PR. Two were real
# defects in code the PR itself introduced; the tests below pin both so they
# cannot regress.


def test_a_candidate_cannot_be_inserted_pre_approved():
    """P1: `add()` accepted a terminal status, bypassing the review gate.

    `export_regression_dataset` publishes every `accepted` row, so inserting one
    with `review_status="accepted"` reached the dataset without ever calling
    `set_review_status` — the only place the human gate and its expectation
    check live. That is exactly the guarantee the pipeline claims to provide.
    """
    from offline.regression_candidates import (
        ACCEPTED,
        PENDING_REVIEW,
        REJECTED,
        RegressionCandidate,
        RegressionCandidateStore,
    )

    store = RegressionCandidateStore(":memory:")
    try:
        for status in (ACCEPTED, REJECTED):
            candidate = RegressionCandidate(
                case_id=f"c-{status}",
                source_feedback_id="f1",
                question="q",
                expected_behaviour="must cite the ingredient list",
                expected_evidence=("doc-1",),
                review_status=status,
            )
            with pytest.raises(ValueError, match="set_review_status"):
                store.add(candidate)
            # And nothing reached the store, so nothing can be exported.
            assert store.count(status) == 0

        # The legitimate path still works.
        ok = RegressionCandidate(
            case_id="c-ok",
            source_feedback_id="f1",
            question="q",
            expected_behaviour="must cite the ingredient list",
            expected_evidence=("doc-1",),
            review_status=PENDING_REVIEW,
        )
        assert store.add(ok) is True
    finally:
        store.close()


def test_each_review_transition_records_its_own_timestamp():
    """P2: `reviewed_at or now` preserved the first decision's time.

    A candidate rejected and later accepted would attribute the approval to the
    rejection, corrupting the review audit trail.
    """
    from offline.regression_candidates import ACCEPTED, REJECTED, RegressionCandidate, RegressionCandidateStore

    store = RegressionCandidateStore(":memory:")
    try:
        store.add(
            RegressionCandidate(
                case_id="c1",
                source_feedback_id="f1",
                question="q",
                expected_behaviour="must cite the ingredient list",
                expected_evidence=("doc-1",),
            )
        )
        first = store.set_review_status("c1", REJECTED, note="too vague")
        rejected_at = first.reviewed_at
        assert rejected_at

        second = store.set_review_status("c1", ACCEPTED, reviewer="alice")
        assert second.reviewed_at != rejected_at, "acceptance must not reuse the rejection timestamp"
        assert second.reviewed_by == "alice"
        assert second.review_status == ACCEPTED
    finally:
        store.close()


def test_deployment_overclaim_checks_do_not_relax_with_an_e2e_artifact():
    """P2: gating deployment checks on the E2E artifact merged claims (C) and (D).

    A browser-against-real-backend run can establish runtime integration. It can
    never establish a production deployment. So the deployment patterns must
    stay active even when an artifact exists.
    """
    from scripts.check_repo_consistency import (
        _FRONTEND_DEPLOYMENT_ESCALATION_PATTERNS,
        _FRONTEND_RUNTIME_ESCALATION_PATTERNS,
        _scan_frontend_patterns,
    )

    deployment = [
        "The frontend is deployed to production.",
        "CI frontend build proves production validated deployment.",
        "前端已在生产部署。",
    ]
    for line in deployment:
        errors: list[str] = []
        _scan_frontend_patterns(
            "doc.md",
            line,
            _FRONTEND_DEPLOYMENT_ESCALATION_PATTERNS,
            "presents the frontend as deployed to production",
            errors,
            mode="escalation",
        )
        assert errors, f"deployment overclaim not flagged: {line}"

    # The runtime patterns are a separate set, so an artifact can relax them
    # without touching deployment.
    assert _FRONTEND_RUNTIME_ESCALATION_PATTERNS is not _FRONTEND_DEPLOYMENT_ESCALATION_PATTERNS
    assert not set(_FRONTEND_RUNTIME_ESCALATION_PATTERNS) & set(_FRONTEND_DEPLOYMENT_ESCALATION_PATTERNS)


def test_audit_row_must_keep_deployment_pending_even_with_an_artifact():
    """The classifier had the same conflation as the scanner."""
    from scripts.check_repo_consistency import frontend_audit_classification_errors as classify

    full = (
        "`REPO_VERIFIED` (A client + metadata contract) / `REPO_VERIFIED` (B CI build gate) / "
        "`PENDING` (C end-to-end runtime integration) / `PENDING` (D production deployment)"
    )
    assert classify(full, builds=True, has_e2e_artifact=False) == []

    # Runtime may upgrade once an artifact exists...
    artifact_backed = (
        "`REPO_VERIFIED` (A client + metadata contract) / `REPO_VERIFIED` (B CI build gate) / "
        "`REPO_VERIFIED` (C end-to-end runtime integration) / `PENDING` (D production deployment)"
    )
    assert classify(artifact_backed, builds=True, has_e2e_artifact=True) == []

    # ...but deployment may not, even with an artifact.
    assert classify(
        "`REPO_VERIFIED` (A client) / `REPO_VERIFIED` (B CI build gate) / "
        "`REPO_VERIFIED` (C end-to-end runtime integration) / `REPO_VERIFIED` (D production deployment)",
        builds=True,
        has_e2e_artifact=True,
    ), "an E2E artifact must never license a production-deployment claim"


# ── the Kubernetes static-check count is derived, never restated ────────────
def _k8s_claim_is_in_scope(text: str) -> bool:
    """True when the guard's scoping puts ``text`` in the Kubernetes comparison.

    A fixture that is silently out of scope asserts nothing: "no errors" then
    passes for the wrong reason and the regression it was written for stops
    holding. Every "this claim is accepted" fixture is checked against this.
    """
    from scripts.check_repo_consistency import (
        _K8S_CLAUSE_SPLIT_RE,
        _K8S_STATIC_CHECK_COUNT_RE,
        _K8S_STATIC_CHECK_PHRASE_RE,
        _K8S_SUBJECT_CONTEXT_RE,
        _markdown_claim_windows,
    )

    for window in _markdown_claim_windows(text):
        for clause in _K8S_CLAUSE_SPLIT_RE.split(window):
            if (
                _K8S_STATIC_CHECK_PHRASE_RE.search(clause)
                and _K8S_SUBJECT_CONTEXT_RE.search(clause)
                and _K8S_STATIC_CHECK_COUNT_RE.search(clause)
            ):
                return True
    return False


def test_k8s_static_check_count_is_derived_from_the_test_module():
    """The count comes from the module that defines the checks, not from a doc."""
    from scripts.check_repo_consistency import ROOT, k8s_static_check_count

    module = ROOT / "tests" / "deploy" / "test_k8s_manifests.py"
    derived = k8s_static_check_count(module)
    assert derived > 0, "the K8s manifest contract must define at least one static check"

    collected = subprocess.run(
        [sys.executable, "-m", "pytest", str(module), "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    match = re.search(r"(\d+) tests? collected", collected)
    assert match, f"could not read a collected-test count from pytest output:\n{collected}"
    assert derived == int(match.group(1)), (
        "the AST count and the collected-test count must agree; a disagreement means "
        "the guard is counting something pytest would not run"
    )


def test_k8s_static_check_count_ignores_nested_and_non_test_helpers(tmp_path):
    """Module level only, so a nested `test_*` helper cannot inflate the count."""
    from scripts.check_repo_consistency import k8s_static_check_count

    module = tmp_path / "test_k8s_manifests.py"
    module.write_text(
        "def test_one():\n"
        "    def test_nested_not_collected():\n"
        "        pass\n"
        "    test_nested_not_collected()\n"
        "\n"
        "def helper():\n"
        "    pass\n"
        "\n"
        "class TestClass:\n"
        "    def test_method_collected():\n"
        "        pass\n",
        encoding="utf-8",
    )
    assert k8s_static_check_count(module) == 1


def test_stale_k8s_static_check_count_is_reported():
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    claim = "`deploy/k8s/` 提供最小契约与 26 项静态检查。"
    errors = k8s_static_check_count_errors("README.md", claim, expected=31)
    assert len(errors) == 1
    assert "states 26 Kubernetes static checks" in errors[0]
    assert "defines 31" in errors[0]


def test_current_k8s_static_check_count_is_accepted():
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    for claim in (
        "`deploy/k8s/` 提供最小契约与 31 项静态检查。",
        "`tests/deploy/test_k8s_manifests.py` → 31 项静态检查",
        "`python3 -m pytest tests/deploy/ -q`（即 `tests/deploy/test_k8s_manifests.py`）→ 31 项静态检查。分组：",
        "静态检查指`tests/deploy/test_k8s_manifests.py`（31 项，全部离线、无网络、无集群）。",
        "The Kubernetes manifests and their 31 static checks",
        "The Kubernetes manifests and their thirty-one static checks",
    ):
        assert k8s_static_check_count_errors("README.md", claim, expected=31) == [], claim
        # A claim the guard skips because it is out of scope must not be "accepted"
        # for the wrong reason: confirm the scoping actually put it in scope.
        assert _k8s_claim_is_in_scope(claim), f"fixture is out of scope, so it asserts nothing: {claim}"


def test_unrelated_item_counts_are_not_read_as_static_check_counts():
    """`N 项` is common Chinese prose. Only a line that names the checks counts."""
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    for line in (
        "仓库包含 6 个核心工程能力与 5 项 SLO 目标。",
        "这个 manifest 用 0440 加 fsGroup 1000。",
        "基础镜像固定为 golang:1.24-alpine3.22 与 alpine:3.22.6。",
        "每个 epoch 通过持久 manifest 固定为一个 embedding version。",
        # Regression: a guard note that names the module and then says "一项检查"
        # is ordinary prose about adding a check, not a count of them.
        "31 这个数字由 `tests/deploy/test_k8s_manifests.py` 定义，所以再加一项检查时，摘要不会再留在旧数字上。",
    ):
        assert k8s_static_check_count_errors("README.md", line, expected=31) == [], line


def test_unreadable_k8s_static_check_count_is_reported_not_ignored():
    """A shape the parser cannot read is a gap in the guard, not a pass."""
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    errors = k8s_static_check_count_errors("README.md", "`deploy/k8s/` 契约与二百零六项静态检查。", expected=31)
    assert len(errors) == 1
    assert "cannot verify the static-check count" in errors[0]

    negative = k8s_static_check_count_errors("README.md", "`deploy/k8s/` 契约与 -26 项静态检查。", expected=31)
    assert len(negative) == 1, "a sign must be captured, so -26 can never be read as 26 and pass"


def test_current_documents_state_the_static_check_count_the_module_defines():
    from scripts.check_repo_consistency import (
        ROOT,
        k8s_static_check_count,
        k8s_static_check_count_errors,
    )

    expected = k8s_static_check_count(ROOT / "tests" / "deploy" / "test_k8s_manifests.py")
    checked = 0
    for path in CANONICAL_DOCS:
        if not path.exists():
            continue
        assert k8s_static_check_count_errors(str(path), path.read_text(encoding="utf-8"), expected) == []
        checked += 1
    assert checked > 0


def test_guard_reports_a_missing_static_check_module(tmp_path, monkeypatch):
    from scripts.check_repo_consistency import check_k8s_static_check_counts

    monkeypatch.setattr("scripts.check_repo_consistency.ROOT", tmp_path)
    errors: list[str] = []
    check_k8s_static_check_counts(errors, root=tmp_path)
    assert errors == [], "an absent module means there is nothing to compare; nothing is claimed"


def test_guard_reports_an_empty_static_check_module(tmp_path, monkeypatch):
    """Silently skipping a module with no checks would make the guard a no-op."""
    from scripts.check_repo_consistency import check_k8s_static_check_counts

    module = tmp_path / "tests" / "deploy" / "test_k8s_manifests.py"
    module.parent.mkdir(parents=True)
    module.write_text("def helper():\n    pass\n", encoding="utf-8")
    monkeypatch.setattr("scripts.check_repo_consistency.ROOT", tmp_path)

    errors: list[str] = []
    check_k8s_static_check_counts(errors, root=tmp_path)
    assert len(errors) == 1
    assert "no module-level test functions found" in errors[0]


def test_guard_fails_end_to_end_when_the_static_check_count_drifts(tmp_path, monkeypatch):
    """Wiring check: `main()` must call the guard, or the pure function is decoration."""
    from scripts.check_repo_consistency import check_k8s_static_check_counts

    module = tmp_path / "tests" / "deploy" / "test_k8s_manifests.py"
    module.parent.mkdir(parents=True)
    module.write_text("def test_a():\n    pass\n\n\ndef test_b():\n    pass\n", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text("`deploy/k8s/` 契约与 26 项静态检查。\n", encoding="utf-8")
    monkeypatch.setattr("scripts.check_repo_consistency.ROOT", tmp_path)
    monkeypatch.setattr("scripts.check_repo_consistency.CANONICAL_DOCS", [readme])

    errors: list[str] = []
    check_k8s_static_check_counts(errors, root=tmp_path)
    assert len(errors) == 1
    assert "states 26 Kubernetes static checks but tests/deploy/test_k8s_manifests.py defines 2" in errors[0]


def test_consistency_guard_main_calls_the_static_check_guard():
    """A guard that is not invoked from `main()` never runs in CI."""
    from scripts import check_repo_consistency as guard

    source = Path(guard.__file__).read_text(encoding="utf-8")
    main_body = source.split("def main()", 1)[1]
    assert "check_k8s_static_check_counts(errors)" in main_body


# ── review: the guard must only compare counts that are about Kubernetes ──────
def test_static_check_count_without_a_kubernetes_subject_is_ignored():
    """`static checks` alone does not make a number a Kubernetes claim.

    This repository has more than one thing a document could call a static check.
    Comparing any of them against the K8s manifest module's size would invent an
    error out of an unrelated sentence.
    """
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    for line in (
        "The frontend is covered by 12 static checks.",
        "The demo capture script is covered by 3 static checks.",
        "The retrieval benchmark harness is covered by 41 static checks.",
        "前端由 12 项静态检查覆盖。",
        # A different deploy-shaped suite: `tests/deploy/` on its own is not
        # Kubernetes context, and this one names another module explicitly.
        "`tests/test_demo_corpus_rbac_consistency.py` 覆盖 5 项静态检查。",
        # A wrong number would still be ignored: out of scope beats wrong.
        "The frontend is covered by 999 static checks.",
    ):
        assert k8s_static_check_count_errors("README.md", line, expected=31) == [], line
        assert not _k8s_claim_is_in_scope(line), f"fixture must be out of scope: {line}"


def test_a_kubernetes_claim_next_to_a_non_kubernetes_one_is_scoped_separately():
    """Windows are bounded, so one claim cannot borrow the other's subject.

    Concatenating the document would put both sentences in one window and read the
    frontend's 12 as the manifest module's count.
    """
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    document = (
        "`deploy/k8s/` 的清单与 31 项静态检查确实存在且为 `REPO_VERIFIED`。\n"
        "\n"
        "The frontend is covered by 12 static checks.\n"
    )
    assert k8s_static_check_count_errors("README.md", document, expected=31) == []


def test_an_unrelated_tally_in_another_clause_is_not_read_as_the_static_check_count():
    """Within one window, the count still has to share a clause with the phrase."""
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    document = "`deploy/k8s/` 清单有 8 项已知限制；`tests/deploy/test_k8s_manifests.py` 的 31 项静态检查全部离线。"
    assert k8s_static_check_count_errors("README.md", document, expected=31) == []

    stale = "`deploy/k8s/` 清单有 8 项已知限制；`tests/deploy/test_k8s_manifests.py` 的 26 项静态检查全部离线。"
    errors = k8s_static_check_count_errors("README.md", stale, expected=31)
    assert len(errors) == 1
    assert "states 26 Kubernetes static checks" in errors[0]


def test_a_kubernetes_subject_is_required_before_the_number_is_read():
    """`tests/deploy/` names the deploy contract, not this module."""
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    # Correct number, but nothing in the window says Kubernetes...
    assert k8s_static_check_count_errors("README.md", "`tests/deploy/` 有 31 项静态检查。", expected=31) == []
    # ...and a wrong number stays unreported for the same reason.
    assert k8s_static_check_count_errors("README.md", "`tests/deploy/` 有 26 项静态检查。", expected=31) == []
    # Naming the module is what brings it into scope.
    errors = k8s_static_check_count_errors(
        "README.md",
        "`tests/deploy/test_k8s_manifests.py` 有 26 项静态检查。",
        expected=31,
    )
    assert len(errors) == 1
    assert "states 26 Kubernetes static checks" in errors[0]


# ── review: a Markdown soft wrap must not hide a claim ───────────────────────
def test_static_check_count_split_across_a_soft_wrap_is_still_checked():
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    wrapped_stale = "The Kubernetes manifests are covered by 26 static\nchecks."
    errors = k8s_static_check_count_errors("README.md", wrapped_stale, expected=31)
    assert len(errors) == 1
    assert "states 26 Kubernetes static checks" in errors[0]

    wrapped_current = "The Kubernetes manifests are covered by 31 static\nchecks."
    assert k8s_static_check_count_errors("README.md", wrapped_current, expected=31) == []

    # The subject itself may be on the other side of the wrap too.
    subject_wrapped = (
        "The Kubernetes manifests are covered by 26 static\nchecks in\n`tests/deploy/test_k8s_manifests.py`."
    )
    assert len(k8s_static_check_count_errors("README.md", subject_wrapped, expected=31)) == 1

    # Chinese, wrapped between the count and the phrase.
    cn_stale = "`deploy/k8s/` 的清单与 26 项\n静态检查确实存在。"
    assert len(k8s_static_check_count_errors("README.md", cn_stale, expected=31)) == 1
    cn_current = "`deploy/k8s/` 的清单与 31 项\n静态检查确实存在。"
    assert k8s_static_check_count_errors("README.md", cn_current, expected=31) == []


def test_a_wrapped_non_kubernetes_claim_is_still_ignored():
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    wrapped = "The frontend is covered by 12 static\nchecks."
    assert k8s_static_check_count_errors("README.md", wrapped, expected=31) == []


def test_claim_windows_are_bounded_block_elements():
    """Wrapped lines join their block; headings, rows and items start new ones."""
    from scripts.check_repo_consistency import _markdown_claim_windows

    document = (
        "first paragraph line one\n"
        "first paragraph line two\n"
        "\n"
        "## A heading\n"
        "\n"
        "- a list item line one\n"
        "  a list item line two\n"
        "- second item\n"
        "\n"
        "| a | table row |\n"
        "|---|---|\n"
        "| b | another row |\n"
    )
    assert _markdown_claim_windows(document) == [
        "first paragraph line one first paragraph line two",
        "## A heading",
        "- a list item line one a list item line two",
        "- second item",
        "| a | table row |",
        "| b | another row |",
    ]


def test_guarded_documents_keep_every_real_claim_in_scope():
    """The scoping must not have silently dropped a current document's claim.

    Scope narrowing is only safe while the claims stay visible: five current
    documents state the count, and each of them must still reach the comparison.
    """
    from scripts.check_repo_consistency import (
        ROOT,
        k8s_static_check_count,
        k8s_static_check_count_errors,
    )

    expected = k8s_static_check_count(ROOT / "tests" / "deploy" / "test_k8s_manifests.py")
    claiming = {
        "README.md",
        "docs/deployment-guide-k8s.md",
        "docs/repository-metadata.md",
        "docs/repository-truth-audit.md",
    }
    found = {
        path.relative_to(ROOT).as_posix()
        for path in CANONICAL_DOCS
        if path.exists()
        and _k8s_claim_is_in_scope(path.read_text(encoding="utf-8"))
        and k8s_static_check_count_errors(str(path), path.read_text(encoding="utf-8"), expected) == []
    }
    assert claiming <= found, f"claims dropped out of scope: {sorted(claiming - found)}"


# ── review: the Kubernetes subject must be bound to the counted clause ────────
def test_the_kubernetes_subject_does_not_authorise_a_neighbouring_clause():
    """Window-level licensing was the first false positive: it still was not fixed.

    One paragraph, two clauses, two different subjects. Checking the subject at the
    window level let the Kubernetes clause vouch for the frontend's 12 checks, so
    the guard would report a count that is perfectly correct about a different
    suite.
    """
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    for document in (
        "The Kubernetes manifests are documented here; The frontend is covered by 12 static checks.",
        "The Kubernetes manifests are documented here;\nThe frontend is covered by 12 static checks.",
        "`deploy/k8s/` 清单已归档；前端由 12 项静态检查覆盖。",
    ):
        assert k8s_static_check_count_errors("README.md", document, expected=31) == [], document
        assert not _k8s_claim_is_in_scope(document), f"fixture must be out of scope: {document}"

    # The reverse order must not leak either: the count precedes its subject clause.
    reversed_claim = "The frontend is covered by 12 static checks; the Kubernetes manifests are documented here."
    assert k8s_static_check_count_errors("README.md", reversed_claim, expected=31) == []
    assert not _k8s_claim_is_in_scope(reversed_claim)

    # Naming the subject in the *same* clause is still what brings it into scope.
    in_scope = "The Kubernetes manifests are covered by 12 static checks."
    assert len(k8s_static_check_count_errors("README.md", in_scope, expected=31)) == 1
    assert _k8s_claim_is_in_scope(in_scope)


def test_a_wrapped_claim_still_binds_its_subject_inside_the_clause():
    """The clause bound applies to a soft-wrapped claim too, not just a single line."""
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    wrapped = "The Kubernetes manifests are\ndocumented here; The frontend is covered by 12 static\nchecks."
    assert k8s_static_check_count_errors("README.md", wrapped, expected=31) == []

    stale = "The Kubernetes manifests are covered by\n26 static checks."
    assert len(k8s_static_check_count_errors("README.md", stale, expected=31)) == 1


# ── review: a blockquote wraps per line, so its lines are one window ──────────
def test_a_wrapped_blockquote_claim_is_still_checked():
    """Standard Markdown repeats `>` on every line of a paragraph.

    Treating each marked line as its own block element split one rendered paragraph
    into one window per line, and neither half held both the count and the subject —
    so rewrapping a stale claim inside a blockquote hid it.
    """
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    stale = "> The Kubernetes manifests are covered by 26 static\n> checks.\n"
    errors = k8s_static_check_count_errors("README.md", stale, expected=31)
    assert len(errors) == 1
    assert "states 26 Kubernetes static checks" in errors[0]

    assert (
        k8s_static_check_count_errors(
            "README.md", "> The Kubernetes manifests are covered by 31 static\n> checks.\n", expected=31
        )
        == []
    )

    # The `>`-only line is a paragraph break inside the quote, not a claim.
    separated = "> The Kubernetes manifests are documented here.\n>\n> The frontend is covered by 12 static checks.\n"
    assert k8s_static_check_count_errors("README.md", separated, expected=31) == []

    # A blank source line also ends a quote paragraph.
    blank = "> The Kubernetes manifests are documented here.\n\n> The frontend is covered by 12 static checks.\n"
    assert k8s_static_check_count_errors("README.md", blank, expected=31) == []

    # The unmarked line after the quote is a lazy continuation and joins the quote's
    # window, so what keeps its own 12 out of scope is the sentence split, not the
    # window boundary.
    after = (
        "> The Kubernetes manifests are covered by 26 static checks.\nThe frontend is covered by 12 static checks.\n"
    )
    errors = k8s_static_check_count_errors("README.md", after, expected=31)
    assert len(errors) == 1, "only the quoted Kubernetes claim is in scope"


def test_claim_windows_keep_wrapped_blockquote_lines_together():
    from scripts.check_repo_consistency import _markdown_claim_windows

    document = (
        "> a quoted line one\n"
        "> a quoted line two\n"
        "\n"
        "> another quote\n"
        ">\n"
        "> after the paragraph break\n"
        "\n"
        "ordinary prose\n"
    )
    assert _markdown_claim_windows(document) == [
        "a quoted line one a quoted line two",
        "another quote",
        "after the paragraph break",
        "ordinary prose",
    ]


# ── review: a grouping comma is part of the count, not a clause break ────────
def test_a_grouped_count_is_not_split_on_its_grouping_comma():
    """`1,031` is one number; splitting there read it as `031` and certified it."""
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    errors = k8s_static_check_count_errors(
        "README.md", "The Kubernetes manifests are covered by 1,031 static checks.", expected=31
    )
    assert len(errors) == 1
    assert "states 1031 Kubernetes static checks" in errors[0], "1,031 must be read as 1031, never as 031 or 31"

    assert (
        k8s_static_check_count_errors(
            "README.md", "The Kubernetes manifests are covered by 31 static checks.", expected=31
        )
        == []
    )
    assert (
        len(
            k8s_static_check_count_errors(
                "README.md", "The Kubernetes manifests are covered by 26 static checks.", expected=31
            )
        )
        == 1
    )

    # Chinese grouping uses the same ASCII comma, and the count may sit after the
    # subject in a wrapped line.
    cn = "`deploy/k8s/` 的清单由 1,031 项\n静态检查覆盖。"
    cn_errors = k8s_static_check_count_errors("README.md", cn, expected=31)
    assert len(cn_errors) == 1
    assert "states 1031" in cn_errors[0]


def test_an_ordinary_comma_still_separates_clauses():
    """The grouping exception must not become "commas never separate"."""
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    document = (
        "The Kubernetes manifests are documented here, the frontend is covered by 12 static checks, "
        "and the demo capture by 3."
    )
    assert k8s_static_check_count_errors("README.md", document, expected=31) == []

    # The exemption is a *grouping* comma, not "any comma next to a digit": a comma
    # after a count is a sentence boundary, and it must still bind the subject to
    # the clause it appears in.
    split_but_bound = (
        "The Kubernetes manifests have 8 known limits, and the k8s manifests are covered by 26 static checks."
    )
    errors = k8s_static_check_count_errors("README.md", split_but_bound, expected=31)
    assert len(errors) == 1, "the 8 must not be compared; the 26 must be"
    assert "states 26 Kubernetes static checks" in errors[0]

    # A malformed grouping is not a group, so it is prose: out of scope beats
    # reading it as a passing count.
    malformed = "The Kubernetes manifests are covered by 1,03 static checks."
    assert k8s_static_check_count_errors("README.md", malformed, expected=31) == []
    assert not _k8s_claim_is_in_scope(malformed), "a malformed grouping must not be certified by accident"


# ── review: sentences are clauses, lazy quotes continue, headings end ─────────
def test_period_delimited_sentences_are_separate_clauses():
    """Two English sentences in one paragraph are two claims.

    The splitter had `。`, `!` and `?` but not the ordinary ASCII full stop, so
    `...documented here. The frontend is covered by 12 static checks.` stayed one
    clause and the frontend's count borrowed the manifests' subject — the same
    cross-clause leak the clause-level subject check exists to stop.
    """
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    for document in (
        "The Kubernetes manifests are documented here. The frontend is covered by 12 static checks.",
        "The Kubernetes manifests are documented here.  The frontend is covered by 999 static checks.",
        "The frontend is covered by 12 static checks. The Kubernetes manifests are documented here.",
        "The manifests are documented here! The frontend is covered by 12 static checks?",
    ):
        assert k8s_static_check_count_errors("README.md", document, expected=31) == [], document
        assert not _k8s_claim_is_in_scope(document), f"fixture must be out of scope: {document}"

    # A stale claim in its own sentence is still caught.
    stale = "The frontend is documented here. The Kubernetes manifests are covered by 26 static checks."
    errors = k8s_static_check_count_errors("README.md", stale, expected=31)
    assert len(errors) == 1
    assert "states 26 Kubernetes static checks" in errors[0]


def test_periods_inside_a_token_are_not_sentence_boundaries():
    """Splitting every `.` would break file paths and decimals out of the subject."""
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    document = "See `README.md`, `deploy/k8s/api-deployment.yaml` and the 1.5 GiB limit. The frontend is covered by 12 static checks."
    assert k8s_static_check_count_errors("README.md", document, expected=31) == []

    # A path that *ends* a sentence still ends it: the period before `py` is not
    # the boundary, the full stop after it is.
    wrapped = "The manifests are defined in `tests/deploy/test_k8s_manifests.py`.\nThe frontend is covered by 12 static checks."
    assert k8s_static_check_count_errors("README.md", wrapped, expected=31) == []

    # Without that full stop the text really is one clause, and saying so is the
    # honest reading — the guard may not invent a boundary the author did not write.
    unpunctuated = "The manifests are defined in `tests/deploy/test_k8s_manifests.py`\nThe frontend is covered by 12 static checks."
    assert len(k8s_static_check_count_errors("README.md", unpunctuated, expected=31)) == 1

    # A numbered claim whose count precedes a period is not truncated.
    assert (
        len(
            k8s_static_check_count_errors(
                "README.md", "The Kubernetes manifests are covered by 26 static checks.", expected=31
            )
        )
        == 1
    )


def test_a_lazy_blockquote_continuation_stays_in_the_quote_window():
    """CommonMark lazy continuation: the unmarked line is part of the quote.

    An unconditional flush on leaving the quote dropped the count out of the
    subject's window, so rewrapping a stale claim this way bypassed the guard.
    """
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    stale = "> The Kubernetes manifests are covered by 26 static\nchecks.\n"
    errors = k8s_static_check_count_errors("README.md", stale, expected=31)
    assert len(errors) == 1
    assert "states 26 Kubernetes static checks" in errors[0]

    assert (
        k8s_static_check_count_errors(
            "README.md", "> The Kubernetes manifests are covered by 31 static\nchecks.\n", expected=31
        )
        == []
    )

    # A lazy continuation is one window; whether the 12 is in scope then depends only
    # on whether the author ended a sentence. Without punctuation the text really
    # is one clause, and the guard reports it rather than guessing a boundary.
    unpunctuated = (
        "> The Kubernetes manifests are covered by 26 static checks\nThe frontend is covered by 12 static checks.\n"
    )
    errors = k8s_static_check_count_errors("README.md", unpunctuated, expected=31)
    assert len(errors) == 2

    punctuated = (
        "> The Kubernetes manifests are covered by 26 static checks.\nThe frontend is covered by 12 static checks.\n"
    )
    errors = k8s_static_check_count_errors("README.md", punctuated, expected=31)
    assert len(errors) == 1, "the punctuated second sentence has no Kubernetes subject"

    # A blank line still ends the quote, so the prose after it is its own window.
    separated = (
        "> The Kubernetes manifests are covered by 26 static checks.\n\nThe frontend is covered by 12 static checks.\n"
    )
    assert len(k8s_static_check_count_errors("README.md", separated, expected=31)) == 1


def test_a_heading_does_not_absorb_the_paragraph_after_it():
    """A heading is one line; the next line starts a new paragraph, blank or not."""
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    for document in (
        "## Kubernetes manifests\nThe frontend is covered by 12 static checks.",
        "## Kubernetes manifests\n\nThe frontend is covered by 12 static checks.",
        "### k8s manifests\nThe demo capture is covered by 3 static checks.",
    ):
        assert k8s_static_check_count_errors("README.md", document, expected=31) == [], document
        assert not _k8s_claim_is_in_scope(document), f"fixture must be out of scope: {document}"

    # A stale claim in the paragraph after a heading is still caught.
    stale = "## Kubernetes manifests\nThe manifests are covered by 26 static checks in `deploy/k8s/`."
    errors = k8s_static_check_count_errors("README.md", stale, expected=31)
    assert len(errors) == 1
    assert "states 26 Kubernetes static checks" in errors[0]

    # A stale claim inside the heading itself is caught too.
    in_heading = "## The Kubernetes manifests are covered by 26 static checks\n\nBody prose.\n"
    assert len(k8s_static_check_count_errors("README.md", in_heading, expected=31)) == 1


def test_claim_windows_close_single_line_blocks_but_keep_list_and_quote_continuations():
    """The structural rule in one place: headings and rows close, items and quotes continue."""
    from scripts.check_repo_consistency import _markdown_claim_windows

    document = (
        "## a heading\n"
        "the paragraph under it\n"
        "\n"
        "| a | table row |\n"
        "prose after the row\n"
        "\n"
        "- a list item\n"
        "  its wrapped continuation\n"
        "- second item\n"
        "\n"
        "> a quoted line\n"
        "its lazy continuation\n"
    )
    assert _markdown_claim_windows(document) == [
        "## a heading",
        "the paragraph under it",
        "| a | table row |",
        "prose after the row",
        "- a list item its wrapped continuation",
        "- second item",
        "a quoted line its lazy continuation",
    ]


# ── review: Chinese counts must bind to 静态检查, and blocks must close ────────
def test_a_chinese_item_count_is_not_read_as_a_static_check_count():
    """`N 项` counts items of every kind; only the one bound to 静态检查 is a count.

    The Chinese alternative matched every `N 项`, so
    `Kubernetes 清单的 5 项资源由 31 项静态检查覆盖。` reported the 5 resources as
    five static checks even though the stated check count was correct.
    """
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    for document in (
        "Kubernetes 清单的 5 项资源由 31 项静态检查覆盖。",
        "`deploy/k8s/` 契约含 12 项限制与 31 项静态检查。",
    ):
        # These are in scope — the bound count is a real static-check claim — so the
        # assertion has to be about *which* number was read, not about scope.
        assert _k8s_claim_is_in_scope(document), f"fixture must be in scope: {document}"
        assert k8s_static_check_count_errors("README.md", document, expected=31) == [], document

    # The same shape with a stale *check* count: the resource count must not be the
    # number reported, so exactly one error and it names the check count.
    resource_then_stale = k8s_static_check_count_errors(
        "README.md", "Kubernetes 清单的 5 项资源由 26 项静态检查覆盖。", expected=31
    )
    assert len(resource_then_stale) == 1
    assert "states 26 Kubernetes static checks" in resource_then_stale[0]

    # A resource count on its own, with no static-check count bound to it, is out of
    # scope entirely — nothing about it can be verified.
    resources_alone = "Kubernetes 清单的 5 项资源由测试覆盖。"
    assert k8s_static_check_count_errors("README.md", resources_alone, expected=31) == []
    assert not _k8s_claim_is_in_scope(resources_alone)

    # The count that *does* modify 静态检查 is still compared, in either order.
    stale = "`deploy/k8s/` 提供 3 项契约与 26 项静态检查。"
    errors = k8s_static_check_count_errors("README.md", stale, expected=31)
    assert len(errors) == 1, "the 3 contracts must not be compared; the 26 checks must be"
    assert "states 26 Kubernetes static checks" in errors[0]

    # Phrase first, count second — the other direction the documents use.
    phrase_first = k8s_static_check_count_errors(
        "README.md", "静态检查指`tests/deploy/test_k8s_manifests.py`（26 项，全部离线）。", expected=31
    )
    assert len(phrase_first) == 1
    assert "states 26 Kubernetes static checks" in phrase_first[0]

    # A count separated from the phrase by another count is not bound to it.
    unbound = "静态检查指 5 项资源的清单（8 项）。"
    assert k8s_static_check_count_errors("README.md", unbound, expected=31) == []


def test_a_sentence_ending_in_a_number_is_still_a_sentence_boundary():
    """`(?<!\\d)` blocked the split after any digit, so a numeric sentence ran on.

    `Kubernetes uses manifest schema 1.5. The frontend is covered by 12 static
    checks.` reported the frontend's 12 as a Kubernetes count, because the period
    after `1.5` was refused.
    """
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    document = "Kubernetes uses manifest schema 1.5. The frontend is covered by 12 static checks."
    assert k8s_static_check_count_errors("README.md", document, expected=31) == []
    assert not _k8s_claim_is_in_scope(document)

    stale = "Kubernetes uses manifest schema 1.5. The k8s manifests are covered by 26 static checks."
    errors = k8s_static_check_count_errors("README.md", stale, expected=31)
    assert len(errors) == 1
    assert "states 26 Kubernetes static checks" in errors[0]

    # The decimal itself is untouched: a claim with a version in it is still checked.
    with_version = "The Kubernetes manifests are covered by 26 static checks and run on schema 1.5."
    assert len(k8s_static_check_count_errors("README.md", with_version, expected=31)) == 1


def test_a_lazy_continuation_only_applies_to_a_quoted_paragraph():
    """`> ## heading` followed by prose is two blocks, not one lazy paragraph."""
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    for document in (
        "> ## Kubernetes manifests\nThe frontend is covered by 12 static checks.",
        "> | a | b |\nThe frontend is covered by 12 static checks.",
    ):
        assert k8s_static_check_count_errors("README.md", document, expected=31) == [], document

    # An unmarked line after a quoted list item is that item's lazy paragraph, so the
    # window is one; what keeps the frontend's 12 out of scope is the sentence split.
    item_then_prose = "> - The Kubernetes manifests.\nThe frontend is covered by 12 static checks.\n"
    assert k8s_static_check_count_errors("README.md", item_then_prose, expected=31) == []

    # A genuinely quoted paragraph still absorbs the unmarked line.
    quoted_paragraph = "> The Kubernetes manifests are covered by 26 static\nchecks.\n"
    assert len(k8s_static_check_count_errors("README.md", quoted_paragraph, expected=31)) == 1

    # An indented continuation *is* the quoted list item's own content, so a claim
    # in it is in the item's scope — the subject and the count are one claim, and
    # widening it to read the 12 as Kubernetes's would be a fabricated error only
    # if the item had not named Kubernetes, which here it does.
    quoted_item = "> - The Kubernetes manifests\n>   are covered by 12 static checks.\n"
    errors = k8s_static_check_count_errors("README.md", quoted_item, expected=31)
    assert len(errors) == 1, "the item names Kubernetes, so its 12 is a Kubernetes count"
    assert "states 12 Kubernetes static checks" in errors[0]

    # The unindented form joins the same way, because a list item's paragraph is lazy
    # about its continuation indent. What keeps the next sentence out of the item's
    # scope is the *sentence split*, not the window: two sentences are two clauses, and
    # the second does not name Kubernetes. That is the bound this case now rests on,
    # so both the punctuated and unpunctuated forms are asserted below rather than
    # assumed — see `test_a_list_items_paragraph_is_lazy_about_its_continuation`.
    unindented = "> - The Kubernetes manifests.\n> The frontend is covered by 12 static checks.\n"
    assert k8s_static_check_count_errors("README.md", unindented, expected=31) == []


def test_a_setext_heading_closes_before_the_following_paragraph():
    """The underline marks the line above it as a heading, so the window ends there."""
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    for document in (
        "Kubernetes manifests\n--------------------\nThe frontend is covered by 12 static checks.",
        "Kubernetes manifests\n====================\nThe frontend is covered by 12 static checks.",
    ):
        assert k8s_static_check_count_errors("README.md", document, expected=31) == [], document
        assert not _k8s_claim_is_in_scope(document), f"fixture must be out of scope: {document}"

    stale = "Kubernetes manifests\n--------------------\nThe Kubernetes manifests are covered by 26 static checks."
    assert len(k8s_static_check_count_errors("README.md", stale, expected=31)) == 1

    # A stale claim inside the Setext heading itself is still caught.
    in_heading = "The Kubernetes manifests are covered by 26 static checks\n--------------------\n\nBody prose.\n"
    assert len(k8s_static_check_count_errors("README.md", in_heading, expected=31)) == 1


def test_claim_windows_close_a_setext_heading_before_the_following_paragraph():
    from scripts.check_repo_consistency import _markdown_claim_windows

    document = "heading text one\n-------------\nheading text two\n=============\nbody prose\n"
    assert _markdown_claim_windows(document) == ["heading text one", "heading text two", "body prose"]


def test_a_phrase_first_chinese_count_must_not_modify_another_noun():
    """`静态检查覆盖 5 项资源` counts resources, not checks.

    The phrase-first alternative accepted the next `N 项` whatever it counted, so
    an ordinary sentence about how many resources the checks cover failed the
    repository consistency job.
    """
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    for document in (
        "Kubernetes 静态检查覆盖 5 项资源。",
        "`deploy/k8s/` 的 31 项静态检查覆盖 5 项资源。",
    ):
        assert k8s_static_check_count_errors("README.md", document, expected=31) == [], document

    # A bare tally after the phrase is still read as a check count: nothing in
    # `静态检查共 5 项` takes the number, and that is the form documents use.
    bare = k8s_static_check_count_errors("README.md", "Kubernetes 静态检查共 26 项。", expected=31)
    assert len(bare) == 1
    assert "states 26 Kubernetes static checks" in bare[0]

    # The parenthetical form the current documentation actually uses stays bound.
    real = "静态检查指`tests/deploy/test_k8s_manifests.py`（26 项，全部离线）。"
    errors = k8s_static_check_count_errors("README.md", real, expected=31)
    assert len(errors) == 1
    assert "states 26 Kubernetes static checks" in errors[0]


def test_a_quoted_list_continuation_keeps_its_indentation():
    """The `>` marker occupies column one, so the item's indent is what follows it.

    Reading indentation off the raw line made every quoted line look unindented, so
    `> - ...26 static\n>   checks.` split into two windows and the stale count was
    never checked.
    """
    from scripts.check_repo_consistency import _markdown_claim_windows, k8s_static_check_count_errors

    stale = "> - The Kubernetes manifests are covered by 26 static\n>   checks.\n"
    errors = k8s_static_check_count_errors("README.md", stale, expected=31)
    assert len(errors) == 1
    assert "states 26 Kubernetes static checks" in errors[0]

    current = "> - The Kubernetes manifests are covered by 31 static\n>   checks.\n"
    assert k8s_static_check_count_errors("README.md", current, expected=31) == []

    assert _markdown_claim_windows(stale) == ["- The Kubernetes manifests are covered by 26 static checks."]

    # The unquoted wrap of the same item is unaffected.
    plain = "- The Kubernetes manifests are covered by 26 static\n  checks.\n"
    assert len(k8s_static_check_count_errors("README.md", plain, expected=31)) == 1


def test_a_blockquote_interrupts_a_preceding_paragraph():
    """Entering a quote starts a new block; it does not continue the paragraph above.

    Both bodies are paragraphs, so the join rule alone let the quoted frontend count
    borrow the preceding paragraph's Kubernetes subject.
    """
    from scripts.check_repo_consistency import _markdown_claim_windows, k8s_static_check_count_errors

    document = "The Kubernetes manifests are documented here\n> The frontend is covered by 12 static checks.\n"
    assert k8s_static_check_count_errors("README.md", document, expected=31) == []
    assert not _k8s_claim_is_in_scope(document)
    assert _markdown_claim_windows(document) == [
        "The Kubernetes manifests are documented here",
        "The frontend is covered by 12 static checks.",
    ]

    # The reverse direction is lazy continuation and stays one window, so this
    # asymmetry is not "a quote always ends the window".
    lazy = "> The Kubernetes manifests are covered by 26 static\nchecks.\n"
    assert _markdown_claim_windows(lazy) == ["The Kubernetes manifests are covered by 26 static checks."]
    assert len(k8s_static_check_count_errors("README.md", lazy, expected=31)) == 1


def test_a_phrase_first_tally_must_run_out_inside_its_clause():
    """ "Not Han next" is not "bare": the counted noun can be one gap further on.

    `(?![CJK])` accepted `5 项 资源` and `5 项 YAML 资源`, because the space between
    `项` and the noun is not Han either. A bare tally is now a positive test: what
    follows the count must be a closing bracket or the end of the clause.
    """
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    for document in (
        "Kubernetes 静态检查覆盖 5 项 资源。",
        "Kubernetes 静态检查覆盖 5 项 YAML 资源。",
        "Kubernetes 静态检查覆盖 5 项资源。",
        "Kubernetes 静态检查覆盖 5 项 限制，由 31 项静态检查验证。",
    ):
        assert k8s_static_check_count_errors("README.md", document, expected=31) == [], document

    # A tally that really does run out is still compared, with or without a bracket.
    for document in (
        "Kubernetes 静态检查共 26 项。",
        "Kubernetes 静态检查共 26 项）。",
        "静态检查指`tests/deploy/test_k8s_manifests.py`（26 项，全部离线）。",
    ):
        errors = k8s_static_check_count_errors("README.md", document, expected=31)
        assert len(errors) == 1, document
        assert "states 26 Kubernetes static checks" in errors[0], document


def test_entering_a_nested_blockquote_starts_a_new_block():
    """Quote depth is what separates `> text` from `> > text`; a boolean cannot."""
    from scripts.check_repo_consistency import _markdown_claim_windows, k8s_static_check_count_errors

    document = "> The Kubernetes manifests are documented here\n> > The frontend is covered by 12 static checks.\n"
    assert k8s_static_check_count_errors("README.md", document, expected=31) == []
    assert not _k8s_claim_is_in_scope(document)
    assert _markdown_claim_windows(document) == [
        "The Kubernetes manifests are documented here",
        "The frontend is covered by 12 static checks.",
    ]

    # The inner block's own stale claim is still checked.
    stale = "> The Kubernetes manifests are documented here\n> > The Kubernetes manifests are covered by 26 static checks.\n"
    errors = k8s_static_check_count_errors("README.md", stale, expected=31)
    assert len(errors) == 1
    assert "states 26 Kubernetes static checks" in errors[0]

    # Wrapping at the same depth is one window, which is what the depth check must
    # not break.
    same_depth = "> > The Kubernetes manifests are covered by 26 static\n> > checks.\n"
    assert _markdown_claim_windows(same_depth) == ["The Kubernetes manifests are covered by 26 static checks."]
    assert len(k8s_static_check_count_errors("README.md", same_depth, expected=31)) == 1

    # And the unmarked lazy continuation out of a quote is unaffected.
    lazy = "> The Kubernetes manifests are covered by 26 static\nchecks.\n"
    assert _markdown_claim_windows(lazy) == ["The Kubernetes manifests are covered by 26 static checks."]
    assert len(k8s_static_check_count_errors("README.md", lazy, expected=31)) == 1


def test_inline_delimiters_are_transparent_to_the_tally_terminator():
    """`**26 项**` is the same claim as `26 项`; the closing `*` must not hide it.

    A terminator that stopped at the closing delimiter returned no error for the
    stale count, which is the worse failure: nothing said the claim went unverified.
    """
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    for document, stated in (
        ("Kubernetes 静态检查共 **26 项**。", "states 26"),
        ("Kubernetes 静态检查共 `26 项`。", "states 26"),
        ("Kubernetes 静态检查共 _26 项_。", "states 26"),
    ):
        errors = k8s_static_check_count_errors("README.md", document, expected=31)
        assert len(errors) == 1, document
        assert stated in errors[0], document

    # Delimiters are transparent, not a loophole: a noun still follows them.
    for document in (
        "Kubernetes 静态检查覆盖 **5 项** 资源。",
        "Kubernetes 静态检查覆盖 **5 项资源**。",
        "Kubernetes 静态检查覆盖 `5 项 YAML` 资源。",
    ):
        assert k8s_static_check_count_errors("README.md", document, expected=31) == [], document

    # The current count is still accepted through the same delimiters.
    assert k8s_static_check_count_errors("README.md", "Kubernetes 静态检查共 **31 项**。", expected=31) == []


def test_an_indented_nested_quote_marker_is_still_a_nested_quote():
    """CommonMark allows up to three spaces before a nested `>`, so `>  > text` is depth 2.

    The depth regex allowed at most one whitespace character between markers, read
    the line as depth 1, and merged it into the outer paragraph.
    """
    from scripts.check_repo_consistency import _markdown_claim_windows, k8s_static_check_count_errors

    for separator in (" ", "  ", "   "):
        document = (
            f"> Kubernetes manifests are documented here\n>{separator}> The frontend is covered by 12 static checks.\n"
        )
        assert k8s_static_check_count_errors("README.md", document, expected=31) == [], document
        assert not _k8s_claim_is_in_scope(document), document
        assert _markdown_claim_windows(document) == [
            "Kubernetes manifests are documented here",
            "The frontend is covered by 12 static checks.",
        ]

    # More than three spaces is an indented code block, not a nested quote, and the
    # line stays inside the outer quote's paragraph.
    code = "> Kubernetes manifests are documented here\n>     not a nested quote\n"
    assert _markdown_claim_windows(code) == [
        "Kubernetes manifests are documented here not a nested quote",
    ]

    # The nested block's own stale claim, the same-depth wrap, and the quoted list
    # continuation are all unaffected by the deeper parsing.
    stale = (
        "> Kubernetes manifests are documented here\n>  > The Kubernetes manifests are covered by 26 static checks.\n"
    )
    assert len(k8s_static_check_count_errors("README.md", stale, expected=31)) == 1
    same_depth = "> > The Kubernetes manifests are covered by 26 static\n> > checks.\n"
    assert _markdown_claim_windows(same_depth) == ["The Kubernetes manifests are covered by 26 static checks."]
    quoted_item = "> - The Kubernetes manifests are covered by 26 static\n>   checks.\n"
    assert _markdown_claim_windows(quoted_item) == ["- The Kubernetes manifests are covered by 26 static checks."]


def test_whitespace_before_the_tally_terminator_is_transparent():
    """`共 26 项 ）` is the bare form with a space in it, and must be compared."""
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    for document, stated in (
        ("Kubernetes 静态检查 (共 26 项 )", "states 26"),
        ("Kubernetes 静态检查共 26 项 。", "states 26"),
        ("Kubernetes 静态检查共 **26 项** ）", "states 26"),
        ("Kubernetes 静态检查 (共 31 项 )", None),
    ):
        errors = k8s_static_check_count_errors("README.md", document, expected=31)
        if stated is None:
            assert errors == [], document
        else:
            assert len(errors) == 1, document
            assert stated in errors[0], document

    # Transparency is not a loophole: a noun after the whitespace still wins.
    for document in (
        "Kubernetes 静态检查覆盖 5 项 资源。",
        "Kubernetes 静态检查覆盖 **5 项** 资源。",
        "Kubernetes 静态检查覆盖 5 项 YAML 资源。",
    ):
        assert k8s_static_check_count_errors("README.md", document, expected=31) == [], document


def test_a_deeply_indented_quoted_list_continuation_is_measured_from_the_source_column():
    """Indentation is the content's column, not what the quote prefix happened to eat.

    The prefix match caps how much whitespace it consumes, so a deeply indented
    continuation still carries leading spaces on the body. Measuring only the
    prefix's share made it look no more indented than the item it belongs to, and
    the item was split in two.
    """
    from scripts.check_repo_consistency import _markdown_claim_windows, k8s_static_check_count_errors

    stale = ">   - The Kubernetes manifests are covered by 26 static\n>     checks.\n"
    assert _markdown_claim_windows(stale) == ["- The Kubernetes manifests are covered by 26 static checks."]
    errors = k8s_static_check_count_errors("README.md", stale, expected=31)
    assert len(errors) == 1
    assert "states 26 Kubernetes static checks" in errors[0]

    current = ">   - The Kubernetes manifests are covered by 31 static\n>     checks.\n"
    assert k8s_static_check_count_errors("README.md", current, expected=31) == []

    # The shallower wraps, and the unquoted one, are unaffected.
    for document in (
        "> - The Kubernetes manifests are covered by 26 static\n>   checks.",
        "- The Kubernetes manifests are covered by 26 static\n  checks.",
        "- The Kubernetes manifests are covered by 26 static\nchecks.",
    ):
        assert _markdown_claim_windows(document) == ["- The Kubernetes manifests are covered by 26 static checks."]
        assert len(k8s_static_check_count_errors("README.md", document, expected=31)) == 1


def test_a_list_items_paragraph_is_lazy_about_its_continuation():
    """CommonMark: a list item's paragraph may drop its continuation's indent.

    This is the same shape as an earlier finding — an item naming Kubernetes followed
    by a paragraph about another suite — and the two cannot both hold: laziness is a
    property of the *paragraph*, not of how far the continuation was indented, so
    there is no measurement that separates `- …26 static\\nchecks.` (one paragraph)
    from `>- …\\n> The frontend…` (also one paragraph, under the same rule).

    The spec-faithful reading is chosen, because it is the reading a reader gets and
    because it *widens the guard's coverage* rather than narrowing it. The bound that
    keeps two unrelated claims apart is now the sentence split plus the clause-level
    subject, so both forms are asserted: punctuated, they stay apart; unpunctuated,
    they are one clause and the number is compared, which is reported as the honest
    consequence rather than hidden.
    """
    from scripts.check_repo_consistency import _markdown_claim_windows, k8s_static_check_count_errors

    # The reported case: an unpunctuated wrap inside a list item is one paragraph,
    # so the stale count is now caught.
    stale = "- The Kubernetes manifests are covered by 26 static\nchecks.\n"
    assert _markdown_claim_windows(stale) == ["- The Kubernetes manifests are covered by 26 static checks."]
    errors = k8s_static_check_count_errors("README.md", stale, expected=31)
    assert len(errors) == 1
    assert "states 26 Kubernetes static checks" in errors[0]
    assert k8s_static_check_count_errors("README.md", stale.replace("26 static", "31 static"), expected=31) == []

    # The same sentence with its subject restored is one clause, so the 12 is compared
    # against the manifests module. This is a *reported* false positive under the
    # earlier strict rule; under laziness it is the spec's answer, and it is recorded
    # here rather than left to be discovered.
    unpunctuated = ">- The Kubernetes manifests\n> The frontend is covered by 12 static checks.\n"
    assert _markdown_claim_windows(unpunctuated) == [
        "- The Kubernetes manifests The frontend is covered by 12 static checks.",
    ]
    assert len(k8s_static_check_count_errors("README.md", unpunctuated, expected=31)) == 1

    # Two *sentences* are two clauses whatever the indentation, and the second does not
    # name Kubernetes — this is the form real prose takes, and it stays out of scope.
    punctuated = ">- The Kubernetes manifests.\n> The frontend is covered by 12 static checks.\n"
    assert _markdown_claim_windows(punctuated) == [
        "- The Kubernetes manifests. The frontend is covered by 12 static checks.",
    ]
    assert k8s_static_check_count_errors("README.md", punctuated, expected=31) == []
    assert not _k8s_claim_is_in_scope(punctuated)

    # An ordered item behaves the same way, and a block element still breaks out.
    assert _markdown_claim_windows("1. a list item\nits continuation\n") == ["1. a list item its continuation"]
    assert _markdown_claim_windows("- a list item\n- a second item\n") == ["- a list item", "- a second item"]


def test_a_list_items_paragraph_continues_at_any_indentation():
    """The removed content-column test, replaced by what actually bounds it.

    Rounds 9–12 required a continuation to reach the list item's content column.
    CommonMark's list-item laziness rule does not: `- …26 static\\nchecks.` is one
    paragraph. The rule that replaced it is that a paragraph continues a paragraph,
    and the bounds that keep unrelated claims apart are the sentence split and the
    clause-level subject — asserted in
    `test_a_list_items_paragraph_is_lazy_about_its_continuation`.
    """
    from scripts.check_repo_consistency import _markdown_claim_windows

    for opener, continuation in (
        ("> - a", ">   b"),
        (">- a", "> b"),
        (">   - a", ">     b"),
        ("- a", "  b"),
        ("- a", " b"),
        ("1. a", "   b"),
    ):
        document = f"{opener}\n{continuation}\n"
        assert _markdown_claim_windows(document) == [f"{opener.lstrip('> ')} {continuation.lstrip('> ')}"], document

    # A block element still ends the item, so laziness did not become "everything
    # joins": headings, rows, setext underlines and blank lines all still break.
    for document, expected in (
        ("- a\n\n  b", ["- a", "b"]),
        ("- a\n## heading", ["- a", "## heading"]),
        ("- a\n| x |", ["- a", "| x |"]),
        ("- a\n---", ["- a"]),
        ("> - a\n> ## heading", ["- a", "## heading"]),
    ):
        assert _markdown_claim_windows(f"{document}\n") == expected, document


def test_indentation_is_measured_inside_the_quote_container():
    """A nested quote's prefix width varies between lines; the measure must not.

    `> > - item` and `>>   continuation` are the same depth-2 quote written with
    different optional spaces. Comparing source columns compared the two prefixes
    against each other — opener column 6, continuation column 5 — and split a claim
    that was one window before. Indentation is now counted from where the container's
    own prefix ends.
    """
    from scripts.check_repo_consistency import _markdown_claim_windows, k8s_static_check_count_errors

    # Both orderings of the optional space, and the loose-both-ways version.
    for opener, continuation in (
        (">> - The Kubernetes manifests are covered by 26 static", ">>   checks."),
        ("> > - The Kubernetes manifests are covered by 26 static", ">>   checks."),
        (">> - The Kubernetes manifests are covered by 26 static", "> >   checks."),
    ):
        document = f"{opener}\n{continuation}\n"
        assert _markdown_claim_windows(document) == ["- The Kubernetes manifests are covered by 26 static checks."], (
            document
        )
        errors = k8s_static_check_count_errors("README.md", document, expected=31)
        assert len(errors) == 1, document
        assert "states 26 Kubernetes static checks" in errors[0], document
        current = document.replace("26 static", "31 static")
        assert k8s_static_check_count_errors("README.md", current, expected=31) == [], document

    # The container's own prefix width no longer decides anything: a nested quote and
    # its paragraph, however it is written, is one paragraph.
    for opener, continuation in (
        ("> - a", ">   b"),
        (">- a", "> b"),
        (">   - a", ">     b"),
        ("- a", "  b"),
        ("- a", " b"),
        ("1. a", "   b"),
    ):
        document = f"{opener}\n{continuation}\n"
        assert len(_markdown_claim_windows(document)) == 1, document


def test_four_spaces_before_a_nested_quote_marker_is_still_a_nested_quote():
    """Three of those spaces are the inner marker's indentation, the fourth is the
    outer marker's optional space — so the bound between markers is four, not three.

    Reading `>    > text` as depth 1 merged the inner paragraph into the outer one and
    let its count borrow the outer subject.
    """
    from scripts.check_repo_consistency import _markdown_claim_windows, k8s_static_check_count_errors

    for separator in (" ", "  ", "   ", "    "):
        document = (
            f"> Kubernetes manifests are documented here\n>{separator}> The frontend is covered by 12 static checks.\n"
        )
        assert k8s_static_check_count_errors("README.md", document, expected=31) == [], document
        assert not _k8s_claim_is_in_scope(document), document
        assert _markdown_claim_windows(document) == [
            "Kubernetes manifests are documented here",
            "The frontend is covered by 12 static checks.",
        ], document

    # Five spaces is an indented code block, not a nested quote: the marker is not
    # there, so the line is an ordinary continuation of the outer paragraph.
    code = "> Kubernetes manifests are documented here\n>     > not a nested quote\n"
    assert _markdown_claim_windows(code) == [
        "Kubernetes manifests are documented here > not a nested quote",
    ]

    # A stale claim inside the nested block is still caught.
    stale = (
        "> Kubernetes manifests are documented here\n>    > The Kubernetes manifests are covered by 26 static checks.\n"
    )
    errors = k8s_static_check_count_errors("README.md", stale, expected=31)
    assert len(errors) == 1
    assert "states 26 Kubernetes static checks" in errors[0]


def test_a_generic_manifest_noun_is_not_a_kubernetes_subject():
    """`manifest` is a generic noun, so naming it is not naming the K8s manifests.

    `The frontend manifest is covered by 12 static checks.` was compared against the
    K8s module's size. The word was in the subject list to cover an English phrasing
    the current documents no longer use — every real claim names `deploy/k8s/`,
    `k8s`, or the test module in the same clause.
    """
    from scripts.check_repo_consistency import k8s_static_check_count_errors

    for document in (
        "The frontend manifest is covered by 12 static checks.",
        "The Python package manifest has 12 static checks.",
        "The manifests and their 31 static checks",
        # A wrong number is still ignored for the same reason: out of scope beats wrong.
        "The frontend manifest is covered by 999 static checks.",
    ):
        assert k8s_static_check_count_errors("README.md", document, expected=31) == [], document
        assert not _k8s_claim_is_in_scope(document), f"fixture must be out of scope: {document}"

    # A Kubernetes-qualified reference is still in scope, by either route.
    for document in (
        "The Kubernetes manifests are covered by 26 static checks.",
        "The k8s manifests are covered by 26 static checks.",
        "The manifests are covered by 26 static checks in `deploy/k8s/`.",
        "The manifests and their 26 static checks (`tests/deploy/test_k8s_manifests.py`, offline).",
    ):
        errors = k8s_static_check_count_errors("README.md", document, expected=31)
        assert len(errors) == 1, document
        assert "states 26 Kubernetes static checks" in errors[0], document


def test_the_nested_marker_allowance_is_measured_in_columns():
    """Tabs are expanded to CommonMark's four-column stops before anything is counted.

    Counting characters gets this wrong in both directions: four tabs carry the line
    to column 16 and are not a nested marker, while a single tab reaches column 4 and
    *is* one. Excluding tabs outright fixed the first case and broke the second.
    """
    from scripts.check_repo_consistency import _markdown_claim_windows, k8s_static_check_count_errors

    # Four tabs reach column 16: not a marker, so this continues the quoted paragraph
    # and the stale count on it is still compared.
    document = "> The Kubernetes manifests are covered by\n>\t\t\t\t> 26 static checks.\n"
    assert _markdown_claim_windows(document) == [
        "The Kubernetes manifests are covered by > 26 static checks.",
    ]
    errors = k8s_static_check_count_errors("README.md", document, expected=31)
    assert len(errors) == 1
    assert "states 26 Kubernetes static checks" in errors[0]
    assert k8s_static_check_count_errors("README.md", document.replace("26 static", "31 static"), expected=31) == []

    # One tab reaches column 4, leaving two permitted indentation columns before the
    # second marker: a depth-2 quote, which interrupts the outer paragraph.
    nested = "> Kubernetes manifests are documented here\n>\t> The frontend is covered by 12 static checks.\n"
    assert _markdown_claim_windows(nested) == [
        "Kubernetes manifests are documented here",
        "The frontend is covered by 12 static checks.",
    ]
    assert k8s_static_check_count_errors("README.md", nested, expected=31) == []

    # The space forms are unchanged: 1..4 columns nest, 5 does not.
    assert _markdown_claim_windows("> a\n> > b\n") == ["a", "b"]
    assert _markdown_claim_windows("> a\n>  > b\n") == ["a", "b"]
    assert _markdown_claim_windows("> a\n>   > b\n") == ["a", "b"]
    assert _markdown_claim_windows("> a\n>    > b\n") == ["a", "b"]
    assert _markdown_claim_windows("> a\n>     > b\n") == ["a > b"]


def test_quote_markers_may_be_omitted_on_a_continuation_line():
    """CommonMark lets a wrapped paragraph omit some leading `>` markers.

    `>>> wrapped by 26 static` / `> checks.` is one paragraph inside the depth-3
    quote, so requiring the depth to be unchanged rejected it and split the claim.
    Depth may be *omitted*; only entering a deeper quote starts a block, because a
    quote marker begins one.
    """
    from scripts.check_repo_consistency import _markdown_claim_windows, k8s_static_check_count_errors

    for opener, continuation in (
        (">>> The Kubernetes manifests are covered by 26 static", "> checks."),
        (">> The Kubernetes manifests are covered by 26 static", "> checks."),
        (">> The Kubernetes manifests are covered by 26 static", ">> checks."),
        (">> The Kubernetes manifests are covered by 26 static", "checks."),
    ):
        document = f"{opener}\n{continuation}\n"
        assert _markdown_claim_windows(document) == [
            "The Kubernetes manifests are covered by 26 static checks.",
        ], document
        errors = k8s_static_check_count_errors("README.md", document, expected=31)
        assert len(errors) == 1, document
        assert "states 26 Kubernetes static checks" in errors[0], document
        current = document.replace("26 static", "31 static")
        assert k8s_static_check_count_errors("README.md", current, expected=31) == [], document

    # Entering a deeper quote still begins a block, and an unmarked line still ends a
    # quote — omission is one-directional.
    deeper = "> The Kubernetes manifests are documented here\n>> The frontend is covered by 12 static checks.\n"
    assert _markdown_claim_windows(deeper) == [
        "The Kubernetes manifests are documented here",
        "The frontend is covered by 12 static checks.",
    ]
    assert k8s_static_check_count_errors("README.md", deeper, expected=31) == []


def test_effective_quote_depth_survives_a_marker_restore():
    """Omit markers, then bring some back, and the paragraph is still one block.

    `open_depth` was replaced with each line's *written* depth, so after the reduced
    middle line `> static` the restored `>> checks.` looked like a deeper quote and
    split the paragraph. The comparison is against the paragraph's effective
    container depth — the deepest marker it has been written with — which is what the
    CommonMark example 251 shape needs.
    """
    from scripts.check_repo_consistency import _markdown_claim_windows, k8s_static_check_count_errors

    for document in (
        ">>> The Kubernetes manifests are covered by 26\n> static\n>> checks.\n",
        ">>> The Kubernetes manifests are covered by 26\n> static\n> checks.\n",
        ">> The Kubernetes manifests are covered by 26\n> static\n>> checks.\n",
    ):
        assert _markdown_claim_windows(document) == [
            "The Kubernetes manifests are covered by 26 static checks.",
        ], document
        errors = k8s_static_check_count_errors("README.md", document, expected=31)
        assert len(errors) == 1, document
        assert "states 26 Kubernetes static checks" in errors[0], document
        assert k8s_static_check_count_errors("README.md", document.replace("26", "31"), expected=31) == [], document

    # Only *entering* a deeper quote begins a block: a line whose depth exceeds the
    # paragraph's effective depth still starts one, so this is not "any depth joins".
    deeper = "> The Kubernetes manifests are documented here\n>> The frontend is covered by 12 static checks.\n"
    assert _markdown_claim_windows(deeper) == [
        "The Kubernetes manifests are documented here",
        "The frontend is covered by 12 static checks.",
    ]
    assert k8s_static_check_count_errors("README.md", deeper, expected=31) == []

    # A *deeper* line than the paragraph's effective depth starts a new block, so this
    # is not "any depth joins". Effective depth resets when the previous block is
    # flushed, so the comparison is against this paragraph's container and not a
    # carry-over from an earlier one.
    new_block = ">>> The Kubernetes manifests are covered by 26 static checks.\n>>>> The frontend is covered by 12 static checks.\n"
    assert _markdown_claim_windows(new_block) == [
        "The Kubernetes manifests are covered by 26 static checks.",
        "The frontend is covered by 12 static checks.",
    ]


def test_fully_unmarked_lazy_lines_keep_quote_membership():
    """Any number of markers may be omitted, including all of them.

    `>>> …26` / `static` / `>> checks.` is one paragraph: the middle line drops every
    marker, and the third brings some back. Clearing quote membership on the unmarked
    line made the restored `>>` look like a quote being entered and split the claim.
    """
    from scripts.check_repo_consistency import _markdown_claim_windows, k8s_static_check_count_errors

    for document in (
        ">>> The Kubernetes manifests are covered by 26\nstatic\n>> checks.\n",
        ">>> The Kubernetes manifests are covered by 26\nstatic\n> checks.\n",
        ">>> The Kubernetes manifests are covered by 26\nstatic\nchecks.\n",
    ):
        assert _markdown_claim_windows(document) == [
            "The Kubernetes manifests are covered by 26 static checks.",
        ], document
        errors = k8s_static_check_count_errors("README.md", document, expected=31)
        assert len(errors) == 1, document
        assert "states 26 Kubernetes static checks" in errors[0], document
        assert k8s_static_check_count_errors("README.md", document.replace("26", "31"), expected=31) == [], document

    # Membership is retained, not manufactured: entering a quote from plain prose
    # still begins a block, and a deeper line still begins one.
    for document in (
        "The Kubernetes manifests are documented here\n> The frontend is covered by 12 static checks.",
        "> The Kubernetes manifests are documented here\n>> The frontend is covered by 12 static checks.",
    ):
        assert k8s_static_check_count_errors("README.md", document, expected=31) == [], document
        assert not _k8s_claim_is_in_scope(document), document


def test_ordered_list_markers_may_hold_up_to_nine_digits():
    """CommonMark's list-item rule allows one to nine digits, not one to three.

    `1000) …` was not recognised as a list item, so it and the next item merged into
    one window and the frontend count borrowed the Kubernetes subject.
    """
    from scripts.check_repo_consistency import _markdown_claim_windows, k8s_static_check_count_errors

    for first, second in (("1000)", "1001)"), ("100)", "200)"), ("1.", "2."), ("123456789)", "123456790)")):
        document = (
            f"{first} Kubernetes manifests are documented here\n{second} The frontend is covered by 12 static checks.\n"
        )
        assert _markdown_claim_windows(document) == [
            f"{first} Kubernetes manifests are documented here",
            f"{second} The frontend is covered by 12 static checks.",
        ], document
        assert k8s_static_check_count_errors("README.md", document, expected=31) == [], document

    # Ten digits is not a marker, so that line is paragraph text and continues the
    # preceding item — which is the honest reading, not a merge of two list items.
    document = "1. Kubernetes manifests are documented here\n0123456789) The frontend is covered by 12 static checks.\n"
    assert _markdown_claim_windows(document) == [
        "1. Kubernetes manifests are documented here 0123456789) The frontend is covered by 12 static checks.",
    ]


def test_an_ordered_marker_interrupts_a_paragraph_only_when_it_starts_at_one():
    """CommonMark: a bullet may interrupt a paragraph; an ordered marker only at 1.

    `covered by\\n1000) 26 static checks.` is paragraph continuation text, so the
    guard must keep it in one window and compare the count. Reading the marker as a
    block start split the claim and the stale count escaped.
    """
    from scripts.check_repo_consistency import _markdown_claim_windows, k8s_static_check_count_errors

    for marker in ("1000)", "123456789)"):
        document = f"The Kubernetes manifests are covered by\n{marker} 26 static checks.\n"
        assert _markdown_claim_windows(document) == [
            f"The Kubernetes manifests are covered by {marker} 26 static checks.",
        ], document
        errors = k8s_static_check_count_errors("README.md", document, expected=31)
        assert len(errors) == 1, document
        assert "states 26 Kubernetes static checks" in errors[0], document

    # The parenthesis form keeps the marker inside one sentence. The period form
    # `4.` is also a sentence boundary, so it splits at the period rather than at the
    # marker — either way the count is not compared against the wrong subject.
    period_form = "The Kubernetes manifests are covered by\n4. The frontend is covered by 12 static checks.\n"
    assert k8s_static_check_count_errors("README.md", period_form, expected=31) == []

    # A marker that does interrupt — a bullet, or an ordered marker at 1 — starts a
    # block, so the paragraph's subject does not reach the next line.
    for marker in ("-", "1)", "1."):
        document = f"The Kubernetes manifests are covered by\n{marker} The frontend is covered by 12 static checks.\n"
        assert len(_markdown_claim_windows(document)) == 2, document
        assert k8s_static_check_count_errors("README.md", document, expected=31) == [], document

    # A marker after another list item is a sibling item, whatever its number: the
    # interruption rule does not apply and each item is its own window.
    for document in ("1000) a\n1001) b\n", "1. a\n2. b\n", "- a\n- b\n"):
        assert _markdown_claim_windows(document) == document.strip().splitlines(), document


def test_list_context_survives_a_lazy_continuation():
    """A lazy line makes the item's paragraph look plain; the sibling still splits.

    `1000) …` / `continuation` / `1001) …`: the middle line turns `open_block` into
    `"paragraph"`, so the start-at-1 interruption rule would have read the sibling
    marker as paragraph text and merged the two items, letting the frontend's count
    borrow the Kubernetes subject. List membership is tracked separately.
    """
    from scripts.check_repo_consistency import _markdown_claim_windows, k8s_static_check_count_errors

    document = "1000) Kubernetes manifests are documented here\ncontinuation\n1001) The frontend is covered by 12 static checks.\n"
    assert _markdown_claim_windows(document) == [
        "1000) Kubernetes manifests are documented here continuation",
        "1001) The frontend is covered by 12 static checks.",
    ], "the lazy line joins the item; the sibling marker still splits"
    assert k8s_static_check_count_errors("README.md", document, expected=31) == []

    # The start-at-1 rule still applies to a plain paragraph: two high-numbered
    # markers after prose are both paragraph text, so the count is compared.
    plain = "The Kubernetes manifests are covered by\n1000) a\n1001) 26 static checks.\n"
    assert _markdown_claim_windows(plain) == [
        "The Kubernetes manifests are covered by 1000) a 1001) 26 static checks.",
    ]
    assert len(k8s_static_check_count_errors("README.md", plain, expected=31)) == 1

    # Both rules together: an item's lazy paragraph, then a sibling, then another.
    mixed = "1000) The Kubernetes manifests are covered by 26 static\ncontinuation\n1001) a\n1002) b\n"
    assert _markdown_claim_windows(mixed) == [
        "1000) The Kubernetes manifests are covered by 26 static continuation",
        "1001) a",
        "1002) b",
    ]


def test_block_classification_keeps_the_source_indentation():
    """The block patterns all start `^\\s{0,3}`; stripping first defeats the limit.

    `covered by\\n    1) 26 static checks.` is a four-space-indented line, which
    CommonMark makes literal paragraph text, not a list that interrupts. Classifying
    the stripped body read it as `1)` and split the claim.
    """
    from scripts.check_repo_consistency import _markdown_claim_windows, k8s_static_check_count_errors

    for line in ("    1) 26 static checks.", "    - 26 static checks.", "      1000) 26 static checks."):
        document = f"The Kubernetes manifests are covered by\n{line}\n"
        expected = f"The Kubernetes manifests are covered by {line.strip()}"
        assert _markdown_claim_windows(document) == [expected], document
        assert len(k8s_static_check_count_errors("README.md", document, expected=31)) == 1, document

    # Up to three spaces is still a marker, and still interrupts.
    for line in (
        "1) The frontend is covered by 12 static checks.",
        "   1) The frontend is covered by 12 static checks.",
    ):
        document = f"The Kubernetes manifests are covered by\n{line}\n"
        assert len(_markdown_claim_windows(document)) == 2, document
        assert k8s_static_check_count_errors("README.md", document, expected=31) == [], document


def test_all_thematic_break_spellings_are_boundaries():
    """`***` and `___` are thematic breaks too, so they separate paragraphs.

    Only the dash spelling was recognised, so a `***` between two paragraphs let the
    first one's Kubernetes subject reach the second one's count.
    """
    from scripts.check_repo_consistency import _markdown_claim_windows, k8s_static_check_count_errors

    for rule in ("***", "___", "---", "- - -", "* * *", "_ _ _"):
        document = f"Kubernetes manifests are documented here\n{rule}\nThe frontend is covered by 12 static checks.\n"
        assert _markdown_claim_windows(document) == [
            "Kubernetes manifests are documented here",
            "The frontend is covered by 12 static checks.",
        ], document
        assert k8s_static_check_count_errors("README.md", document, expected=31) == [], document

    # Underscores and asterisks are still ordinary paragraph text mid-sentence.
    inline = "The Kubernetes manifests are covered by 26 static checks and use _underscores_ and *stars*."
    assert len(k8s_static_check_count_errors("README.md", inline, expected=31)) == 1

    # A separator is also subject to the three-space limit: four spaces of
    # indentation makes it a code block, not a scenic break, so it stays in the
    # paragraph and the count is compared.
    for rule in ("***", "---", "___"):
        document = f"The Kubernetes manifests are covered by\n    {rule}\n26 static checks.\n"
        assert _markdown_claim_windows(document) == [
            f"The Kubernetes manifests are covered by {rule} 26 static checks.",
        ], document
        assert len(k8s_static_check_count_errors("README.md", document, expected=31)) == 1, document

    # A thematic break is three or more of a *single* marker: two underscores are
    # ordinary text, so the paragraph around them stays one window.
    two = "The Kubernetes manifests are covered by\n__\n26 static checks.\n"
    assert _markdown_claim_windows(two) == ["The Kubernetes manifests are covered by __ 26 static checks."]
    assert len(k8s_static_check_count_errors("README.md", two, expected=31)) == 1


# ── golden-set contract documentation guard ────────────────────────────────


def test_golden_set_contract_required_fields_are_derived_from_the_validator():
    """The guard must derive the field list by executing the validator."""
    from scripts.check_repo_consistency import golden_set_contract_required_fields

    required = golden_set_contract_required_fields()
    assert "sample_id" in required
    assert "annotations" in required
    assert "visual_required" in required
    assert "complexity_label" in required
    assert "corpus_version" in required
    # The provenance sub-fields the validator added are included, which is the
    # whole point: a doc copy of this list is what went stale before.
    assert "annotation.review_status" in required
    assert "annotation.source" in required
    assert "annotation.reviewed_by" in required


def test_contract_doc_block_missing_a_required_field_is_reported():
    """A doc that under-promises the contract must fail, as it did in the wild."""
    from scripts.check_repo_consistency import golden_set_contract_doc_errors

    required = {"sample_id", "annotations", "annotation.review_status", "annotation.reviewed_by"}
    complete = """```text
sample_id
annotations        [{doc_id, chunk_id, text}, ...]
annotation         {annotator, method, annotated_at, source, review_status, reviewed_by, reviewed_at}
```"""
    assert golden_set_contract_doc_errors("doc.md", complete, required) == []

    stale = """```text
sample_id
annotations        [{doc_id, chunk_id, text}, ...]
annotation         {annotator, method, annotated_at, reviewed_by}
```"""
    errors = golden_set_contract_doc_errors("doc.md", stale, required)
    assert len(errors) == 1
    assert "annotation.review_status" in errors[0]


def test_contract_doc_block_is_found_regardless_of_position():
    """Documents carry several fenced blocks; the contract one must be identified."""
    from scripts.check_repo_consistency import golden_set_contract_doc_errors

    required = {"annotation.review_status"}
    document = """# Title

## v1 breakdown

```text
overall
business_type
difficulty
```

## v2 contract

```text
sample_id
annotations
annotation         {annotator, review_status}
```
"""
    assert golden_set_contract_doc_errors("doc.md", document, required) == []


def test_contract_doc_that_omits_the_provenance_block_makes_no_claim():
    from scripts.check_repo_consistency import golden_set_contract_doc_errors

    required = {"annotation.review_status"}
    document = """```text
sample_id
annotations
```"""
    assert golden_set_contract_doc_errors("doc.md", document, required) == []


def test_prose_doc_naming_provenance_must_name_review_status():
    from scripts.check_repo_consistency import golden_set_contract_prose_errors

    assert golden_set_contract_prose_errors("d.md", "nothing relevant here", set()) == []
    stale = "provenance requires `annotator`, `method` and `reviewed_by`."
    errors = golden_set_contract_prose_errors("d.md", stale, set())
    assert len(errors) == 1
    assert "review_status" in errors[0]
    good = "provenance requires `annotator`, `review_status` and `reviewed_by`."
    assert golden_set_contract_prose_errors("d.md", good, set()) == []


def test_the_repository_contract_docs_currently_agree_with_the_validator():
    """The guard's own target documents must pass it."""
    from scripts.check_repo_consistency import check_golden_set_contract_doc_fields

    errors: list[str] = []
    check_golden_set_contract_doc_fields(errors)
    assert errors == []
