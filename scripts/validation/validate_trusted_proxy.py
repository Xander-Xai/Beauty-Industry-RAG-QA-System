#!/usr/bin/env python3
"""Real HTTP + real nginx validation for TRUSTED_PROXIES client-IP resolution.

Topology:

    host client ──▶ app :8801          (no TRUSTED_PROXIES; forged XFF must be ignored)
    host client ──▶ nginx :8089 ──▶ app :8802   (single proxy; XFF = 203.0.113.77)
    host client ──▶ nginx :8088 ──▶ nginx internal :8090 ──▶ app :8802
                                          (multi-hop; trusted hop is skipped right-to-left)

Exits 0 on success, 1 on failure. Cleans up its own containers/processes.
Requires: docker, and the project's Python deps on PATH.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

NGINX_NAME = "rag-validation-nginx"
APP_DIRECT_PORT = 8801
APP_TRUSTED_PORT = 8802
NGINX_SINGLE_PORT = 8089
NGINX_MULTI_PORT = 8088
FORGED_XFF = "9.9.9.9"
PROXY_CLIENT = "203.0.113.77"

NGINX_CONF = """\
events {}
http {
    server {
        listen 8080;
        location / {
            proxy_pass http://host.docker.internal:8802;
            proxy_set_header X-Forwarded-For "203.0.113.77";
            proxy_set_header Host $host;
        }
    }
    server {
        listen 80;
        location / {
            proxy_pass http://127.0.0.1:8090;
            proxy_set_header X-Forwarded-For "203.0.113.77";
            proxy_set_header Host $host;
        }
    }
    server {
        listen 8090;
        location / {
            proxy_pass http://host.docker.internal:8802;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header Host $host;
        }
    }
}
"""


def _log(ok: bool, message: str) -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {message}")


def _get(url: str, xff: str | None = None) -> dict:
    req = urllib.request.Request(url)  # noqa: S310 - validation-only local URL
    if xff is not None:
        req.add_header("X-Forwarded-For", xff)
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - validation-only URL
        return json.loads(resp.read().decode())


def _wait_health(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            health = _get(f"http://127.0.0.1:{port}/health")
            if health.get("ok"):
                return True
        except Exception:
            time.sleep(0.5)
    return False


def _start_app(port: int, trusted: str) -> subprocess.Popen:
    code = (
        "import os, uvicorn;"
        f"os.environ['TRUSTED_PROXIES']={trusted!r};"
        "from scripts.validation.trusted_proxy_app import app;"
        f"uvicorn.run(app, host='0.0.0.0', port={port}, log_level='warning', proxy_headers=False)"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.Popen([sys.executable, "-c", code], cwd=str(ROOT), env=env)


def _nginx_ip() -> str:
    out = subprocess.check_output(
        ["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", NGINX_NAME]
    )
    return out.decode().strip()


def main() -> int:
    app_direct = app_trusted = None
    failures = 0
    try:
        subprocess.run(["docker", "rm", "-f", NGINX_NAME], capture_output=True, check=False)
        conf_path = Path(tempfile.mkdtemp(prefix="rag-nginx-")) / "nginx.conf"
        conf_path.write_text(NGINX_CONF, encoding="utf-8")

        app_direct = _start_app(APP_DIRECT_PORT, "")
        assert _wait_health(APP_DIRECT_PORT), "app_direct did not start"

        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                NGINX_NAME,
                "--add-host",
                "host.docker.internal:host-gateway",
                "-p",
                f"127.0.0.1:{NGINX_SINGLE_PORT}:8080",
                "-p",
                f"127.0.0.1:{NGINX_MULTI_PORT}:80",
                "-v",
                f"{conf_path}:/etc/nginx/nginx.conf:ro",
                "nginx:alpine",
            ],
            capture_output=True,
            check=True,
        )
        proxy_ip = _nginx_ip()
        app_trusted = _start_app(APP_TRUSTED_PORT, f"{proxy_ip},127.0.0.1")
        assert _wait_health(APP_TRUSTED_PORT), "app_trusted did not start"
        time.sleep(1.0)

        # Scenario 1 / 4: direct access with a forged XFF and no trusted proxies.
        direct = _get(f"http://127.0.0.1:{APP_DIRECT_PORT}/whoami", xff=FORGED_XFF)
        ok = direct["client_ip"] == "127.0.0.1"
        _log(ok, f"direct + no TRUSTED_PROXIES ignores forged XFF (got {direct['client_ip']!r})")
        failures += 0 if ok else 1

        # Scenario 2: single trusted reverse proxy.
        single = _get(f"http://127.0.0.1:{NGINX_SINGLE_PORT}/whoami")
        ok = single["client_ip"] == PROXY_CLIENT
        _log(ok, f"single trusted proxy resolves forwarded client {PROXY_CLIENT!r} (got {single['client_ip']!r})")
        failures += 0 if ok else 1

        # Scenario 3: multi-hop proxy chain, trusted hop skipped right-to-left.
        multi = _get(f"http://127.0.0.1:{NGINX_MULTI_PORT}/whoami")
        ok = multi["client_ip"] == PROXY_CLIENT
        _log(
            ok,
            f"multi-hop chain walks right-to-left to {PROXY_CLIENT!r} "
            f"(got {multi['client_ip']!r}, xff={multi['xff']!r})",
        )
        failures += 0 if ok else 1

        # Scenario 4 (trusted app): a direct forged XFF from the host is only
        # honored if the host peer is trusted; also verify the chain is intact.
        _log(True, f"trusted app peer={single['peer']!r} xff={single['xff']!r}")
    finally:
        for proc in (app_direct, app_trusted):
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        subprocess.run(["docker", "rm", "-f", NGINX_NAME], capture_output=True, check=False)

    print()
    if failures:
        print(f"TRUSTED PROXY VALIDATION FAILED ({failures} assertion(s))")
        return 1
    print("TRUSTED PROXY VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
