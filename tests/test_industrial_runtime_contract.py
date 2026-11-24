"""Regression tests for request failures, build layout and the shared probe budget."""

from pathlib import Path

import pytest

from api import readiness, routes
from core.exceptions import InfrastructureError
from core.pipeline_context import RequestContext


def test_infrastructure_failure_is_not_a_success(monkeypatch):
    class BrokenPipeline:
        def process(self, ctx):
            raise InfrastructureError("private backend detail", service="redis")

    class Metrics:
        def record_request(self, ctx):
            assert ctx.degraded

    monkeypatch.setattr(routes, "get_pipeline", BrokenPipeline)
    monkeypatch.setattr(routes, "get_metrics", Metrics)
    ctx = RequestContext(user_input="test")
    routes._execute_pipeline(ctx)
    assert ctx.http_status_override[0] == 503
    assert "private backend detail" not in str(ctx.http_status_override)
    assert not ctx.evidence_locked_doc_ids


def test_probe_waits_share_one_deadline(monkeypatch):
    waits = []
    now = [0.0]

    class Future:
        def result(self, timeout):
            waits.append(timeout)
            now[0] += timeout
            raise TimeoutError

    class Executor:
        def __init__(self, max_workers):
            pass

        def submit(self, *args):
            return Future()

        def shutdown(self, **kwargs):
            pass

    monkeypatch.setattr(readiness, "ThreadPoolExecutor", Executor)
    monkeypatch.setattr(readiness.time, "monotonic", lambda: now[0])
    probes = dict.fromkeys(readiness.DEPENDENCY_KEYS, lambda _: False)
    report = readiness.evaluate_readiness(readiness.ProbeContext(is_production=False), probes)
    assert not report.ready
    assert sum(waits) == pytest.approx(readiness.PROBE_TIMEOUT_SECONDS + readiness._PROBE_GRACE_SECONDS)


def test_image_build_preserves_python_model_package():
    rules = (Path(__file__).resolve().parents[1] / ".dockerignore").read_text().splitlines()
    assert "models" not in rules
    assert "models/*" in rules
    assert "!models/*.py" in rules
