#!/usr/bin/env python3
"""Render the single README demo image: real frontend + synthetic backend.

What this produces
------------------
One image (`docs/assets/demo-request-evidence-flow.webp`) that a recruiter
can read top-to-bottom as:

    用户 Query → 回答 → 引用证据 → 来源文档 → 权限 / 可信证据

How it stays honest
-------------------
* The chat panel is a **real Chromium render of this repository's frontend**
  (`frontend/src/App.jsx`) driven through the real UI: type, click Send, wait
  for the evidence chips.
* Every value comes from `docs/demo/synthetic_corpus.json` — fictional
  document ids, a placeholder CAS number, invented standard names. No real
  regulation text, no proprietary corpus, no production metrics, no secrets.
* The two right-hand cards are labelled demo annotation. Each row points at
  the file that really implements that step, so a reviewer can open it.
* The image is committed as WebP; nothing else from the capture run is kept.

Usage
-----
    python3 docs/demo/capture_demo.py
    python3 docs/demo/capture_demo.py --quality 70   # smaller file

Requires: playwright + chromium, node/npm for the Vite dev server, and
outbound network once to fetch a glyph-subset CJK font (cached under
docs/demo/.build/). If the font cannot be fetched, install a system CJK font
instead (`fonts-noto-cjk`) and re-run with `--no-font-download`.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = Path(__file__).resolve().parent
BUILD_DIR = DEMO_DIR / ".build"
CORPUS_PATH = DEMO_DIR / "synthetic_corpus.json"
DEFAULT_OUT = REPO_ROOT / "docs" / "assets" / "demo-request-evidence-flow.webp"
FRONTEND_DIR = REPO_ROOT / "frontend"

# Width of the composed image. Wide enough that the annotation column keeps
# table rows on one or two lines, which is what keeps the whole thing short.
CANVAS_WIDTH = 1340
CANVAS_HEIGHT = 900
APP_VIEWPORT = {"width": 700, "height": 832}

CHROME_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
FONT_FAMILY = "Noto Sans SC"
CANONICAL_CHARS = (
    "0123456789"
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    " !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~°·—…、。，；：？！（）【】《》“”‘’％✓→↔"
)


# ── font subset ────────────────────────────────────────────────────────
def collect_chars(*sources: str) -> str:
    """Every character the capture can possibly render."""
    chars: set[str] = set(CANONICAL_CHARS)
    for source in sources:
        chars.update(source)
    return "".join(sorted(chars))


def html_visible_text(markup: str) -> str:
    """Text a browser would paint, with markup and style/script bodies dropped."""
    markup = re.sub(r"<(script|style)\b.*?</\1>", " ", markup, flags=re.S | re.I)
    markup = re.sub(r"<[^>]+>", " ", markup)
    return html.unescape(markup)


def fetch_font_css(chars: str, *, allow_download: bool) -> str:
    """Return an @font-face CSS string with an inline base64 woff2 subset."""
    digest = hashlib.sha256(chars.encode("utf-8")).hexdigest()[:16]
    font_dir = BUILD_DIR / "fonts"
    font_path = font_dir / f"noto-sans-sc-{digest}.woff2"

    if not font_path.exists():
        if not allow_download:
            raise SystemExit(
                "CJK font subset missing and --no-font-download was passed.\n"
                "Install a system CJK font (e.g. fonts-noto-cjk) and re-run."
            )
        query = urllib.parse.quote(chars, safe="")
        css_url = f"https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&text={query}"
        print("· fetching glyph-subset CJK font (one-off, cached under docs/demo/.build/)")
        css_req = urllib.request.Request(css_url, headers={"User-Agent": CHROME_UA})
        css_text = (
            urllib.request.urlopen(  # noqa: S310 - fixed https Google Fonts endpoint
                css_req, timeout=60
            )
            .read()
            .decode("utf-8")
        )
        marker = "src: url("
        start = css_text.index(marker) + len(marker)
        font_url = css_text[start : css_text.index(")", start)]
        font_req = urllib.request.Request(font_url, headers={"User-Agent": CHROME_UA})  # noqa: S310
        font_bytes = urllib.request.urlopen(  # noqa: S310 - URL comes from the response above
            font_req, timeout=60
        ).read()
        font_dir.mkdir(parents=True, exist_ok=True)
        font_path.write_bytes(font_bytes)
        print(f"· cached {len(font_bytes) / 1024:.1f} KiB font subset")

    encoded = base64.b64encode(font_path.read_bytes()).decode("ascii")
    faces = "\n".join(
        f"@font-face{{font-family:'{FONT_FAMILY}';font-style:normal;font-weight:{weight};"
        f"src:url(data:font/woff2;base64,{encoded}) format('woff2');}}"
        for weight in (400, 500, 700)
    )
    # The repository stylesheet resolves text through the generic `sans-serif`
    # family, which has no CJK coverage on a bare Linux container. Appending the
    # subset family to every element reproduces the real cascade without
    # editing a single line of `frontend/src/index.css`.
    return (
        faces + f"\n*,*::before,*::after{{font-family:-apple-system,BlinkMacSystemFont,"
        f"'Segoe UI',Roboto,'{FONT_FAMILY}',sans-serif;}}"
    )


# ── local servers ──────────────────────────────────────────────────────
def wait_for_http(url: str, *, timeout: float = 60.0, expect_json: bool = False) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 - loopback readiness probe
                if response.status == 200:
                    if expect_json:
                        json.loads(response.read().decode("utf-8"))
                    return
        except Exception as exc:  # noqa: BLE001 - polling a starting server
            last_error = exc
        time.sleep(0.4)
    raise SystemExit(f"timed out waiting for {url}: {last_error}")


def start_mock_api(port: int, log_path: Path) -> subprocess.Popen:
    handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(DEMO_DIR / "mock_api.py"), "--port", str(port), "--quiet"],
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    wait_for_http(f"http://127.0.0.1:{port}/api/health", expect_json=True)
    return process


def start_frontend(port: int, api_port: int, log_path: Path) -> subprocess.Popen:
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("npm not found — the Vite dev server is required for capture")
    if not (FRONTEND_DIR / "node_modules").exists():
        raise SystemExit("frontend/node_modules missing — run `npm ci` in frontend/")
    handle = log_path.open("w", encoding="utf-8")
    env = dict(os.environ)
    env["VITE_API_PROXY_TARGET"] = f"http://127.0.0.1:{api_port}"
    process = subprocess.Popen(
        [npm, "run", "dev", "--", "--port", str(port), "--strictPort"],
        cwd=str(FRONTEND_DIR),
        stdout=handle,
        stderr=subprocess.STDOUT,
        env=env,
    )
    wait_for_http(f"http://127.0.0.1:{port}/", timeout=90)
    return process


# ── walkthrough markup ─────────────────────────────────────────────────
def step_chip(number: str) -> str:
    return f'<span class="chip">{number}</span>'


def mask_note(identity: dict) -> str:
    """`role_mask=0x04 · dept_mask=0x04`, straight from the corpus."""
    return f"role_mask=0x{int(identity['role_mask']):02x} · dept_mask=0x{int(identity['dept_mask']):02x}"


def render_document_card(doc: dict, *, primary: bool) -> str:
    lines = doc["excerpt"].splitlines()
    truncated = False
    if not primary:
        truncated = len(lines) > 2
        lines = lines[:2]
    highlight = doc.get("highlight", "")
    body = []
    for line in lines:
        escaped = html.escape(line)
        if highlight and line.startswith(highlight):
            body.append(f"<mark>{escaped}</mark>")
        else:
            body.append(escaped)
    if truncated:
        body.append("…")
    # The permission masks are printed on the card because they are the reason
    # the second identity in the left-hand panel is refused: a reader can check
    # the refusal against the masks instead of taking the caption's word.
    meta = (
        f'<div class="doc-meta">{html.escape(doc["kind"])} · status={doc["status"]}'
        f" · epoch={doc['knowledge_version_epoch']}"
        f" · role_mask=0x{int(doc['role_mask']):02x} · dept_mask=0x{int(doc['dept_mask']):02x}</div>"
    )
    stores = f'<div class="doc-stores">离线写入：{html.escape(doc["stores"])}</div>' if primary else ""
    joined = "\n".join(body)
    excerpt = f'<pre class="excerpt">{joined}</pre>'
    doc_id = f'<span class="doc-id">{html.escape(doc["doc_id"])}</span>'
    return (
        f'<div class="doc{" doc-primary" if primary else ""}">'
        f'<div class="doc-head">{doc_id}<span class="doc-title">'
        f"{html.escape(doc['title'])}</span></div>{meta}{excerpt}{stores}</div>"
    )


def render_trace_table(rows: list[dict]) -> str:
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f'<td class="step-cell">{html.escape(row["step"])}</td>'
            f"<td>{html.escape(row['demo'])}</td>"
            f'<td class="code-cell"><code>{html.escape(row["code"])}</code></td>'
            "</tr>"
        )
    return (
        '<table class="trace"><thead><tr><th>环节</th><th>本次请求</th>'
        "<th>实现位置</th></tr></thead><tbody>" + "".join(body) + "</tbody></table>"
    )


def build_walkthrough_html(corpus: dict, app_png_b64: str, font_css: str) -> str:
    docs = corpus["documents"]
    trace = corpus["trace"]
    metadata = corpus["auth_metadata"]
    query_text = corpus["query"]["text"]
    role_options = metadata["rbac"]["role_options"]
    # Turn 1 runs as role_options[0] (the UI default), turn 2 as
    # role_options[3]. `drive_frontend` selects the same two indices, and
    # tests/test_demo_corpus_rbac_consistency.py pins what each one may read.
    privileged, restricted = role_options[0], role_options[3]

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
{font_css}
:root {{
  --bg: #eef0f4; --surface: #ffffff; --ink: #14161a; --muted: #5b6472;
  --line: #dfe3ea; --primary: #4f46e5; --demo: #b42318; --demo-bg: #fef3f2;
  --mono: ui-monospace, "DejaVu Sans Mono", Menlo, Consolas, '{FONT_FAMILY}', monospace;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  width: {CANVAS_WIDTH}px; background: var(--bg); color: var(--ink);
  padding: 26px 26px 20px;
  font-family: -apple-system, "Segoe UI", Roboto, '{FONT_FAMILY}', sans-serif;
  -webkit-font-smoothing: antialiased;
}}
.head {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; }}
.eyebrow {{ font-size: 12.5px; letter-spacing: .09em; text-transform: uppercase;
  color: var(--muted); font-weight: 600; }}
.headline {{ font-size: 22px; font-weight: 700; margin-top: 5px; letter-spacing: -.01em; }}
.headline .arrow {{ color: var(--primary); font-weight: 600; }}
.demo-badge {{
  flex: none; text-align: right; background: var(--demo-bg); color: var(--demo);
  border: 1px solid #fbd5d1; border-radius: 10px; padding: 8px 13px;
  font-size: 13px; font-weight: 700; letter-spacing: .04em;
}}
.demo-badge small {{ display: block; font-weight: 500; font-size: 11.5px;
  letter-spacing: 0; margin-top: 2px; opacity: .9; }}
.grid {{ display: grid; grid-template-columns: 660px 1fr; gap: 18px; margin-top: 16px; }}
.shot {{ background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
  overflow: hidden; box-shadow: 0 1px 2px rgba(16,24,40,.05); }}
.shot img {{ display: block; width: 100%; }}
figcaption {{ padding: 10px 14px; font-size: 12.5px; color: var(--muted);
  border-top: 1px solid var(--line); line-height: 1.55; }}
figcaption code, .foot code {{ font-family: var(--mono); font-size: 11.5px;
  background: #f3f4f7; border-radius: 4px; padding: 1px 4px; }}
.cards {{ display: flex; flex-direction: column; gap: 14px; }}
.card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
  padding: 13px 15px; box-shadow: 0 1px 2px rgba(16,24,40,.05); }}
.card h3 {{ font-size: 14px; font-weight: 700; display: flex; align-items: center; gap: 8px; }}
.card .note {{ font-size: 11.5px; color: var(--muted); margin-top: 3px; }}
.chip {{ display: inline-flex; align-items: center; justify-content: center;
  width: 19px; height: 19px; border-radius: 6px; background: var(--primary);
  color: #fff; font-size: 11.5px; font-weight: 700; flex: none; }}
.doc {{ margin-top: 10px; border: 1px solid var(--line); border-radius: 10px; padding: 9px 11px; }}
.doc-primary {{ border-color: #c7d2fe; background: #f8f9ff; }}
.doc + .doc {{ margin-top: 8px; }}
.doc-head {{ display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }}
.doc-id {{ font-family: var(--mono); font-size: 11.5px; color: var(--primary);
  background: #eef2ff; border-radius: 5px; padding: 1px 6px; font-weight: 700; }}
.doc-title {{ font-size: 12.5px; font-weight: 600; }}
.doc-meta, .doc-stores {{ font-size: 11px; color: var(--muted); margin-top: 3px; }}
.excerpt {{ margin-top: 6px; font-family: var(--mono); font-size: 11.5px;
  line-height: 1.62; white-space: pre-wrap; color: #29303a; }}
mark {{ background: #fef0c7; border-radius: 3px; padding: 0 2px; }}
.trace {{ width: 100%; border-collapse: collapse; margin-top: 9px; font-size: 11.5px; }}
.trace th {{ text-align: left; font-size: 10.5px; letter-spacing: .05em;
  text-transform: uppercase; color: var(--muted); border-bottom: 1px solid var(--line);
  padding: 0 6px 5px 0; font-weight: 600; }}
.trace td {{ padding: 5px 6px 5px 0; border-bottom: 1px solid #f1f3f7;
  vertical-align: top; line-height: 1.5; }}
.trace tr:last-child td {{ border-bottom: 0; }}
.step-cell {{ font-weight: 600; white-space: nowrap; }}
.code-cell {{ white-space: nowrap; }}
.code-cell code {{ font-family: var(--mono); font-size: 10.5px; color: var(--muted); }}
.foot {{ margin-top: 14px; font-size: 11.5px; color: var(--muted); line-height: 1.6; }}
.foot .title {{ font-weight: 600; color: #414a57; }}
</style></head>
<body>
  <div class="head">
    <div>
      <div class="eyebrow">README 演示 · 一次请求的可视化链路</div>
      <div class="headline">用户 Query <span class="arrow">→</span> 回答
        <span class="arrow">→</span> 引用证据 <span class="arrow">→</span> 来源文档
        <span class="arrow">→</span> 权限 / 可信证据</div>
    </div>
    <div class="demo-badge">SYNTHETIC DEMO
      <small>合成数据 · 非生产 · 无凭据</small></div>
  </div>

  <div class="grid">
    <figure class="shot">
      <img src="data:image/png;base64,{app_png_b64}" alt="真实前端界面：用户提问、带引用的回答、引用证据标签">
      <figcaption>{step_chip("123")} <b>真实前端界面（本仓库渲染）</b>：
        第一问以 <code>{html.escape(privileged["label"])}</code> 身份提问
        「{html.escape(query_text[:22])}…」→ 回答用〔证据N〕标注引用，证据标签可点击
        <code>GET /api/media/&#123;doc_id&#125;</code>（服务端二次鉴权 + 审计）。
        <br>{step_chip("5")} 同一问题切到 <code>{html.escape(restricted["label"])}</code>
        （右上角身份选择器，截图结束时所选）→ 按 ④ 的掩码逐份过滤后证据为空，
        Evidence Gate 拒答：界面不给出无出处的答案。
        <br>身份掩码：{html.escape(privileged["label"])}
        <code>{html.escape(mask_note(privileged))}</code> ·
        {html.escape(restricted["label"])}
        <code>{html.escape(mask_note(restricted))}</code>
      </figcaption>
    </figure>

    <div class="cards">
      <section class="card">
        <h3>{step_chip("4")} 来源文档（合成示例）</h3>
        <div class="note">命中的两份示例文档，卡片上的 role_mask / dept_mask 即其访问边界；高亮行是回答引用的条款。</div>
        {render_document_card(docs[0], primary=True)}
        {render_document_card(docs[1], primary=False)}
      </section>

      <section class="card">
        <h3>{step_chip("5")} 权限与可信证据（合成示例）</h3>
        <div class="note">{html.escape(trace["_note"])}</div>
        {render_trace_table(trace["rows"])}
      </section>
    </div>
  </div>

  <div class="foot">
    <span class="title">复现命令</span>
    <code>python3 docs/demo/capture_demo.py</code> ·
    <span class="title">数据来源</span> <code>docs/demo/synthetic_corpus.json</code>
    （虚构文档号 / 占位 CAS / 杜撰标准名）· 截图不含真实数据、凭据或生产指标。
  </div>
</body></html>"""


