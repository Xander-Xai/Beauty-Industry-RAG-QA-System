"""The capture must report the artifact it wrote, not the one it was asked for.

`pillow` is an optional dependency of `docs/demo/capture_demo.py`: it exists to
transcode the Chromium screenshot into WebP, and without it the PNG is simply
copied next to the requested path. The fallback itself was fine. The reporting was
not — `main()` ignored what the encoder produced and went on to `stat()` the
requested `.webp`, so on any machine without Pillow the run rendered everything,
threw away the result, and died on the last line with `FileNotFoundError`. An
optional dependency turned a working command into a guaranteed failure.

These tests pin the contract from both ends:

* the encoders return the path they actually wrote, so a caller has something
  honest to report;
* `main()` reports, sizes and formats that returned path, so a PNG fallback ends
  in a clean exit instead of a missing-file traceback;
* PNG bytes are never parked under a `.webp` name, which would look like a valid
  asset to git and to any Markdown renderer until something tried to decode it.

`main()` is driven directly with the two servers and Chromium stubbed out, so the
reporting path runs for real while the suite stays fast and offline. `capture()`
itself needs a browser, so the one wire this suite cannot execute — that it returns
the encoder's result — is checked structurally instead.
"""

from __future__ import annotations

import ast
import base64
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = REPO_ROOT / "docs" / "demo"
CAPTURE_PATH = DEMO_DIR / "capture_demo.py"
DEMO_README = DEMO_DIR / "README.md"

# A real 1x1 PNG. Embedded rather than generated so this suite needs neither
# Pillow nor a network, and so the Pillow-present branch decodes an actual image
# instead of passing only because the bytes were never looked at.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
WEBP_MAGIC = b"RIFF"


def _load_capture():
    """Import the standalone capture script by path; `docs/demo` is not a package."""
    spec = importlib.util.spec_from_file_location("demo_capture_pillow", CAPTURE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CAPTURE = _load_capture()


class _StubServer:
    """Stands in for the mock API / Vite subprocesses `main()` tears down."""

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def no_pillow(monkeypatch):
    """Make `from PIL import Image` fail exactly as it does without Pillow.

    A `None` entry in `sys.modules` is how the import system is told a package is
    deliberately unavailable: it raises `ImportError`, which is the branch
    `encode_webp` is written against. Blocking the import rather than faking the
    encoder keeps the fallback under test instead of a mock of it.
    """
    monkeypatch.setitem(sys.modules, "PIL", None)
    monkeypatch.delitem(sys.modules, "PIL.Image", raising=False)


@pytest.fixture
def run_main(tmp_path, monkeypatch):
    """Invoke `main()` for real, with the servers and the browser stubbed out.

    The `capture()` stand-in stops where Chromium would start and then calls the
    real `encode_webp`, so the encoder and everything `main()` does with its return
    value execute for real. Returns the `--out` path so a caller can check what the
    run left on disk.
    """
    monkeypatch.setattr(CAPTURE, "BUILD_DIR", tmp_path / ".build")
    monkeypatch.setattr(CAPTURE, "start_mock_api", lambda *a, **k: _StubServer())
    monkeypatch.setattr(CAPTURE, "start_frontend", lambda *a, **k: _StubServer())
    monkeypatch.setattr(CAPTURE, "drive_frontend", lambda *a, **k: "PNG")

    def _capture(app_png_b64, fonts, out, quality):
        shot = tmp_path / ".build" / "walkthrough.png"
        shot.parent.mkdir(parents=True, exist_ok=True)
        shot.write_bytes(TINY_PNG)
        return CAPTURE.encode_webp(shot, out, quality)

    monkeypatch.setattr(CAPTURE, "capture", _capture)

    out = tmp_path / "hero.webp"

    def _run(*extra_flags):
        # `--no-font-download` keeps the run offline; the font choice has nothing
        # to do with which artifact the encoder produces.
        monkeypatch.setattr(sys, "argv", ["capture_demo.py", "--out", str(out), "--no-font-download", *extra_flags])
        CAPTURE.main()
        return out

    return _run


# ── the encoder returns the file it wrote ───────────────────────────────
def test_encode_webp_returns_the_requested_webp_when_pillow_is_installed(tmp_path):
    """With the encoder available the answer is the requested path, unchanged."""
    pytest.importorskip("PIL")
    shot = tmp_path / "walkthrough.png"
    shot.write_bytes(TINY_PNG)
    out = tmp_path / "hero.webp"

    written = CAPTURE.encode_webp(shot, out, 74)

    assert written == out
    assert written.exists()
    assert written.read_bytes()[:4] == WEBP_MAGIC


def test_encode_webp_returns_the_sibling_png_when_pillow_is_missing(tmp_path, no_pillow):
    """The fallback artifact is the `.png` beside the requested `.webp`."""
    shot = tmp_path / "walkthrough.png"
    shot.write_bytes(TINY_PNG)
    out = tmp_path / "hero.webp"

    written = CAPTURE.encode_webp(shot, out, 74)

    assert written == tmp_path / "hero.png"
    assert written.exists()
    assert written.read_bytes() == TINY_PNG


def test_a_missing_pillow_never_parks_png_bytes_under_the_webp_name(tmp_path, no_pillow):
    """A `.webp` holding PNG bytes survives `git`, Markdown and the next capture
    run; it only fails once something decodes it. The name has to match the bytes.
    """
    shot = tmp_path / "walkthrough.png"
    shot.write_bytes(TINY_PNG)
    out = tmp_path / "hero.webp"

    CAPTURE.encode_webp(shot, out, 74)

    assert not out.exists(), f"{out} was created without an encoder to write it"
    assert (tmp_path / "hero.png").read_bytes()[:8] == PNG_MAGIC


def test_capture_hands_the_encoders_path_back_to_its_caller():
    """The wire this suite cannot execute: `capture()` needs Chromium.

    If a future edit drops that `return` — or returns `out_path` — `main()` goes
    back to reporting a file nobody wrote, which is the original bug.
    """
    tree = ast.parse(CAPTURE_PATH.read_text(encoding="utf-8"))
    capture_fn = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "capture")
    returns = [node for node in ast.walk(capture_fn) if isinstance(node, ast.Return) and node.value]

    assert returns, "capture() returns nothing"
    for node in returns:
        call = node.value
        assert isinstance(call, ast.Call), ast.dump(node)
        assert getattr(call.func, "id", None) == "encode_webp", ast.dump(node)


