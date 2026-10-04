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
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from contextlib import closing, contextmanager
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


# ── a squatter on the port cannot pass for the child ────────────────────
class _ImpostorHandler(BaseHTTPRequestHandler):
    """A healthy server that is not the child we spawned.

    It answers `/api/health` with a perfectly valid body — which is all a
    URL-only probe ever looked at — but knows nothing about this run's instance
    token, which is exactly the situation the token exists to catch.
    """

    token: str | None = None

    def do_GET(self):  # noqa: N802 - stdlib signature
        payload = json.dumps({"status": "healthy", "instance_token": self.token}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@contextmanager
def _stand_in_server(token: str | None):
    """Run a non-child HTTP server on loopback, yielding its base URL."""
    handler = type("_Handler", (_ImpostorHandler,), {"token": token})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_server_that_does_not_echo_the_token_is_refused(sleeper):
    """The window the preflight check cannot close: something answers on the
    port before our child does. The token is the only check that cannot be
    raced, because the squatter never saw it."""
    with _stand_in_server(None) as base_url:
        with pytest.raises(SystemExit) as excinfo:
            CAPTURE.wait_for_http(
                f"{base_url}/api/health",
                expect_json=True,
                process=sleeper,
                expect_token="this-runs-token",
                timeout=30,
            )
    assert "instance token" in str(excinfo.value), str(excinfo.value)


def test_a_server_echoing_the_right_token_is_accepted(sleeper):
    """The happy path for the new check, so it cannot rot into a refusal."""
    with _stand_in_server("this-runs-token") as base_url:
        CAPTURE.wait_for_http(
            f"{base_url}/api/health",
            expect_json=True,
            process=sleeper,
            expect_token="this-runs-token",
            timeout=10,
        )


class _DiesAfterFirstPoll:
    """A child that is alive for the pre-response check and gone for the next.

    The window the finding named is a genuine race, so it cannot be arranged
    with real processes and real timing. This models exactly the interleaving:
    ``poll()`` answers "still running" the first time — which is what the
    pre-`urlopen` check sees — and reports the exit code afterwards, which is
    what the post-response check sees.
    """

    def __init__(self, returncode: int = 4):
        self.returncode = returncode
        self.polls = 0

    def poll(self):
        self.polls += 1
        return None if self.polls == 1 else self.returncode


def test_liveness_is_rechecked_after_a_successful_response():
    """The specific line the finding named: liveness was checked before
    `urlopen` only, so a 200 that arrived from a child that had meanwhile lost
    the bind was still accepted. Here the response is a valid 200 *and* carries
    the right token — only the post-response check can catch it.
    """
    with _stand_in_server("this-runs-token") as base_url:
        # Sanity: the same server with no process attached is accepted, so the
        # refusal below is attributable to the liveness re-check alone.
        CAPTURE.wait_for_http(f"{base_url}/api/health", expect_json=True, process=None, timeout=10)

        child = _DiesAfterFirstPoll()
        with pytest.raises(SystemExit) as excinfo:
            CAPTURE.wait_for_http(
                f"{base_url}/api/health",
                expect_json=True,
                process=child,
                expect_token="this-runs-token",
                timeout=10,
            )

    assert "already exited with code 4" in str(excinfo.value), str(excinfo.value)
    assert child.polls >= 2, "liveness must be polled again after the response"


def test_a_refusal_does_not_wait_out_the_timeout(sleeper):
    """A token mismatch is not a readiness failure, so retrying cannot help.
    Burning the full timeout would make a real collision take 60s to report."""
    with _stand_in_server("something-else") as base_url:
        started = time.monotonic()
        with pytest.raises(SystemExit):
            CAPTURE.wait_for_http(
                f"{base_url}/api/health",
                expect_json=True,
                process=sleeper,
                expect_token="this-runs-token",
                timeout=45,
            )
        assert time.monotonic() - started < 10


def test_the_mock_echoes_the_token_it_was_given_over_http():
    """Wiring check on the child side: the flag has to reach the response body.

    Driven through the real handler, so a rename of either end is caught here
    rather than as a mysterious capture failure.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("demo_mock_readiness", REPO_ROOT / "docs" / "demo" / "mock_api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module.DemoHandler.corpus = module.load_corpus()
    module.DemoHandler.instance_token = "tok-from-the-command-line"
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.DemoHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(f"{base}/api/health", timeout=10) as response:  # noqa: S310 - loopback test server
            body = json.loads(response.read().decode("utf-8"))
        assert body["instance_token"] == "tok-from-the-command-line"
        assert body["status"] == "healthy"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        module.DemoHandler.instance_token = None


def test_the_default_token_is_absent_so_no_run_can_be_impersonated_by_default():
    """The capture generates a fresh token per run; a server started without
    the flag must report null rather than a value someone could predict."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("demo_mock_default", REPO_ROOT / "docs" / "demo" / "mock_api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.DemoHandler.instance_token is None


def test_the_capture_generates_a_fresh_token_per_run():
    """A hardcoded or reused token would be guessable by a squatter."""
    source = CAPTURE_PATH.read_text(encoding="utf-8")
    assert "uuid.uuid4().hex" in source
    assert "expect_token=token" in source


def test_demo_readme_states_the_port_guarantee():
    """The reason the capture refuses an occupied port is not obvious, and a
    reader who hits it deserves to be told rather than left to guess."""
    text = (REPO_ROOT / "docs" / "demo" / "README.md").read_text(encoding="utf-8")

    assert "instance_token" in text, "the token handshake must be documented, not just implemented"
    assert "--api-port" in text and "--web-port" in text


# ── the whole process group goes away, not just the child ───────────────
# Written to files rather than nested into one another with `repr()`: two levels
# of quoting inside `python -c` is where this gets quietly wrong.
_HOLD_PORT_SOURCE = """\
import socket
import sys

sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", int(sys.argv[1])))
sock.listen(8)
print("listening", flush=True)

# Accept and close, the way a real server does. Without this the listen backlog
# fills after the first probe and every later probe is refused — which makes a
# live listener look like a released port.
sock.settimeout(1.0)
while True:
    try:
        conn, _ = sock.accept()
    except OSError:
        continue
    conn.close()
"""

_SPAWN_CHILD_SOURCE = """\
import subprocess
import sys
import time

grandchild = subprocess.Popen([sys.executable, sys.argv[2], sys.argv[3]])
print(grandchild.pid, flush=True)
time.sleep(120)
"""


def _port_is_busy(port: int) -> bool:
    """Whether something is accepting connections on ``port``."""
    probe = socket()
    probe.settimeout(2)
    try:
        probe.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def _is_executing(pid: int) -> bool:
    """Whether ``pid`` is still running *and* is not a zombie.

    `os.kill(pid, 0)` succeeds for a zombie, so on a host whose PID 1 does not
    promptly reap orphans — a container, typically — a correctly killed
    grandchild still answers, and a test waiting for it to vanish spins until it
    times out and then fails over a process that is neither running nor holding
    anything. Zombie state is read from procfs where available, and `Z` counts
    as stopped.
    """
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            # State follows `comm`, which is parenthesised and may itself contain
            # spaces, so split after the final ")".
            fields = handle.read().rsplit(")", 1)[1].split()
        return fields[0] != "Z"
    except (FileNotFoundError, IndexError, OSError):
        pass
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


@pytest.fixture
def spawner(tmp_path):
    """A child that spawns a grandchild holding a port, like `npm run dev` -> Vite.

    Yields ``(child, grandchild_pid, port)``. The grandchild is a real listener,
    so the teardown can be asserted against the released port — which is the
    actual failure — and against process state separately.
    """
    port = _free_port()
    holder = tmp_path / "holder.py"
    holder.write_text(_HOLD_PORT_SOURCE, encoding="utf-8")
    spawner_source = tmp_path / "spawner.py"
    spawner_source.write_text(_SPAWN_CHILD_SOURCE, encoding="utf-8")

    child = subprocess.Popen(
        [sys.executable, str(spawner_source), sys.executable, str(holder), str(port)],
        stdout=subprocess.PIPE,
        start_new_session=True,
        text=True,
    )
    grandchild_pid = int(child.stdout.readline().strip())

    # Wait until the grandchild is really listening, so the test cannot pass by
    # accident before it ever held anything.
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not _port_is_busy(port):
        time.sleep(0.1)
    assert _port_is_busy(port), "the grandchild never started listening"

    try:
        yield child, grandchild_pid, port
    finally:
        for pid in (grandchild_pid, child.pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        child.stdout.close()


def test_stopping_the_child_releases_the_grandchild_port(spawner):
    """The regression: `terminate()` on the `npm` Popen left Vite alive and still
    listening on `--web-port`, so the next run failed the free-port check and
    every run leaked a process.

    The released port is the assertion rather than the process state, because
    the released port *is* the failure. A zombie holds nothing, so a state-only
    assertion would be asserting something weaker than the bug it guards.
    """
    child, _grandchild_pid, port = spawner
    assert _port_is_busy(port), "precondition: the grandchild is listening"

    CAPTURE.stop_server(child)

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and _port_is_busy(port):
        time.sleep(0.1)
    assert not _port_is_busy(port), "the grandchild still holds the port after the group teardown"
    assert child.poll() is not None, "the child was not reaped"


def test_stopping_the_child_stops_the_grandchild(spawner):
    """The same teardown, checked at the process level as well."""
    child, grandchild_pid, _port = spawner
    assert _is_executing(grandchild_pid), "precondition: the grandchild is running"

    CAPTURE.stop_server(child)

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and _is_executing(grandchild_pid):
        time.sleep(0.1)
    assert not _is_executing(grandchild_pid), "the grandchild survived the group teardown"


def test_stopping_an_already_dead_process_is_a_no_op():
    process = subprocess.Popen([sys.executable, "-c", "raise SystemExit(0)"])
    process.wait(timeout=10)
    CAPTURE.stop_server(process)  # must not raise
    CAPTURE.stop_server(None)  # must not raise either


def test_both_servers_are_started_in_their_own_session():
    """Without a new session there is no separate group to signal, so the whole
    teardown above would degrade back to killing only the direct child."""
    source = CAPTURE_PATH.read_text(encoding="utf-8")
    assert source.count("start_new_session=True") >= 2, "both servers need their own process group"


def test_every_teardown_path_uses_the_group_helper():
    """A startup failure has to clean up as thoroughly as a normal exit —
    otherwise a failed run leaves the port held just as badly."""
    source = CAPTURE_PATH.read_text(encoding="utf-8")
    assert "process.terminate()" not in source, "a bare terminate() bypasses the group teardown"
    assert "process.kill()" not in source, "a bare kill() bypasses the group teardown too"
    assert source.count("stop_server(process)") >= 3, "both failure paths and main() must use it"


def test_an_exited_wrapper_still_gets_its_group_signalled(spawner):
    """The regression: `stop_server` returned early when `process.poll()` was
    already non-None. During a startup failure `npm` can be gone while Vite is
    still holding `--web-port`, so that early return is exactly the case the
    group signal exists for.

    The group id is recorded rather than looked up, because `getpgid()` on an
    already-reaped child fails — which would silently skip the signal.
    """
    child, _grandchild_pid, port = spawner
    assert _port_is_busy(port), "precondition: the grandchild is listening"

    # Kill only the direct child, exactly as an npm wrapper exiting would. The
    # grandchild is untouched and keeps the port.
    child.kill()
    child.wait(timeout=10)
    assert child.poll() is not None
    time.sleep(0.5)
    assert _port_is_busy(port), "precondition: the grandchild outlived the wrapper"

    CAPTURE.stop_server(child)

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and _port_is_busy(port):
        time.sleep(0.1)
    assert not _port_is_busy(port), "the group was not signalled after the wrapper had exited"