# ── capture ────────────────────────────────────────────────────────────
def wait_for_fonts(page) -> None:
    """Block until the injected subset font is actually usable."""
    page.evaluate(
        'async (family) => { await document.fonts.load(`16px "${family}"`); await document.fonts.ready; }',
        FONT_FAMILY,
    )


def capture(app_png_b64: str, font_css: str, out_path: Path, quality: int) -> None:
    from playwright.sync_api import sync_playwright

    with CORPUS_PATH.open(encoding="utf-8") as handle:
        corpus = json.load(handle)
    walkthrough = build_walkthrough_html(corpus, app_png_b64, font_css)
    walkthrough_path = BUILD_DIR / "walkthrough.html"
    walkthrough_path.write_text(walkthrough, encoding="utf-8")

    png_path = BUILD_DIR / "walkthrough.png"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--force-color-profile=srgb", "--hide-scrollbars"])
        try:
            page = browser.new_page(
                viewport={"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT},
                device_scale_factor=2,
            )
            page.goto(walkthrough_path.as_uri())
            wait_for_fonts(page)
            page.wait_for_timeout(250)
            png_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(png_path), full_page=True)
        finally:
            browser.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    encode_webp(png_path, out_path, quality)


def encode_webp(png_path: Path, out_path: Path, quality: int) -> None:
    """WebP keeps this text-heavy image around 150-250 KiB; PNG would be ~1 MB."""
    try:
        from PIL import Image
    except ImportError:
        shutil.copyfile(png_path, out_path.with_suffix(".png"))
        print("! Pillow unavailable — wrote PNG instead of WebP", file=sys.stderr)
        return

    with Image.open(png_path) as image:
        image.save(out_path, "WEBP", quality=quality, method=6)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--quality", type=int, default=74, help="WebP quality")
    parser.add_argument("--api-port", type=int, default=8799)
    parser.add_argument("--web-port", type=int, default=3111)
    parser.add_argument("--no-font-download", action="store_true")
    args = parser.parse_args()

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    with CORPUS_PATH.open(encoding="utf-8") as handle:
        corpus = json.load(handle)

    # Glyph coverage has to cover both pages: the real frontend paints whatever
    # the corpus holds, the annotation layer paints whatever the markup holds.
    # Build the markup once with placeholders so the character set is derived
    # from the actual copy instead of a hand-kept list that can drift.
    probe = build_walkthrough_html(corpus, "PNG", "FONT")
    chars = collect_chars(html_visible_text(probe), json.dumps(corpus, ensure_ascii=False))
    font_css = fetch_font_css(chars, allow_download=not args.no_font_download)

    mock_api = None
    frontend = None
    try:
        mock_api = start_mock_api(args.api_port, BUILD_DIR / "mock_api.log")
        frontend = start_frontend(args.web_port, args.api_port, BUILD_DIR / "vite.log")
        app_png_b64 = drive_frontend(args.web_port, font_css)
        capture(app_png_b64, font_css, args.out, args.quality)
    finally:
        for process in (frontend, mock_api):
            if process is not None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()

    size_kib = args.out.stat().st_size / 1024
    try:
        shown = args.out.resolve().relative_to(REPO_ROOT)
    except ValueError:
        shown = args.out
    print(f"· wrote {shown} ({size_kib:.0f} KiB, WebP q{args.quality})")