# ── main() reports the artifact that exists ─────────────────────────────
def test_a_run_without_pillow_finishes_and_writes_the_png_fallback(run_main, no_pillow, capsys):
    """The regression: the whole run completes and the PNG is the file on disk.

    `main()` sizing the requested `.webp` instead of what the encoder returned is
    exactly the `FileNotFoundError` this replaces, so reaching the assertions at
    all is part of the assertion.
    """
    out = run_main()

    assert not out.exists(), "the .webp must not appear when nothing could encode one"
    fallback = out.with_suffix(".png")
    assert fallback.exists()
    assert fallback.read_bytes() == TINY_PNG

    printed = capsys.readouterr()
    assert fallback.name in printed.out, printed.out
    assert "WebP" not in printed.out, printed.out
    assert "PNG" in printed.out, printed.out
    assert "Pillow" in printed.err, printed.err


def test_a_run_without_pillow_reports_a_size_and_not_a_quality_setting(run_main, no_pillow, capsys):
    """The `q74` in the completion line is a WebP encoder setting. Printing it for
    a PNG claims an encoder ran that never did."""
    run_main()

    printed = capsys.readouterr().out
    assert "KiB" in printed, printed
    assert "q74" not in printed, printed


def test_a_run_with_pillow_reports_the_webp_it_requested(run_main, capsys):
    """The fallback must not have cost the normal path its own reporting."""
    pytest.importorskip("PIL")

    out = run_main()

    assert out.exists()
    assert out.read_bytes()[:4] == WEBP_MAGIC
    printed = capsys.readouterr().out
    assert out.name in printed, printed
    assert "WebP q74" in printed, printed
    assert not out.with_suffix(".png").exists()


def test_the_fallback_lands_beside_the_requested_path(tmp_path, no_pillow):
    """`--out` may point anywhere. The fallback has to be a sibling of *that* path,
    or the completion line names a file the caller was never told about."""
    shot = tmp_path / "walkthrough.png"
    shot.write_bytes(TINY_PNG)
    out = tmp_path / "scratch" / "hero.webp"
    out.parent.mkdir(parents=True)

    written = CAPTURE.encode_webp(shot, out, 74)

    assert written == out.parent / "hero.png"
    assert written != CAPTURE.DEFAULT_OUT.with_suffix(".png")
    assert written.exists()


# ── the documentation states the behaviour that exists ──────────────────
def test_demo_readme_separates_the_committed_asset_from_the_local_fallback():
    """A README that says "pillow optional" without saying what lands on disk
    leaves a reader unable to tell a PNG fallback from a broken WebP."""
    text = DEMO_README.read_text(encoding="utf-8")

    assert "hero.png" in text or "同名" in text
    assert "WebP" in text
    assert "不提交" in text or "仅供本地" in text


def test_demo_readme_forbids_mislabelled_png_bytes():
    """The honesty rule is stated, not just implemented."""
    text = DEMO_README.read_text(encoding="utf-8")

    assert "PNG 字节" in text
    assert "文件名" in text


