"""Runtime wrapper for the real-Qdrant store validation (VAL-STORE-001).

Runs ``scripts/validation/validate_qdrant_store.py`` against a real Qdrant
server.  Every regression run in this repository otherwise uses the in-process
``QdrantClient(":memory:")``; this is the only path that exercises the actual
vector engine (epoch point ids, payload filters, collection separation, RBAC
re-filter, text+image fusion).

Gate: RUN_RUNTIME_VALIDATION=1 and a reachable Qdrant
(VALIDATION_QDRANT_HOST / VALIDATION_QDRANT_PORT, default 127.0.0.1:6333).
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
def test_real_qdrant_store_paths():
    env = dict(os.environ)
    env.setdefault("VALIDATION_QDRANT_HOST", "127.0.0.1")
    env.setdefault("VALIDATION_QDRANT_PORT", "6333")
    proc = subprocess.run(
        [sys.executable, "-u", "scripts/validation/validate_qdrant_store.py"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "QDRANT STORE VALIDATION PASSED" in combined
