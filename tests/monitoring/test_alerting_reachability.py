"""Reachability contract for the two alerting mechanisms.

This repository contains two threshold engines:

* ``monitoring/prometheus/alerts.yml`` — the canonical alert contract, evaluated
  by an external Prometheus server. Its rules only reference series that
  ``/api/metrics`` really emits.
* ``monitoring/otel_tracer.py::AlertingManager`` — an older *in-process* engine.
  Nothing in the canonical request path constructs it or calls ``check_alerts()``.

The second one is legacy. Keeping it is a deliberate, documented decision rather
than an oversight, so the documentation is allowed to say "not wired". What must
never happen is the opposite drift: the documentation implying that this class is
part of the Prometheus alert contract, or that its rules are the alerting this
project ships.

These tests therefore assert the *reachability* fact. If someone later wires the
class into ``app.py`` / ``api/`` / ``core/``, the test fails and forces the docs,
the audit row and the config story to be reconciled in the same change — which is
the moment a second alert contract would actually become real.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Modules that make up the canonical online request path. If any of them starts
#: referencing the in-process engine, the "legacy, not wired" claim becomes false.
CANONICAL_PATH_MODULES = (
    "app.py",
    "api/routes.py",
    "api/routes_auth.py",
    "api/middleware.py",
    "core/pipeline.py",
    "core/pipeline_context.py",
)

#: Names that would indicate the in-process engine became reachable.
ENGINE_SYMBOLS = ("AlertingManager", "check_alerts", "get_active_alerts", "clear_alert")


def _imported_names(path: Path) -> set[str]:
    """Return every name the module binds, from imports and definitions."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def test_in_process_alerting_manager_is_not_reachable_from_the_canonical_path():
    """No canonical request-path module may reference the in-process engine."""
    offenders: list[str] = []
    for name in CANONICAL_PATH_MODULES:
        path = REPO_ROOT / name
        assert path.is_file(), f"canonical module is missing: {name}"
        bound = _imported_names(path)
        for symbol in ENGINE_SYMBOLS:
            if symbol in bound:
                offenders.append(f"{name}: {symbol}")
    assert not offenders, (
        "monitoring.otel_tracer.AlertingManager is now reachable from the canonical path "
        f"({offenders}). Two consequences must be reconciled in the same change: "
        "docs/repository-truth-audit.md (In-process AlertingManager row + the "
        "'Two alerting mechanisms' section) and the canonical alerting statement in "
        "docs/operations-guide.md and docs/slo-runbook.md."
    )


def test_canonical_alerting_contract_is_the_prometheus_rule_file():
    """The shipped alert contract is the external rule file, not the class."""
    alerts = REPO_ROOT / "monitoring/prometheus/alerts.yml"
    assert alerts.is_file(), "the canonical Prometheus alert rules must exist"

    rules = [rule for group in yaml.safe_load(alerts.read_text(encoding="utf-8"))["groups"] for rule in group["rules"]]
    assert rules, "the canonical alert file must define at least one rule"

    # A Prometheus rule evaluates series, so it must carry a severity label and an
    # expression. The in-process engine's rules carry a dotted collector name
    # instead and are not loaded by anything.
    for rule in rules:
        assert rule["labels"]["severity"] in {"critical", "warning", "info"}
        assert rule["expr"].strip()


def test_config_json_ships_no_alert_rule_block():
    """`config.json` must not look like a second alert contract.

    The block that used to live here fed only the unwired in-process engine. JSON
    cannot carry a comment explaining that, so leaving it would be indistinguishable
    from a real production rule set. `AlertingManager._load_alert_rules` still
    tolerates the key for an operator's local config, but the repository's own
    config does not advertise one.
    """
    config = json.loads((REPO_ROOT / "config.json").read_text(encoding="utf-8"))
    assert "alerting" not in config, (
        "config.json must not declare alerting.rules: the only consumer is the legacy, "
        "unwired AlertingManager, and a rule block here reads like a second production "
        "alert contract. Canonical alerting is monitoring/prometheus/alerts.yml."
    )


def test_audit_and_operations_docs_state_which_mechanism_is_canonical():
    """The two-mechanism distinction must be documented, not just true in code."""
    audit = (REPO_ROOT / "docs/repository-truth-audit.md").read_text(encoding="utf-8")
    operations = (REPO_ROOT / "docs/operations-guide.md").read_text(encoding="utf-8")

    assert "In-process AlertingManager" in audit
    assert "monitoring/prometheus/alerts.yml" in audit
    # The audit must name the reachability evidence, not merely assert the outcome.
    assert "core/pipeline_context.py" in audit

    assert "monitoring/prometheus/alerts.yml" in operations
    assert "config.json" in operations, "operations docs must say where the rules do NOT live"


def test_default_engine_rules_without_a_canonical_producer_are_documented():
    """Two default rules depend on metrics the canonical path never produces.

    This is a known limitation of the retained class, recorded so nobody reads the
    rule list as usable coverage.
    """
    from monitoring.otel_tracer import AlertingManager, MetricsCollector

    collector = MetricsCollector()
    collector.set_active_requests(0)
    for status in (200, 401, 429, 500):
        collector.record_http_request(status, 12.5)
    collector.set_redis_degraded(True)
    collector.set_redis_degraded(False)

    unproducible = {"kv_utilization", "rerank_batch_queue_delay_p99"}
    rules = AlertingManager(collector)._alert_rules
    dependent = {rule["metric"] for rule in rules} & unproducible
    assert dependent == unproducible, (
        "expected the retained default rule set to still reference the two "
        f"unproducible metrics, found {sorted(dependent)}"
    )
    manager = AlertingManager(collector)
    for metric in dependent:
        assert manager._get_metric_value(metric) is None, (
            f"{metric} unexpectedly has a canonical producer; if one was added, "
            "update docs/repository-truth-audit.md instead of leaving a stale caveat"
        )
