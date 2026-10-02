"""Tests for the repository consistency drift guard."""

from __future__ import annotations

from pathlib import Path

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
    """The default install uses the OTel SDK provider with no exporter."""
    from pathlib import Path

    text = Path("docs/interview-architecture-baseline.md").read_text(encoding="utf-8")
    assert "没有配置任何 exporter" in text
    assert "OTel SDK 未安装或初始化失败" in text


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
    (evidence / "span.json").write_text("{}", encoding="utf-8")
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
