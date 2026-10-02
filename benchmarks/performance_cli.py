"""CLI for the performance-evidence harness.

Two modes:

``--probe``
    Run only the preflight and report whether a real measurement is possible.
    Writes no artifact, so it is safe to run anywhere.

default
    Execute the declared workload and write the seven-file artifact. When a
    prerequisite is missing the artifact is still written, but with
    ``status = BLOCKED`` and every measurement absent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - direct-script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.performance import (  # noqa: E402
    RequestTally,
    Workload,
    build_report,
    new_run_id,
    write_artifact,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "performance"

_ENDPOINTS = ("/api/health", "/api/stats", "/api/query")


def preflight(host: str, token: str) -> str | None:
    """Return a ``blocked_reason`` when the target cannot be measured.

    Duplicated deliberately from the Locust file: the CLI must stay usable
    without importing Locust, which is an optional dependency.
    """
    import requests

    try:
        health = requests.get(f"{host}/api/health", timeout=10)
    except Exception as exc:
        return f"api_unreachable: {type(exc).__name__}"
    if health.status_code != 200:
        return f"api_health_status_{health.status_code}"
    if not token:
        return "missing_auth_token: /api/stats and /api/query require a bearer token"
    try:
        stats = requests.get(f"{host}/api/stats", headers={"Authorization": f"Bearer {token}"}, timeout=10)
    except Exception as exc:
        return f"api_stats_unreachable: {type(exc).__name__}"
    if stats.status_code != 200:
        return f"api_stats_status_{stats.status_code}"
    return None


def build_blocked_artifact(reason: str, host: str, token: str, users: int, spawn_rate: float) -> tuple[dict, Path]:
    """Write an artifact for a run that could not execute.

    Returns the artifact and the directory it was written to, so the caller
    reports the real path instead of generating a second, different run id.
    """
    workload = Workload(
        users=users,
        spawn_rate=spawn_rate,
        duration_seconds=None,
        target_base=host,
        endpoints=_ENDPOINTS,
        authenticated=bool(token),
    )
    run_dir = ARTIFACT_ROOT / new_run_id()
    artifact = write_artifact(
        run_dir,
        kind="cli-preflight",
        blocked_reason=reason,
        workload=workload,
        latency_samples=[],
        requests=RequestTally(),
        duration_seconds=None,
        observed_users=None,
        limitations=["Run aborted by preflight; no request was sent to the target."],
        repo_root=str(REPO_ROOT),
    )
    return artifact, run_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.performance",
        description="Performance evidence harness (BLOCKED/PARTIAL/EXECUTED, never fabricated zeros)",
    )
    parser.add_argument("--host", default=os.environ.get("BENCHMARK_API_BASE", "http://localhost:8000"))
    parser.add_argument("--token", default=os.environ.get("BENCHMARK_AUTH_TOKEN", ""))
    parser.add_argument("--users", type=int, default=3)
    parser.add_argument("--spawn-rate", type=float, default=1.0)
    parser.add_argument("--run-time", default="60s")
    parser.add_argument(
        "--probe",
        action="store_true",
        help="run only the preflight and exit without writing an artifact",
    )
    args = parser.parse_args(argv)

    reason = preflight(args.host, args.token)

    if args.probe:
        print(json.dumps({"host": args.host, "measurable": reason is None, "blocked_reason": reason}, indent=2))
        return 0 if reason is None else 1

    if reason is not None:
        artifact, run_dir = build_blocked_artifact(reason, args.host, args.token, args.users, args.spawn_rate)
        print(f"status: {artifact['metadata']['status']}")
        print(f"blocked_reason: {artifact['metadata']['blocked_reason']}")
        print(f"artifact: {run_dir}")
        print(f"latency: {json.dumps(artifact['latency'])}")
        print(f"throughput: {json.dumps(artifact['throughput'])}")
        print(build_report(artifact))
        return 0

    # Preflight passed: delegate the real workload to Locust.
    import subprocess

    os.environ["BENCHMARK_API_BASE"] = args.host
    os.environ["BENCHMARK_AUTH_TOKEN"] = args.token
    os.environ["BENCHMARK_SPAWN_RATE"] = str(args.spawn_rate)
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tests" / "load" / "run_benchmark.py"),
            "--host",
            args.host,
            "--token",
            args.token,
            "--users",
            str(args.users),
            "--spawn-rate",
            str(int(args.spawn_rate)),
            "--run-time",
            args.run_time,
        ],
        cwd=str(REPO_ROOT),
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
