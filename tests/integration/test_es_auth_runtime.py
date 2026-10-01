"""Runtime wrapper for authenticated Elasticsearch validation.

Runs scripts/validation/validate_es_auth.py against a real ES with
xpack.security.enabled=true.

Gate: RUN_RUNTIME_VALIDATION=1 and ELASTICSEARCH_PASSWORD set.
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
    return os.environ.get("RUN_RUNTIME_VALIDATION") == "1" and bool(os.environ.get("ELASTICSEARCH_PASSWORD"))


@pytest.mark.skipif(not _enabled(), reason="set RUN_RUNTIME_VALIDATION=1 and ELASTICSEARCH_PASSWORD")
def test_authenticated_elasticsearch_paths():
    proc = subprocess.run(
        [sys.executable, "-u", "scripts/validation/validate_es_auth.py"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=240,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "ES AUTH VALIDATION PASSED" in combined
