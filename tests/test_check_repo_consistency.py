"""Tests for the repository consistency drift guard."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.check_repo_consistency import (
    REQUIRED_AUDIT_AREAS,
    benchmark_artifact_exists,
    benchmark_classification_errors,
    check_docs_index,
    check_documented_offline_commands,
    check_forbidden_current_claims,
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

    text = Path("docs/interview-architecture-baseline.md").read_text(encoding="utf-8")
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


def test_interview_baseline_must_split_exporter_implementation_from_closed_loop():
    from scripts.check_repo_consistency import check_interview_baseline_exporter_split

    errors: list[str] = []
    check_interview_baseline_exporter_split(errors)
    assert errors == []


def test_audit_tracker_requires_open_external_validation():
    from scripts.check_repo_consistency import audit_tracker_errors

    audit = (Path("docs/repository-truth-audit.md")).read_text(encoding="utf-8")
    assert audit_tracker_errors(audit) == []

    closed = audit.replace(
        "[#18](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/18) — **real** retrieval\n"
        "  benchmark execution: open.",
        "[#18](https://github.com/Xander-Xai/Beauty-Industry-RAG-QA-System/issues/18) — **real** retrieval\n"
        "  benchmark execution: closed.",
    )
    assert closed != audit, "fixture must actually change the tracker state"
    assert audit_tracker_errors(closed)


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
    """The interview evidence map must give each capability exactly one row."""
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
    (tmp_path / guard.INTERVIEW_EVIDENCE_MAP).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / guard.INTERVIEW_EVIDENCE_MAP).write_text("# Evidence\n\nNo table here.\n", encoding="utf-8")

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
        "# Interview Evidence Map\n\n"
        "## Classification vocabulary\n\n"
        "| Level | Meaning | Interview-safe framing | Must not say |\n"
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
    assert "docs/interview-evidence-map.md" in EVIDENCE_VOCABULARY_DOCS
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


def test_no_qdrant_runtime_artifact_is_committed():
    from scripts.check_repo_consistency import qdrant_runtime_artifact_exists

    assert qdrant_runtime_artifact_exists() is False


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

    for name in ("docs/interview-evidence-map.md", "docs/repository-truth-audit.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert qdrant_lineage_errors(name, text, artifact_exists=False) == []
