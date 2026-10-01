"""Runtime wrapper for authenticated Prometheus scrape validation.

Runs scripts/validation/validate_metrics_auth.py: canonical FastAPI app with
RS256 auth + a real Prometheus container scraping /api/metrics with a bearer
token.

Gate: RUN_RUNTIME_VALIDATION=1 plus docker.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.runtime]

ROOT = Path(__file__).resolve().parents[2]


def _enabled() -> bool:
    return os.environ.get("RUN_RUNTIME_VALIDATION") == "1"


@pytest.mark.skipif(not _enabled(), reason="set RUN_RUNTIME_VALIDATION=1 to run real runtime validation")
def test_prometheus_authenticated_scrape():
    proc = subprocess.run(
        [sys.executable, "-u", "scripts/validation/validate_metrics_auth.py"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "METRICS AUTH VALIDATION PASSED" in combined
