"""Real HTTP + real nginx validation wrapper for TRUSTED_PROXIES.

Runs scripts/validation/validate_trusted_proxy.py, which starts two uvicorn
instances and a real nginx (single-hop and multi-hop) and asserts the client-IP
resolver behavior at the HTTP layer.

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
def test_trusted_proxy_with_real_nginx():
    proc = subprocess.run(
        [sys.executable, "-u", "scripts/validation/validate_trusted_proxy.py"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=240,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "TRUSTED PROXY VALIDATION PASSED" in combined