def test_capture_docstring_documents_the_fallback():
    """The module docstring is the `--help` text, so it is where someone meets
    this behaviour before running into it."""
    doc = CAPTURE.__doc__ or ""

    assert "Pillow is optional" in doc
    assert "never written under the" in doc


# ── only WebP is encoded, so only .webp may be requested ────────────────
def _out_is_refused(monkeypatch, tmp_path, name: str):
    """Ask `main()` for `name` and return the refusal, if any.

    The validation runs before anything is spawned, so no stubbing is needed
    beyond making sure a refusal really is a refusal and not a crash later on.
    """
    monkeypatch.setattr(
        sys,
        "argv",
        ["capture_demo.py", "--out", str(tmp_path / name), "--no-font-download"],
    )
    with pytest.raises(SystemExit) as excinfo:
        CAPTURE.main()
    return str(excinfo.value)


def test_a_non_webp_output_path_is_refused(tmp_path, monkeypatch):
    """The regression: `--out hero.png` wrote WebP bytes into a file named
    `.png`, and the completion line then reported it as PNG — the precise
    extension/content mismatch the fallback path refuses to create."""
    message = _out_is_refused(monkeypatch, tmp_path, "hero.png")

    assert "must end in .webp" in message, message
    assert not (tmp_path / "hero.png").exists()


@pytest.mark.parametrize("name", ["hero.png", "hero.jpg", "hero.webp2", "hero"])
def test_every_non_webp_suffix_is_refused(name, tmp_path, monkeypatch):
    """Not just `.png`. Anything the encoder will not actually produce is
    refused, including a bare name with no suffix at all."""
    assert "must end in .webp" in _out_is_refused(monkeypatch, tmp_path, name)


def test_a_webp_path_with_odd_casing_is_accepted(tmp_path, monkeypatch):
    """The check is case-insensitive on purpose: refusing `hero.WEBP` would be
    pedantry, and the encoder's output does not care what case the suffix is."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["capture_demo.py", "--out", str(tmp_path / "hero.WEBP"), "--no-font-download"],
    )
    monkeypatch.setattr(CAPTURE, "start_mock_api", lambda *a, **k: _StubServer())
    monkeypatch.setattr(CAPTURE, "start_frontend", lambda *a, **k: _StubServer())
    monkeypatch.setattr(CAPTURE, "drive_frontend", lambda *a, **k: "PNG")
    monkeypatch.setattr(CAPTURE, "capture", lambda app, fonts, out, quality: (out.write_bytes(b"x"), out)[1])

    CAPTURE.main()
    assert (tmp_path / "hero.WEBP").exists()


def test_the_default_output_path_is_webp():
    """The guard is only safe because the default satisfies it."""
    assert CAPTURE.DEFAULT_OUT.suffix == ".webp"


def test_capture_docstring_states_that_only_webp_is_encoded():
    doc = CAPTURE.__doc__ or ""
    assert "Only WebP is encoded" in doc
    assert "--out` must end in `.webp`" in doc


# ── the reproduction path is installable from a clean checkout ───────────
DEMO_REQUIREMENTS = REPO_ROOT / "docs" / "demo" / "requirements.txt"
DEMO_DIR = REPO_ROOT / "docs" / "demo"


def test_playwright_is_declared_for_the_demo():
    """The regression: `playwright install chromium` was documented, but the
    Python package was in no requirements file, so on a clean checkout with the
    documented Python dependencies installed the capture died with
    `ModuleNotFoundError: No module named 'playwright'`."""
    assert DEMO_REQUIREMENTS.is_file(), "docs/demo/requirements.txt must exist"
    text = DEMO_REQUIREMENTS.read_text(encoding="utf-8")
    assert "playwright" in text, text


def test_the_demo_requirements_file_is_named_where_a_reader_looks():
    """A file nobody is told about fixes nothing."""
    readme = DEMO_README.read_text(encoding="utf-8")
    assert "docs/demo/requirements.txt" in readme, "docs/demo/README.md must point at the demo requirements"


def test_the_browser_install_step_comes_after_the_package_install():
    """`playwright install chromium` needs the CLI to exist already. Swapping
    the two steps is the exact order a reader would guess."""
    readme = DEMO_README.read_text(encoding="utf-8")
    install = readme.index("pip install -r docs/demo/requirements.txt")
    browser = readme.index("playwright install chromium")
    assert install < browser, "the package must be installed before the browser binaries"


def test_playwright_is_not_pulled_into_the_service_requirements():
    """The service never imports playwright; making every service install carry
    a browser automation stack would be the wrong fix for a demo-only need."""
    service_requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "playwright" not in service_requirements.lower()


def test_the_capture_script_does_import_playwright():
    """Confirms the split is real: the dependency belongs to the demo, and the
    demo is the only thing that needs it."""
    assert "from playwright.sync_api import" in CAPTURE_PATH.read_text(encoding="utf-8")
