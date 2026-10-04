"""Regression tests for merged-PR discovery in `scripts/branch_protection.sh`.

The defect these guard: `derive` used to read `pulls?state=closed&per_page=20`
and take the first row whose `merged_at` was non-null. That is wrong twice
over. The window is only the 20 most recently closed PRs, so a repository whose
recent PRs were all closed without merging made `derive` fail even though merged
PRs existed. And `merged_at != null` filters a closed list, which infers
merge-ness from a state that does not carry it.

`gh` is stubbed, so these tests are hermetic: no network, no repository writes,
and every request the script makes is recorded for inspection.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "branch_protection.sh"
REPO = "Xander-Xai/Beauty-Industry-RAG-QA-System"
HEAD_SHA = "d303a37cafe0000000000000000000000000beef"

# The contexts the script declares. `derive` must rediscover exactly these to
# report no drift.
DECLARED_CONTEXTS = [
    "Dockerfile 构建校验",
    "前端构建校验",
    "企业就绪配置校验",
    "测试套件 (3.10)",
    "测试套件 (3.11)",
    "评估确定性守卫",
    "Ruff 检查",
    "pip-audit 依赖漏洞扫描",
    "敏感信息扫描",
]

# A `gh` stub that records argv and answers from a JSON config in the
# environment. Any non-GET request is refused, which is how read-only is
# enforced rather than assumed.
STUB_PY = textwrap.dedent(
    """\
    import json, os, sys

    config = json.loads(os.environ["GH_STUB_CONFIG"])
    args = sys.argv[1:]

    if "auth" in args:
        sys.exit(0 if "status" in args else 1)

    method = "GET"
    for flag in ("--method", "-X"):
        if flag in args:
            method = args[args.index(flag) + 1].upper()
    if method != "GET":
        sys.stderr.write("stub gh refused a non-GET request: %s\\n" % method)
        sys.exit(1)

    # `--jq` appears after the endpoint, so scan the whole argv for it.
    jq = None
    for index, arg in enumerate(args):
        if arg in ("--jq", "-q") and index + 1 < len(args):
            jq = args[index + 1]

    # `gh api <endpoint>`: skip the subcommand and the flags, and the value that
    # follows a value-taking flag such as -f.
    skip_next, endpoint = False, ""
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("-"):
            skip_next = arg in ("--jq", "-q", "--method", "-X", "-f", "-F")
            continue
        if arg == "api":
            continue
        endpoint = arg
        break

    def emit(lines):
        sys.stdout.write("".join(line + "\\n" for line in lines))
        sys.exit(0)

    def apply_jq(obj):
        # Apply the two --jq filters this script uses, and nothing else. An
        # unrecognised filter is an error rather than a silent pass-through,
        # so a change in how the script queries shows up as a failed test.
        expr = jq
        if expr == ".head.sha":
            emit([obj["head"]["sha"]])
        if expr.startswith(".check_runs[]") and '!= "skipped"' in expr:
            emit([r["name"] for r in obj["check_runs"] if r["conclusion"] != "skipped"])
        sys.stderr.write("stub gh cannot evaluate --jq %r\\n" % expr)
        sys.exit(1)

    def respond(obj):
        if jq is not None:
            apply_jq(obj)
        json.dump(obj, sys.stdout)
        sys.exit(0)

    if "check-runs" in endpoint:
        respond(config["check_runs"])
    elif endpoint.startswith("search/issues"):
        pages = config["search_pages"]
        # gh --paginate emits pages back to back with no separator.
        sys.stdout.write("".join(json.dumps(p) for p in pages))
        sys.exit(0)
    elif "/pulls/" in endpoint:
        respond(config["head"])
    else:
        sys.stderr.write("stub gh got an unexpected endpoint: %s\\n" % endpoint)
        sys.exit(1)
    """
)


def _pull(number: int, *, merged_at: str | None, updated_at: str) -> dict:
    """One `search/issues` row, shaped like a pull-request search result."""
    return {
        "number": number,
        "updated_at": updated_at,
        "pull_request": {"merged_at": merged_at},
    }


def _page(items: list[dict], total_count: int | None = None) -> dict:
    return {
        "total_count": len(items) if total_count is None else total_count,
        "incomplete_results": False,
        "items": items,
    }


def _check_runs() -> dict:
    return {
        "check_runs": [
            *({"name": name, "conclusion": "success"} for name in DECLARED_CONTEXTS),
            {"name": "RAGAS evaluator smoke（需显式启用）", "conclusion": "skipped"},
        ]
    }


@pytest.fixture
def stub_gh(tmp_path: Path):
    """Build a `gh` stub; returns (bin_dir, run_derive, read_calls)."""

    def build(pages: list[dict]):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        log = bin_dir / "gh-calls.log"

        config = {
            "search_pages": pages,
            "check_runs": _check_runs(),
            "head": {"number": 26, "head": {"sha": HEAD_SHA}},
        }
        (bin_dir / "gh_stub.py").write_text(STUB_PY, encoding="utf-8")

        stub = bin_dir / "gh"
        stub.write_text(
            f'#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> {log}\nexec python3 {bin_dir / "gh_stub.py"} "$@"\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)

        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
        env["GITHUB_REPOSITORY"] = REPO
        env["GH_STUB_CONFIG"] = json.dumps(config)

        def run_derive():
            return subprocess.run(
                ["bash", str(SCRIPT), "derive"],
                capture_output=True,
                text=True,
                env=env,
                cwd=tmp_path,
                check=False,
            )

        def calls() -> str:
            return log.read_text(encoding="utf-8") if log.exists() else ""

        return run_derive, calls

    return build


def test_script_is_syntactically_valid():
    """A syntax error would make every assertion below vacuous."""
    if shutil.which("bash") is None:  # pragma: no cover
        pytest.skip("bash unavailable")
    completed = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr


def test_derive_finds_the_merge_past_twenty_unmerged_closings(stub_gh):
    """The acceptance scenario: PRs 1-25 closed without merge, PR 26 merged.

    The old code read `state=closed&per_page=20`, saw only unmerged rows, and
    reported "no merged pull request found".
    """
    unmerged = [_pull(n, merged_at=None, updated_at=f"2026-10-04T00:{n:02d}:00Z") for n in range(1, 26)]
    merged = _pull(26, merged_at="2026-10-03T09:00:00Z", updated_at="2026-10-03T09:00:00Z")
    run, _ = stub_gh([_page([merged, *unmerged], total_count=1)])

    result = run()

    assert result.returncode == 0, result.stderr
    assert "OK: derived set matches the declared required set." in result.stdout
    assert "#26" in result.stderr
    assert HEAD_SHA in result.stderr


def test_derive_uses_a_merged_filter_not_a_closed_window(stub_gh):
    """Merged-ness must come from a merged filter, never from closed PRs."""
    merged = _pull(26, merged_at="2026-10-03T09:00:00Z", updated_at="2026-10-03T09:00:00Z")
    run, calls = stub_gh([_page([merged])])

    run()

    recorded = calls()
    assert "is:merged" in recorded
    assert "state=closed" not in recorded
    assert "per_page=20" not in recorded


def test_derive_walks_every_page_rather_than_the_first(stub_gh):
    """The merge must still be found when it is not on the first page."""
    old = _pull(9, merged_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z")
    merged = _pull(26, merged_at="2026-10-03T09:00:00Z", updated_at="2026-10-03T09:00:00Z")
    run, calls = stub_gh([_page([old]), _page([merged], total_count=2)])

    result = run()

    assert "--paginate" in calls()
    assert result.returncode == 0, result.stderr
    assert "#26" in result.stderr


def test_derive_selects_by_merged_at_not_by_row_order(stub_gh):
    """A bumped-but-old PR sorted first must not win over the actual latest merge."""
    old_but_recently_touched = _pull(9, merged_at="2026-01-01T00:00:00Z", updated_at="2026-10-05T00:00:00Z")
    latest = _pull(26, merged_at="2026-10-03T09:00:00Z", updated_at="2026-10-03T09:00:00Z")
    run, _ = stub_gh([_page([old_but_recently_touched, latest])])

    result = run()

    assert result.returncode == 0, result.stderr
    assert "#26" in result.stderr


def test_derive_ignores_rows_without_a_merged_at(stub_gh):
    """`is:merged` should already exclude these; a null must not be selected."""
    unmerged = _pull(30, merged_at=None, updated_at="2026-10-06T00:00:00Z")
    merged = _pull(26, merged_at="2026-10-03T09:00:00Z", updated_at="2026-10-03T09:00:00Z")
    run, _ = stub_gh([_page([unmerged, merged])])

    result = run()

    assert result.returncode == 0, result.stderr
    assert "#26" in result.stderr
    assert "#30" not in result.stderr


def test_derive_fails_when_the_repository_never_merged(stub_gh):
    """The one genuine failure: no merged PR exists at all."""
    run, _ = stub_gh([_page([], total_count=0)])

    result = run()

    assert result.returncode != 0
    assert "no merged pull request exists" in result.stderr


def test_derive_warns_when_search_truncates_at_its_result_ceiling(stub_gh):
    """A capped window must be reported, not passed off as the whole set."""
    merged = _pull(26, merged_at="2026-10-03T09:00:00Z", updated_at="2026-10-03T09:00:00Z")
    run, _ = stub_gh([_page([merged], total_count=5000)])

    result = run()

    assert "WARNING" in result.stderr
    assert "5000" in result.stderr


def test_derive_is_read_only(stub_gh):
    """derive must not write. The stub refuses any non-GET request."""
    merged = _pull(26, merged_at="2026-10-03T09:00:00Z", updated_at="2026-10-03T09:00:00Z")
    run, calls = stub_gh([_page([merged])])

    result = run()

    assert result.returncode == 0, result.stderr
    assert "--method" not in calls()
    assert "PUT" not in calls()
    assert "PATCH" not in calls()


def test_derive_reports_a_query_failure_as_a_query_failure(tmp_path: Path):
    """A broken `gh` is not evidence that no merged PR exists."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "gh"
    stub.write_text(
        '#!/usr/bin/env bash\nif [ "$1" = "auth" ]; then exit 0; fi\necho "gh: connection reset" >&2\nexit 1\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["GITHUB_REPOSITORY"] = REPO

    result = subprocess.run(
        ["bash", str(SCRIPT), "derive"],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )

    assert result.returncode != 0
    assert "no merged pull request exists" not in result.stderr
    assert "could not query merged pull requests" in result.stderr
