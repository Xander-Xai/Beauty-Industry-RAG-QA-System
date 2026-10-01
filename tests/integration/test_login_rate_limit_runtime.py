"""Real Redis + multi-process validation for login rate limiting.

Two independent Python processes must share the same Redis counter. If the
counter were process-local, each worker would independently allow 5 attempts
and the 6th would not be blocked.

Gate: RUN_RUNTIME_VALIDATION=1 plus VALIDATION_REDIS_* env.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.runtime]

ROOT = Path(__file__).resolve().parents[2]

REDIS_HOST = os.environ.get("VALIDATION_REDIS_HOST", "127.0.0.1")
REDIS_PORT = os.environ.get("VALIDATION_REDIS_PORT", "6380")
REDIS_PASSWORD = os.environ.get("VALIDATION_REDIS_PASSWORD", "validation-password")

ATTEMPT_CODE = """
import os
import api.routes_auth as m
ip = os.environ["RL_IP"]
try:
    m._check_login_rate_limit(ip)
    print("ALLOWED")
except Exception as exc:
    print(f"BLOCKED:{getattr(exc, 'status_code', 'none')}")
"""

EXPIRY_CODE = """
import os, time
import api.routes_auth as m
m._LOGIN_RATE_WINDOW = 1.0
ip = os.environ["RL_IP"]
def attempt():
    try:
        m._check_login_rate_limit(ip)
        return "ALLOWED"
    except Exception as exc:
        return f"BLOCKED:{getattr(exc, 'status_code', 'none')}"
results = [attempt() for _ in range(6)]
time.sleep(1.2)
results.append(attempt())
print(",".join(results))
"""

FALLBACK_CODE = """
import os
import api.routes_auth as m
ip = os.environ["RL_IP"]
def attempt():
    try:
        m._check_login_rate_limit(ip)
        return "ALLOWED"
    except Exception as exc:
        return f"BLOCKED:{getattr(exc, 'status_code', 'none')}"
print(",".join(attempt() for _ in range(6)))
"""


def _enabled() -> bool:
    return os.environ.get("RUN_RUNTIME_VALIDATION") == "1"


@pytest.fixture(scope="module", autouse=True)
def _require_runtime():
    if not _enabled():
        pytest.skip("set RUN_RUNTIME_VALIDATION=1 to run real runtime validation")


def _run(code: str, ip: str, port: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(ROOT),
            "DEPLOYMENT_MODE": "development",
            "REDIS_PASSWORD": REDIS_PASSWORD,
            "REDIS_CACHE_HOST": REDIS_HOST,
            "REDIS_CACHE_PORT": port or REDIS_PORT,
            "REDIS_CACHE_DB": "0",
            "RL_IP": ip,
        }
    )
    return subprocess.run(
        [sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60
    )


def _client():
    import redis

    return redis.Redis(
        host=REDIS_HOST, port=int(REDIS_PORT), password=REDIS_PASSWORD, decode_responses=True, socket_connect_timeout=2
    )


@pytest.fixture()
def ip():
    value = f"198.51.100.{uuid.uuid4().int % 200 + 1}"
    _client().delete(f"ratelimit:login:{value}")
    yield value
    _client().delete(f"ratelimit:login:{value}")


class TestLoginRateLimitRuntime:
    def test_two_processes_share_redis_counter(self, ip):
        results = []
        for i in range(6):
            # alternate "workers" A/B on every attempt
            _worker = "A" if i % 2 == 0 else "B"
            proc = _run(ATTEMPT_CODE, ip)
            assert proc.returncode == 0, proc.stderr
            results.append(proc.stdout.strip().splitlines()[-1])

        assert results[:5] == ["ALLOWED"] * 5, results
        assert results[5].startswith("BLOCKED:429"), results

    def test_redis_counter_key_exists(self, ip):
        _run(ATTEMPT_CODE, ip)
        assert _client().get(f"ratelimit:login:{ip}") == "1"

    def test_window_expiry_recovers(self, ip):
        proc = _run(EXPIRY_CODE, ip)
        assert proc.returncode == 0, proc.stderr
        results = proc.stdout.strip().splitlines()[-1].split(",")
        assert results[:5] == ["ALLOWED"] * 5, results
        assert results[5].startswith("BLOCKED:429"), results
        assert results[6] == "ALLOWED", results

    def test_redis_down_degrades_to_process_local_memory(self, ip):
        unused_port = "6399"
        proc = _run(FALLBACK_CODE, ip, port=unused_port)
        assert proc.returncode == 0, proc.stderr
        results = proc.stdout.strip().splitlines()[-1].split(",")
        # memory limiter still blocks the 6th attempt, but it is per-process only
        assert results[:5] == ["ALLOWED"] * 5, results
        assert results[5].startswith("BLOCKED:429"), results
