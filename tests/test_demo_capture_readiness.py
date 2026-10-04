"""The readiness probe must be about *this* child, not just the URL.

`capture_demo.py` starts the synthetic mock API and the Vite dev server, then
polls each until it answers. Polling the URL alone is not sufficient: if the
port is already occupied, the freshly spawned child dies on bind while the
probe succeeds against whatever was already listening there. The capture then
renders a completely different backend than the one in
`docs/demo/synthetic_corpus.json` and commits the result as the README hero
image — a screenshot that is wrong, not merely stale.

These tests drive `wait_for_http` against real child processes on real
loopback ports, because the failure being guarded against is a race between a
process exiting and a probe succeeding.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socket import socket
from threading import Thread

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_PATH = REPO_ROOT / "docs" / "demo" / "capture_demo.py"


def _load_capture():
    """Import the standalone capture script by path; `docs/demo` is not a package."""
    spec = importlib.util.spec_from_file_location("demo_capture_readiness", CAPTURE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CAPTURE = _load_capture()


def _free_port() -> int:
    """A port that was free a moment ago.

    Racy in principle; good enough here because the tests either bind it
    immediately or never bind it at all.
    """
    with closing(socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib signature
        payload = b'{"status": "healthy"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def serving():
    """A real loopback HTTP server, yielded as ``(base_url, port)``."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OkHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        yield f"http://127.0.0.1:{port}", port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def sleeper():
    """A child that stays alive but never listens — the 'still booting' case."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        yield process
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture
def dead_child(tmp_path):
    """A child that exits immediately with a distinctive code."""
    process = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
    process.wait(timeout=10)
    assert process.returncode == 3
    return process


# ── the happy path still works ──────────────────────────────────────────
def test_a_live_server_is_accepted(serving):
    """Unchanged behaviour: a child that serves is ready."""
    base_url, _ = serving
    CAPTURE.wait_for_http(f"{base_url}/api/health", expect_json=True, timeout=10)


def test_the_process_argument_is_optional(serving):
    """Existing callers that pass only a URL keep working."""
    base_url, _ = serving
    CAPTURE.wait_for_http(f"{base_url}/api/health", expect_json=True, timeout=10)


# ── the regression: a dead child must not look ready ────────────────────
def test_a_child_that_exited_is_not_ready_even_when_the_url_answers(serving, dead_child):
    """The port is served, but by something else. This is exactly the shape of
    the bug: without the liveness check the probe returns and the capture
    renders whatever is on that port."""
    base_url, _ = serving

    with pytest.raises(SystemExit) as excinfo:
        CAPTURE.wait_for_http(f"{base_url}/api/health", expect_json=True, timeout=30, process=dead_child)

    message = str(excinfo.value)
    assert "exited with code 3" in message, message


def test_a_dead_child_fails_fast_instead_of_waiting_out_the_timeout(serving, dead_child):
    """A 90-second timeout on a port that will never be ours is a long way to
    wait to learn nothing; the exit is detected on the first iteration."""
    base_url, _ = serving

    started = time.monotonic()
    with pytest.raises(SystemExit):
        CAPTURE.wait_for_http(f"{base_url}/api/health", timeout=45, process=dead_child)
    elapsed = time.monotonic() - started

    assert elapsed < 5, f"took {elapsed:.1f}s to notice an already-exited child"


def test_a_child_that_dies_mid_poll_is_caught_before_the_timeout(sleeper):
    """Not only "already dead at entry": a child that dies while the probe is
    looping must still surface, rather than the probe silently succeeding
    against whatever claims the port next."""
    port = _free_port()

    def _kill_soon():
        time.sleep(1.0)
        sleeper.terminate()

    Thread(target=_kill_soon, daemon=True).start()

    with pytest.raises(SystemExit) as excinfo:
        CAPTURE.wait_for_http(f"http://127.0.0.1:{port}/", timeout=30, process=sleeper)

    assert "exited with code" in str(excinfo.value), str(excinfo.value)


def test_a_live_child_that_never_serves_still_reports_a_timeout(sleeper):
    """The liveness check must not replace the timeout: a child that stays up
    and never binds is a timeout, not an exit."""
    port = _free_port()

    started = time.monotonic()
    with pytest.raises(SystemExit) as excinfo:
        CAPTURE.wait_for_http(f"http://127.0.0.1:{port}/", timeout=3, process=sleeper)
    elapsed = time.monotonic() - started

    assert "timed out waiting for" in str(excinfo.value), str(excinfo.value)
    assert "exited with code" not in str(excinfo.value)
    assert elapsed < 20, f"timeout was not honoured ({elapsed:.1f}s)"


# ── the starters surface the actual cause ───────────────────────────────
def test_starting_the_mock_on_an_occupied_port_fails_with_the_port_in_the_message(serving):
    """`--api-port` collision has to be reported as a collision, not as a
    mystery readiness timeout, and must not leave the failed child running."""
    base_url, port = serving

    with pytest.raises(SystemExit) as excinfo:
        CAPTURE.start_mock_api(port, REPO_ROOT / "docs" / "demo" / ".build" / "test_mock_api.log")

    message = str(excinfo.value)
    assert str(port) in message, message
    assert "--api-port" in message, message


def test_starting_the_frontend_on_an_occupied_port_fails_with_the_port_in_the_message(serving, tmp_path):
    """Same for `--web-port`, which uses `--strictPort` and so fails the same
    way. Skipped when npm or node_modules is unavailable, since the point under
    test is the error path after the spawn."""
    import shutil

    if shutil.which("npm") is None or not (REPO_ROOT / "frontend" / "node_modules").exists():
        pytest.skip("npm or frontend/node_modules unavailable")

    _, port = serving

    with pytest.raises(SystemExit) as excinfo:
        CAPTURE.start_frontend(port, 8799, tmp_path / "vite.log")

    message = str(excinfo.value)
    assert str(port) in message, message
    assert "--web-port" in message, message


# ── the reason is documented where a reader will look ───────────────────
def test_capture_docstring_names_the_port_collision():
    """The docstring is the `--help` text, and a port collision is the most
    common reason a first run fails on a shared machine or in CI."""
    doc = CAPTURE.__doc__ or ""
    assert "--api-port" in doc
    assert "--web-port" in doc