def drive_frontend(web_port: int, font_css: str) -> str:
    """Drive the real UI twice: the answered turn, then the refused turn."""
    from playwright.sync_api import sync_playwright

    with CORPUS_PATH.open(encoding="utf-8") as handle:
        corpus = json.load(handle)
    query_text = corpus["query"]["text"]
    other_role = corpus["auth_metadata"]["rbac"]["role_options"][3]["label"]

    app_shot = BUILD_DIR / "app-chat.png"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--force-color-profile=srgb", "--hide-scrollbars"])
        try:
            page = browser.new_page(
                viewport=APP_VIEWPORT,
                device_scale_factor=2,
            )
            page.goto(f"http://127.0.0.1:{web_port}/", wait_until="networkidle")
            page.add_style_tag(content=font_css)
            wait_for_fonts(page)

            # Turn 1 — privileged identity, evidence found and cited.
            page.fill(".chat-input", query_text)
            page.click(".btn-send")
            page.wait_for_selector(".message-assistant .evidence-bar", timeout=30_000)
            page.wait_for_selector(".typing-indicator", state="detached", timeout=30_000)

            # Turn 2 — same question, a role without access to those documents.
            page.select_option(".role-select", label=other_role)
            page.fill(".chat-input", query_text)
            page.click(".btn-send")
            page.wait_for_function(
                "document.querySelectorAll('.message-assistant').length >= 2",
                timeout=30_000,
            )
            page.wait_for_selector(".typing-indicator", state="detached", timeout=30_000)

            # The transcript autoscrolls to the newest turn; rewind so both
            # turns stay in the frame.
            page.eval_on_selector(".chat-area", "el => el.scrollTop = 0")
            page.wait_for_timeout(200)
            page.locator(".app").screenshot(path=str(app_shot))
        finally:
            browser.close()

    return base64.b64encode(app_shot.read_bytes()).decode("ascii")


if __name__ == "__main__":
    main()
