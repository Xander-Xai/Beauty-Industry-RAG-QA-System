"""`--no-font-download` must work on a fresh checkout, with no cache and no network.

`docs/demo/capture_demo.py` has two font strategies. The default one downloads a
glyph-subset Noto Sans SC once and caches it under the gitignored
`docs/demo/.build/`. The offline one exists so the hero image can be regenerated
without network access at all.

It used to be unusable for exactly that purpose: it looked for the cached subset
first and exited when the file was absent. Because `docs/demo/.build/` is
gitignored, that meant the documented offline command failed on every fresh
checkout — the only case it was written for — even with a CJK font installed.

These tests pin the offline contract (no cache required, no network call, real
CJK families in the cascade) and keep the default download path intact.

`main()` is driven directly with the servers, Chromium and the encoder stubbed
out, so the branch that actually reads the flag is executed for real while the
run stays fast and offline. Everything after the font decision is replaced, so
nothing here says anything about the rendered image itself.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = REPO_ROOT / "docs" / "demo"
CAPTURE_PATH = DEMO_DIR / "capture_demo.py"
DEMO_README = DEMO_DIR / "README.md"


def _load_capture():
    """Import the standalone capture script by path; `docs/demo` is not a package."""
    spec = importlib.util.spec_from_file_location("demo_capture", CAPTURE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CAPTURE = _load_capture()


class _StubServer:
    """A stand-in for a spawned server.

    `stop_server` is stubbed out alongside this rather than fed one of these: it
    signals a real process *group*, so a duck-typed fake reaching the `killpg`
    call would aim at the test runner's own group. The teardown is covered where
    it can be exercised against real child processes, in
    `tests/test_demo_capture_readiness.py`.
    """

    def __init__(self, label: str):
        self.label = label

    def __repr__(self):
        return f"<stub {self.label}>"


@pytest.fixture
def run_main(tmp_path, monkeypatch):
    """Invoke `main()` for real, with every post-font step replaced.

    Returns the list of URLs the run tried to fetch, so a caller can assert on
    the network decisions the flag is supposed to control.
    """
    monkeypatch.setattr(CAPTURE, "BUILD_DIR", tmp_path / ".build")
    # Recorded rather than discarded: `main()` must hand both servers to
    # `stop_server`, and that is only observable from here.
    stopped: list[str] = []
    monkeypatch.setattr(CAPTURE, "start_mock_api", lambda *a, **k: _StubServer("mock_api"))
    monkeypatch.setattr(CAPTURE, "start_frontend", lambda *a, **k: _StubServer("frontend"))
    monkeypatch.setattr(CAPTURE, "stop_server", lambda process: stopped.append(process.label))
    monkeypatch.setattr(CAPTURE, "drive_frontend", lambda *a, **k: "PNG")

    def _capture(app_png_b64, fonts, out, quality):
        out.write_bytes(b"stub")
        return out

    monkeypatch.setattr(CAPTURE, "capture", _capture)

    calls: list[str] = []

    def _urlopen(request, timeout=None):
        calls.append(request.full_url if hasattr(request, "full_url") else str(request))
        if "fonts.googleapis.com" in calls[-1]:
            return io.BytesIO(
                b"@font-face{font-family:'Noto Sans SC';font-style:normal;font-weight:400;"
                b"src: url(https://fonts.gstatic.com/s/subset.woff2) format('woff2');}"
            )
        return io.BytesIO(b"subset-bytes")

    monkeypatch.setattr(CAPTURE.urllib.request, "urlopen", _urlopen)

    def _run(*flags):
        out = tmp_path / "hero.webp"
        monkeypatch.setattr(sys, "argv", ["capture_demo.py", "--out", str(out), *flags])
        stopped.clear()
        CAPTURE.main()
        _run.stopped = list(stopped)
        return calls

    _run.stopped = []

    return _run


@pytest.fixture
def offline(tmp_path, monkeypatch):
    """An empty build directory plus a network that fails every call.

    The empty directory is the fresh-checkout state, and the hostile `urlopen`
    turns "did not access the network" from an inference into a test failure
    rather than something a future edit could quietly reintroduce.
    """
    monkeypatch.setattr(CAPTURE, "BUILD_DIR", tmp_path / ".build")

    def _no_network(*args, **kwargs):
        raise AssertionError("--no-font-download attempted a network request")

    monkeypatch.setattr(CAPTURE.urllib.request, "urlopen", _no_network)
    return tmp_path / ".build"


# ── the offline flag needs no cached subset ─────────────────────────────
def test_offline_font_plan_resolves_with_an_empty_build_directory(offline):
    """The regression itself: an empty `.build` must be a valid starting state."""
    fonts = CAPTURE.system_font_plan()

    assert fonts.text_stack.strip()
    assert fonts.mono_stack.strip()
    assert fonts.family.strip()


def test_offline_font_plan_writes_nothing_to_the_build_directory(offline):
    """No subset is fetched, so there is nothing to cache. A `fonts/` directory
    appearing here would mean the offline path silently started downloading."""
    CAPTURE.system_font_plan()

    assert not offline.exists(), f"offline run created {offline}"


def test_offline_font_plan_makes_no_network_call(offline):
    """`offline` has poisoned `urlopen`, so simply surviving is the assertion."""
    CAPTURE.system_font_plan()


def _network_calling_functions() -> set[str]:
    """Names of the module-level functions that call `urllib.request.urlopen`."""
    tree = ast.parse(CAPTURE_PATH.read_text(encoding="utf-8"))
    callers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "urlopen"
                and isinstance(inner.func.value, ast.Attribute)
                and inner.func.value.attr == "request"
            ):
                callers.add(node.name)
                break
    return callers


def test_only_the_download_strategy_can_reach_the_network():
    """Structural guard: no future helper may reintroduce a fetch into the
    offline path. `wait_for_http` is exempt because it probes loopback only."""
    callers = _network_calling_functions()

    assert "system_font_plan" not in callers
    assert callers == {"subset_font_plan", "wait_for_http"}, callers


# ── the cascade it hands to Chromium ────────────────────────────────────
def test_system_stack_names_a_cjk_family_for_each_desktop_platform():
    """A stack that only works on the machine that wrote it is not a fallback.
    One family per platform the demo is documented to run on, so the command
    works off a bare Debian container, a Mac and a Windows box alike."""
    stack = CAPTURE.SYSTEM_TEXT_STACK
    for platform_family in (
        "'Noto Sans CJK SC'",  # Linux, fonts-noto-cjk
        "'PingFang SC'",  # macOS
        "'Microsoft YaHei'",  # Windows
    ):
        assert platform_family in stack, f"{platform_family} missing from the system stack"


def test_system_stack_keeps_a_generic_family_last():
    """`sans-serif` is what lets Chromium substitute per glyph for anything the
    named families miss, instead of painting tofu boxes."""
    families = [part.strip() for part in CAPTURE.SYSTEM_TEXT_STACK.split(",")]
    assert families[-1] == "sans-serif", families


def test_mono_stack_carries_its_own_cjk_family():
    """The annotation layer sets file names and masks in monospace, and those
    cells contain Chinese. Without a CJK family the mono stack degrades to tofu
    even when the text stack resolves perfectly."""
    assert "'Noto Sans Mono CJK SC'" in CAPTURE.SYSTEM_MONO_STACK
    assert CAPTURE.SYSTEM_MONO_STACK.rstrip().endswith("monospace")


def test_awaited_family_is_one_the_stack_actually_names():
    """`document.fonts.load` is pointed at `plan.family`; waiting on a family the
    stack never lists would hang on a resolution that cannot succeed."""
    fonts = CAPTURE.system_font_plan()
    assert fonts.family in fonts.text_stack


def test_walkthrough_markup_carries_the_system_stack_and_no_embedded_font():
    """Both pages must resolve through the same stack, and the offline render must
    not smuggle in a base64 `@font-face` from a previous download."""
    with CAPTURE.CORPUS_PATH.open(encoding="utf-8") as handle:
        corpus = json.load(handle)
    markup = CAPTURE.build_walkthrough_html(corpus, "PNG", CAPTURE.system_font_plan())

    assert CAPTURE.SYSTEM_TEXT_STACK in markup
    assert CAPTURE.SYSTEM_MONO_STACK in markup
    assert "@font-face" not in markup
    assert "base64,font/woff2" not in markup


def test_walkthrough_visible_text_does_not_depend_on_the_font_strategy():
    """The flag is a rendering strategy, not a content switch. Two deliberately
    unrelated stacks must yield byte-identical copy, so switching fonts can never
    alter what the hero image says."""
    with CAPTURE.CORPUS_PATH.open(encoding="utf-8") as handle:
        corpus = json.load(handle)

    offline = CAPTURE.build_walkthrough_html(corpus, "PNG", CAPTURE.system_font_plan())
    unrelated = CAPTURE.build_walkthrough_html(
        corpus,
        "PNG",
        CAPTURE.FontPlan(css="", text_stack="Papyrus", mono_stack="Comic Sans MS", family="Papyrus"),
    )

    assert CAPTURE.html_visible_text(offline) == CAPTURE.html_visible_text(unrelated)


# ── the default path still downloads, and caches ────────────────────────
def _fake_google_fonts(monkeypatch, calls: list[str]):
    """Stand in for the two Google Fonts requests the default path makes."""
    css = (
        "@font-face{font-family:'Noto Sans SC';font-style:normal;font-weight:400;"
        "src: url(https://fonts.gstatic.com/s/notosanssc/v41/subset.woff2) format('woff2');}"
    )

    def _urlopen(request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        calls.append(url)
        if "fonts.googleapis.com" in url:
            return io.BytesIO(css.encode("utf-8"))
        return io.BytesIO(b"PK\x03\x04not-really-a-woff2-but-bytes-are-bytes")

    monkeypatch.setattr(CAPTURE.urllib.request, "urlopen", _urlopen)


def test_default_strategy_downloads_and_caches_the_subset(tmp_path, monkeypatch):
    """Requirement of the default mode: fetch once, keep the file for next time."""
    monkeypatch.setattr(CAPTURE, "BUILD_DIR", tmp_path / ".build")
    calls: list[str] = []
    _fake_google_fonts(monkeypatch, calls)

    with CAPTURE.CORPUS_PATH.open(encoding="utf-8") as handle:
        corpus = json.load(handle)
    fonts = CAPTURE.subset_font_plan(CAPTURE.render_charset(corpus))

    cached = sorted((tmp_path / ".build" / "fonts").iterdir())
    assert len(cached) == 1, cached
    assert cached[0].name.startswith("noto-sans-sc-")
    assert fonts.family == CAPTURE.FONT_FAMILY
    assert "@font-face" in fonts.css
    assert len(calls) == 2, calls


def test_default_strategy_reuses_the_cache_on_a_second_run(tmp_path, monkeypatch):
    """The cache is the point of the default mode: a warm `.build` must not hit
    the network again."""
    monkeypatch.setattr(CAPTURE, "BUILD_DIR", tmp_path / ".build")
    calls: list[str] = []
    _fake_google_fonts(monkeypatch, calls)

    with CAPTURE.CORPUS_PATH.open(encoding="utf-8") as handle:
        corpus = json.load(handle)
    chars = CAPTURE.render_charset(corpus)
    first = CAPTURE.subset_font_plan(chars)
    second = CAPTURE.subset_font_plan(chars)

    assert len(calls) == 2, calls
    assert first.css == second.css


# ── the flag the way the documented command passes it ───────────────────
def test_offline_flag_completes_a_whole_run_without_network_or_cache(run_main, tmp_path):
    """The end-to-end shape of the acceptance check: fresh build directory, the
    documented flag, no network. If `main()` ever consults the cache or fetches
    a font on this branch, the poisoned request fails the run."""
    calls = run_main("--no-font-download")

    assert calls == [], calls
    assert not (tmp_path / ".build" / "fonts").exists()
    assert (tmp_path / "hero.webp").exists()


def test_default_run_still_downloads_and_caches_the_subset(run_main, tmp_path):
    """The complement: dropping the flag must keep the documented download."""
    calls = run_main()

    assert len(calls) == 2, calls
    assert "fonts.googleapis.com" in calls[0]
    cached = sorted((tmp_path / ".build" / "fonts").glob("noto-sans-sc-*.woff2"))
    assert len(cached) == 1, cached


def test_default_run_reuses_the_cache_on_a_second_invocation(run_main, tmp_path):
    """A warm `.build` must not re-download."""
    run_main()
    calls = run_main()

    assert len(calls) == 2, calls


def test_the_two_strategies_produce_different_cascades(tmp_path, monkeypatch):
    """Guards against a future edit collapsing the offline stack back onto the
    downloaded subset, which is what made the flag depend on the cache."""
    monkeypatch.setattr(CAPTURE, "BUILD_DIR", tmp_path / ".build")
    _fake_google_fonts(monkeypatch, calls=[])

    with CAPTURE.CORPUS_PATH.open(encoding="utf-8") as handle:
        corpus = json.load(handle)
    offline = CAPTURE.system_font_plan()
    subset = CAPTURE.subset_font_plan(CAPTURE.render_charset(corpus))

    assert "@font-face" not in offline.css
    assert "base64" not in offline.css
    assert offline.text_stack != subset.text_stack
    assert offline.mono_stack != subset.mono_stack


# ── the documentation states the behaviour that exists ──────────────────
def test_demo_readme_documents_the_offline_flag_as_cache_free():
    """A flag documented as "use system CJK fonts" that still needs a prior
    download is exactly the mismatch this suite exists to prevent."""
    text = DEMO_README.read_text(encoding="utf-8")
    assert "--no-font-download" in text

    offline_section = text[text.index("--no-font-download") :]
    assert "系统" in offline_section or "system" in offline_section.lower()


def test_demo_readme_does_not_tell_readers_they_need_the_cache_for_offline_mode():
    """The old wording implied the flag was only usable once a subset had been
    downloaded. Any surviving 'if you have already downloaded' phrasing must go."""
    text = DEMO_README.read_text(encoding="utf-8")
    for stale in ("已缓存", "缓存字体子集后", "缺少缓存"):
        assert stale not in text, f"stale offline-font wording in docs/demo/README.md: {stale!r}"


def test_capture_docstring_matches_the_offline_contract():
    """The module docstring is the `--help` text; it must not promise a cache."""
    doc = CAPTURE.__doc__ or ""
    assert "--no-font-download" in doc
    assert "fresh checkout" in doc


def test_a_run_hands_both_servers_to_the_teardown(run_main):
    """`main()` must stop the frontend as well as the mock.

    `stop_server` signals a whole process group precisely so Vite cannot outlive
    the `npm` wrapper; a run that tore down only one of the two would leave a
    listener holding `--web-port` and fail the next run's free-port check.
    """
    run_main("--no-font-download")

    assert run_main.stopped == ["frontend", "mock_api"]
