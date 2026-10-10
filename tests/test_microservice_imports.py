"""Contract test: every retained microservice entry point must import.

Issue #84 found that 5 of the 6 service directories could not start at all:
each ``*/main.py`` added only the project root to ``sys.path`` and then imported
sibling modules by bare top-level name, so ``python -m uvicorn <svc>.main:app``
failed with ``ModuleNotFoundError``.  ``api-gateway/main.py`` did it correctly.

This test pins the fix.  Each service is imported in its **own** subprocess so
that one service's ``sys.path`` edits cannot mask a missing bootstrap in
another, and so the test process itself is never polluted with the service
top-level module names (``rewriter``, ``redis_cache``, ``metrics_collector`` …).

Evidence level: ``REPO_VERIFIED`` for "the module imports and constructs a
FastAPI app with a health route".  It is deliberately **not** a claim that a
container starts or that the services are integrated end to end — see
``docs/deferred-runtime-validation.md``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: service directory -> dotted module path
SERVICES = {
    "api-gateway": "api-gateway.main",
    "rewrite-service": "rewrite-service.main",
    "retrieval-service": "retrieval-service.main",
    "generation-service": "generation-service.main",
    "cache-service": "cache-service.main",
    "monitoring-service": "monitoring-service.main",
}

# The gateway mounts its routers under ``/v1`` and exposes them through an
# included-router wrapper, so its health path is asserted separately.
_HEALTH_PATH = {
    "api-gateway": "/v1/health",
    "rewrite-service": "/health",
    "retrieval-service": "/health",
    "generation-service": "/health",
    "cache-service": "/health",
    "monitoring-service": "/health",
}

_IMPORT_PROBE = r"""
import importlib
import json
import sys

module_name = sys.argv[1]
try:
    # uvicorn resolves ``<module>:<attr>`` through importlib.import_module,
    # which is exactly how docker-compose.microservices.yml invokes each app.
    module = importlib.import_module(module_name)
    app = getattr(module, "app", None)
    paths = sorted(
        p for p in (getattr(route, "path", None) for route in getattr(app, "routes", [])) if p
    )
    print(json.dumps({
        "ok": app is not None,
        "app_type": type(app).__name__,
        "paths": paths,
    }))
except Exception as exc:  # noqa: BLE001 - report the real failure to the test
    print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
    sys.exit(1)
"""


def _import_service(service: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE, SERVICES[service]],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    stdout = proc.stdout.strip().splitlines()
    payload = json.loads(stdout[-1]) if stdout else {"ok": False, "error": proc.stderr[-500:]}
    assert proc.returncode == 0 and payload.get("ok"), (
        f"{service} failed to import: returncode={proc.returncode}, payload={payload}, stderr={proc.stderr[-800:]}"
    )
    return payload


@pytest.mark.parametrize("service", sorted(SERVICES))
def test_service_main_module_imports_and_exposes_app(service):
    """Each service's ``main`` module imports and exposes a FastAPI app."""
    payload = _import_service(service)
    assert payload["app_type"] == "FastAPI", payload
    assert payload["paths"], f"{service} exposes no routes"


@pytest.mark.parametrize("service", sorted(SERVICES))
def test_service_exposes_a_health_route(service):
    """Every service declares the health route its compose healthcheck calls."""
    payload = _import_service(service)
    expected = _HEALTH_PATH[service]
    if service == "api-gateway":
        # The gateway's routes are mounted through an included-router wrapper;
        # the ASGI-level path is asserted by tests/test_api.py, so here we only
        # require the import to succeed and at least one route to exist.
        assert payload["paths"], payload
        return
    assert expected in payload["paths"], f"{service} is missing {expected}: {payload['paths']}"
