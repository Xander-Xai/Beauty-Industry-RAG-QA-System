#!/usr/bin/env python3
"""Real Prometheus authenticated-scrape validation for GET /api/metrics.

Starts the canonical FastAPI app with RS256 JWT auth, then verifies:
  1. no token -> 401
  2. valid bearer token -> 200 Prometheus text
  3. a real Prometheus container scrapes the target with a bearer_token and the
     target reports UP.

Exits 0 on success, 1 on failure. Cleans up its own container/process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

APP_PORT = int(os.environ.get("VALIDATION_APP_PORT", "8010"))
PROM_NAME = "rag-validation-prometheus"
PROM_PORT = 9090
JOB_NAME = "rag-api"


def _log(ok: bool, message: str) -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {message}")


def _http(url: str, token: str | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url)  # noqa: S310 - validation-only local URL
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - validation-only local URL
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def _wait_health(timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, _ = _http(f"http://127.0.0.1:{APP_PORT}/api/health")
            if status == 200:
                return True
        except Exception:  # noqa: S110 - poll until the app is ready
            pass
        time.sleep(1.0)
    return False


def main() -> int:
    failures = 0
    workdir = Path(tempfile.mkdtemp(prefix="rag-metrics-"))
    key_dir = workdir / "keys"
    log_path = workdir / "app.log"

    from auth.jwt_auth import create_access_token, generate_keypair

    private_path, public_path = generate_keypair(str(key_dir))
    os.environ["JWT_PRIVATE_KEY_PATH"] = private_path
    os.environ["JWT_PUBLIC_KEY_PATH"] = public_path
    os.environ["JWT_ALGORITHM"] = "RS256"
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(ROOT),
            "DEPLOYMENT_MODE": "development",
            "API_PORT": str(APP_PORT),
            "JWT_PRIVATE_KEY_PATH": private_path,
            "JWT_PUBLIC_KEY_PATH": public_path,
            "JWT_ALGORITHM": "RS256",
            "JWT_SECRET": "",
        }
    )
    token = create_access_token("validation-user", role_mask=0, dept_mask=0)

    app_log = open(log_path, "w")  # noqa: SIM115 - closed in finally
    app_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(APP_PORT),
            "--no-proxy-headers",
            "--log-level",
            "warning",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=app_log,
        stderr=subprocess.STDOUT,
    )
    try:
        if not _wait_health():
            app_log.flush()
            print(log_path.read_text(encoding="utf-8")[-2000:])
            _log(False, "app did not become healthy")
            return 1

        status, _ = _http(f"http://127.0.0.1:{APP_PORT}/api/metrics")
        _log(status == 401, f"GET /api/metrics without token -> {status} (expected 401)")
        failures += 0 if status == 401 else 1

        status, body = _http(f"http://127.0.0.1:{APP_PORT}/api/metrics", token=token)
        prometheus_text = body.lstrip().startswith("# HELP") or "# HELP" in body or "rag_" in body
        _log(
            status == 200 and prometheus_text,
            f"GET /api/metrics with bearer token -> {status}, Prometheus text={prometheus_text}",
        )
        failures += 0 if (status == 200 and prometheus_text) else 1

        # Real Prometheus scrape with the token.
        prom_conf = workdir / "prometheus.yml"
        prom_conf.write_text(
            "global:\n  scrape_interval: 5s\n"
            "scrape_configs:\n"
            f"  - job_name: {JOB_NAME}\n"
            "    metrics_path: /api/metrics\n"
            "    static_configs:\n"
            "      - targets: ['127.0.0.1:%d']\n" % APP_PORT + "    authorization:\n"
            "      type: Bearer\n"
            f'      credentials: "{token}"\n',
            encoding="utf-8",
        )
        os.chmod(prom_conf, 0o600)
        subprocess.run(["docker", "rm", "-f", PROM_NAME], capture_output=True, check=False)
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                PROM_NAME,
                # Host networking so Prometheus can reach the loopback-bound app;
                # root so it can read the 0600 token-bearing config in the
                # private temp dir (throwaway validation container only).
                "--network",
                "host",
                "--user",
                "0:0",
                "-v",
                f"{prom_conf}:/etc/prometheus/prometheus.yml:ro",
                "prom/prometheus:latest",
            ],
            capture_output=True,
            check=True,
        )

        # Wait for Prometheus API and an UP target.
        target_up = False
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                status, body = _http(f"http://127.0.0.1:{PROM_PORT}/api/v1/targets")
                if status == 200:
                    targets = json.loads(body)["data"]["activeTargets"]
                    if any(t["labels"].get("job") == JOB_NAME and t["health"] == "up" for t in targets):
                        target_up = True
                        break
            except Exception:  # noqa: S110 - poll until the target is scraped
                pass
            time.sleep(3)
        _log(target_up, f"Prometheus target {JOB_NAME!r} is UP")
        failures += 0 if target_up else 1

        if target_up:
            status, body = _http(f"http://127.0.0.1:{PROM_PORT}/api/v1/query?query=up%7Bjob%3D%22{JOB_NAME}%22%7D")
            value = None
            if status == 200:
                result = json.loads(body)["data"]["result"]
                if result:
                    value = result[0]["value"][1]
            _log(value == "1", f"Prometheus up{{job={JOB_NAME!r}}} == {value!r}")
            failures += 0 if value == "1" else 1

        # Security: the token must not leak into logs or git.
        app_log.flush()
        logs = log_path.read_text(encoding="utf-8", errors="replace")
        _log(token not in logs, "bearer token does not appear in app logs")
        failures += 0 if token not in logs else 1
    finally:
        subprocess.run(["docker", "rm", "-f", PROM_NAME], capture_output=True, check=False)
        app_proc.terminate()
        try:
            app_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            app_proc.kill()
        app_log.close()

    print()
    if failures:
        print(f"METRICS AUTH VALIDATION FAILED ({failures})")
        return 1
    print("METRICS AUTH VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
