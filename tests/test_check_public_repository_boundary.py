"""Tests for the public repository boundary guard.

Two things are under test, and the second matters as much as the first:

1. Each rule catches the boundary violation it exists for.
2. Each rule **does not** catch normal engineering vocabulary.

The second is the reason this file exists at all. A guard that has to be
repeatedly loosened because it rejects `Candidate Ranking` or `resume the
ingestion run` is a guard that gets deleted, so the false-positive cases are
asserted explicitly rather than assumed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts.check_public_repository_boundary import (
    DATA_MANIFEST,
    DATA_PROVENANCE_DOC,
    broken_markdown_references,
    career_content_violations,
    career_path_violations,
    data_declaration_errors,
    list_datasets,
    main,
    manifest_errors,
    run_checks,
    secret_violations,
    third_party_identity_violations,
)

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def write(root: Path, relative_path: str, content: str = "") -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


offline_json = "offline/known.json"


def boundary_fixture(tmp_path: Path) -> Path:
    """A minimal tree that satisfies every declaration check."""
    write(tmp_path, DATA_PROVENANCE_DOC, "非关联声明 synthetic 审计")
    write(
        tmp_path,
        DATA_MANIFEST,
        json.dumps({"classes": {"SYNTHETIC": ""}, "assets": []}),
    )
    write(tmp_path, "README.md", f"[data]({DATA_PROVENANCE_DOC})")
    write(tmp_path, "docs/README.md", f"[data](../{DATA_PROVENANCE_DOC})")
    return tmp_path


# --------------------------------------------------------------------------
# 1. Career material — filenames
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "docs/RESUME_CLAIMS.md",
        "docs/finalization/INTERVIEW_GUIDE.md",
        "docs/CV.md",
        "docs/cover-letter.md",
        "docs/JOB_DESCRIPTION.md",
        "docs/candidate-pool.csv",
        "docs/performance-review.md",
    ],
)
def test_career_filenames_are_rejected(tmp_path: Path, name: str) -> None:
    path = write(tmp_path, name, "content")

    assert career_path_violations([path], tmp_path)


@pytest.mark.parametrize(
    "name",
    [
        "README.md",
        "PRD.md",
        "docs/architecture-baseline.md",
        "docs/evidence-map.md",
        "docs/slo-runbook.md",
        "docs/benchmark-data-quality.md",
        "tests/evaluation/golden_set.jsonl",
        "offline/regression_candidates.py",
        "rewrite/query_rewrite.py",
        "docs/repository-metadata.md",
    ],
)
def test_engineering_filenames_are_accepted(tmp_path: Path, name: str) -> None:
    """Filenames in a real engineering tree must not trip the guard."""
    path = write(tmp_path, name, "content")

    assert career_path_violations([path], tmp_path) == []


def test_guard_and_policy_files_may_name_the_patterns() -> None:
    """The guard cannot flag its own pattern table."""
    guard = ROOT_SCRIPT()

    assert career_path_violations([guard], ROOT_SCRIPT().parents[1]) == []


def ROOT_SCRIPT() -> Path:
    return Path(__file__).resolve().parents[1] / "scripts" / "check_public_repository_boundary.py"


# --------------------------------------------------------------------------
# 2. Career material — content
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "本文件是简历的一部分，用于说明个人能力。",
        "该项目用于求职展示，帮助面试官快速了解。",
        "面试准备：常见追问与回答思路。",
        "This repository is post-employment open-source work.",
        "Written as open source after leaving the company.",
        "对外介绍建议使用「企业项目经验 + 离职后个人独立复现」。",
        "A recruiter-facing landing page for this project.",
    ],
)
def test_career_content_is_rejected(tmp_path: Path, line: str) -> None:
    path = write(tmp_path, "docs/notes.md", line)

    assert career_content_violations([path], tmp_path)


@pytest.mark.parametrize(
    "line",
    [
        # The exact false-positive classes the task calls out.
        "Query Rewrite rewrites the question before retrieval.",
        "Candidate Ranking reranks recall candidates with a CrossEncoder.",
        "resume the ingestion run from the sealed snapshot",
        "checkpoint resume after a worker restart",
        "resume_candidates = recall_data.get('results', [])",
        "The recall candidates are passed to the reranker.",
        "化妆品法规与成分限量的知识问答",
        "配方稳定性与防腐体系设计",
    ],
)
def test_technical_vocabulary_is_accepted(tmp_path: Path, line: str) -> None:
    """Normal engineering terms must survive. This is the regression net."""
    path = write(tmp_path, "docs/notes.md", line)

    assert career_content_violations([path], tmp_path) == []


def test_binary_or_jsonl_files_are_not_scanned_for_prose(tmp_path: Path) -> None:
    """Fixtures must not be able to trigger a prose finding."""
    path = write(tmp_path, "tests/evaluation/golden_set.jsonl", "候选人筛选与简历相关的问题")

    assert career_content_violations([path], tmp_path) == []


def test_policy_documents_are_exempt(tmp_path: Path) -> None:
    """DATA_PROVENANCE.md names the audit areas and must be allowed to."""
    path = write(tmp_path, DATA_PROVENANCE_DOC, "本文件审计简历、面试、求职类内容的清除情况。")

    assert career_content_violations([path], tmp_path) == []


def test_scan_reports_file_and_line(tmp_path: Path) -> None:
    path = write(tmp_path, "docs/a.md", "line one\nline two\n这里是简历材料\n")

    findings = career_content_violations([path], tmp_path)

    assert findings == ["docs/a.md:3: résumé material"]


# --------------------------------------------------------------------------
# 3. Third-party identity
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "本项目基于企业「药妆助手」的技术经验复现。",
        "The team moved from Qwen2.5 to Qwen3 in production.",
        "生产推理硬件为 RTX A5000 ×2。",
        "历史生产环境共 3000+ 文档与 5000+ 图片。",
        "平台日均 1500+ 请求，高峰 10-15 QPS。",
        "该项目获得年度技术创新奖。",
        "Derived from L'Oréal's internal knowledge base.",
        "内部用户规模 200+ 人。",
        "w_clip 的 2.0 / 1.0 / 0.0 来自线下网格搜索与线上验证。",
        "阈值 0.85 来自生产环境的 A/B 实验。",
        "Weights derived from live experiments on customer traffic.",
    ],
)
def test_third_party_facts_are_rejected(tmp_path: Path, line: str) -> None:
    path = write(tmp_path, "docs/notes.md", line)

    assert third_party_identity_violations([path], tmp_path)


@pytest.mark.parametrize(
    "line",
    [
        # A design target is allowed; a production fact is not.
        "本 PRD 描述双 GPU 目标架构。",
        "docker-compose.gpu.yml — GPU 服务覆盖层（双卡模式）。",
        "QPS 12–18 为 §2.2 推导模型的设计目标。",
        "P95≈2s 是 SLO 告警阈值（DESIGN_TARGET）。",
        "config.json 的 system.dual_gpu 是一个声明式目标标志。",
        "evidence levels separate implemented from production-validated.",
        "The topology gate compares GPU count.",
        "当前权重为 config.json 中的静态配置值，本仓库没有可复现的调参过程。",
        "Weights are read from config.json; no tuning record is committed.",
    ],
)
def test_design_targets_are_accepted(tmp_path: Path, line: str) -> None:
    """Capacity modelling and design goals stay in scope; only facts go."""
    path = write(tmp_path, "docs/notes.md", line)

    assert third_party_identity_violations([path], tmp_path) == []


# --------------------------------------------------------------------------
# 4. Credentials
# --------------------------------------------------------------------------


# Credential-shaped literals are assembled from fragments at import time.
# GitHub push protection scans blobs for live-looking tokens and will refuse the
# push if a complete one is committed, even inside a test — so the fixture
# proves the regex still fires without shipping a token a scanner can read.
_AWS = "AKIA" + "IOSFODNN7" + "EXAMPLE"
_GITHUB = "ghp_" + "abcdefghijklmnopqrstuvwxyz" + "0123456789"
_SLACK = "xoxb-" + "123456789012-" + "abcdefghijklmnop"
_OPENAI = "sk-" + "abcdefghijklmnopqrstuvwxyz" + "0123456789"
_PEM_HEADER = "-----BEGIN " + "RSA " + "PRIVATE KEY-----"
_CONN_URI = "post" + "gres://admin:" + "s3cr3t@db.internal:5432/rag"


@pytest.mark.parametrize(
    "line",
    [
        f"AWS_KEY = {_AWS}",
        f'GITHUB_TOKEN = "{_GITHUB}"',
        f"SLACK = {_SLACK}",
        _PEM_HEADER,
        f'api_key = "{_OPENAI}"',
        _CONN_URI,
    ],
)
def test_real_credential_shapes_are_rejected(tmp_path: Path, line: str) -> None:
    path = write(tmp_path, "config.json", line)

    assert secret_violations([path], tmp_path)


def test_fragmented_fixtures_still_match_the_patterns() -> None:
    """Guard against an over-broad allow-list silently neutering the rules."""
    import scripts.check_public_repository_boundary as guard

    for value in (_AWS, _GITHUB, _SLACK, _OPENAI, _PEM_HEADER, _CONN_URI):
        assert any(pattern.search(value) for pattern, _ in guard._SECRET_PATTERNS), value


@pytest.mark.parametrize(
    "line",
    [
        "JWT_SECRET=",
        "SERVICE_AUTH_TOKEN=REPLACE_WITH_RANDOM_32_CHARS",
        "ELASTICSEARCH_PASSWORD=REPLACE_WITH_STRONG_PASSWORD",
        'password = "ci-only-not-a-secret"',
        "JWT_SECRET: ${JWT_SECRET_FROM_VAULT}",
    ],
)
def test_placeholders_are_accepted(tmp_path: Path, line: str) -> None:
    """`.env.example` ships placeholders; the guard must not flag them."""
    path = write(tmp_path, ".env.example", line)

    assert secret_violations([path], tmp_path) == []


def test_credential_outside_allowlisted_file_is_still_flagged(tmp_path: Path) -> None:
    """The allow-list is a placeholder allow-list, not a file allow-list."""
    path = write(tmp_path, "app.py", f'api_key = "{_OPENAI}"')

    assert secret_violations([path], tmp_path)


# --------------------------------------------------------------------------
# 5. Data declaration presence
# --------------------------------------------------------------------------


def test_missing_data_provenance_is_an_error(tmp_path: Path) -> None:
    write(tmp_path, "README.md", "no link here")

    errors = data_declaration_errors(tmp_path)

    assert any(DATA_PROVENANCE_DOC in error for error in errors)
    assert any(DATA_MANIFEST in error for error in errors)


def test_readme_must_link_the_statement(tmp_path: Path) -> None:
    boundary_fixture(tmp_path)
    write(tmp_path, "README.md", "no link here")

    assert any("README.md" in error for error in data_declaration_errors(tmp_path))


def test_disclaimer_without_audit_section_is_rejected(tmp_path: Path) -> None:
    """A disclaimer is not a substitute for an audit record."""
    boundary_fixture(tmp_path)
    write(tmp_path, DATA_PROVENANCE_DOC, "这是一份免责声明，synthetic，仅此而已。")

    errors = data_declaration_errors(tmp_path)

    assert any("审计" in error for error in errors)


def test_docs_index_must_link_the_statement(tmp_path: Path) -> None:
    boundary_fixture(tmp_path)
    write(tmp_path, "docs/README.md", "index without the link")

    assert any("docs/README.md" in error for error in data_declaration_errors(tmp_path))


# --------------------------------------------------------------------------
# 6. Manifest consistency
# --------------------------------------------------------------------------


def test_declared_but_missing_asset_is_an_error(tmp_path: Path) -> None:
    boundary_fixture(tmp_path)
    write(tmp_path, DATA_PROVENANCE_DOC, f"非关联声明 synthetic 审计\n{offline_json}\n")
    write(
        tmp_path,
        DATA_MANIFEST,
        json.dumps({"classes": {"SYNTHETIC": ""}, "assets": [{"path": offline_json, "class": "SYNTHETIC"}]}),
    )

    errors = manifest_errors([], tmp_path)

    assert any("missing from the working tree" in error for error in errors)


def _single_asset_manifest(tmp_path: Path, path: str, asset: dict | None = None) -> None:
    """Write a manifest declaring one asset plus a matching provenance line.

    The default entry carries an inline marker so the fixture is otherwise
    valid; tests that assert on a specific manifest error override it.
    """
    boundary_fixture(tmp_path)
    write(tmp_path, path, '{"_disclaimer": "SYNTHETIC DEMO DATA"}')
    asset = asset or {
        "path": path,
        "class": "SYNTHETIC",
        "marker": "_disclaimer",
        "marker_must_contain": ["SYNTHETIC DEMO DATA"],
    }
    write(tmp_path, DATA_PROVENANCE_DOC, f"非关联声明 synthetic 审计\n{path}\n")
    write(
        tmp_path,
        DATA_MANIFEST,
        json.dumps({"classes": {"SYNTHETIC": "", "SCHEMA": ""}, "assets": [asset]}),
    )


def test_unclassified_tracked_data_file_is_an_error(tmp_path: Path) -> None:
    _single_asset_manifest(tmp_path, offline_json, {"path": offline_json, "class": "SYNTHETIC"})
    new_data = write(tmp_path, "tests/evaluation/secret_new_set.jsonl", "{}\n")

    errors = manifest_errors([new_data], tmp_path)

    assert any("secret_new_set.jsonl" in error for error in errors)


def test_data_asset_in_an_unlisted_directory_is_still_caught(tmp_path: Path) -> None:
    """The candidate list must not be a directory allow-list.

    Regression: `tests/integration/testdata/queries.json` shipped unclassified
    because the globs enumerated directories rather than data extensions.
    """
    _single_asset_manifest(tmp_path, offline_json, {"path": offline_json, "class": "SYNTHETIC"})
    new_data = write(tmp_path, "tests/integration/testdata/queries.json", "[]\n")

    errors = manifest_errors([new_data], tmp_path)

    assert any("queries.json" in error for error in errors)


def test_configuration_files_are_excluded_by_path(tmp_path: Path) -> None:
    """`config.json` and lockfiles carry configuration, not an information asset."""
    _single_asset_manifest(tmp_path, offline_json)
    # DATA_MANIFEST.json is written by the fixture above; re-writing it here
    # would replace the declaration under test with an empty one.
    configs = [
        write(tmp_path, "config.json", "{}"),
        write(tmp_path, "frontend/package-lock.json", "{}"),
        write(tmp_path, "monitoring/grafana/dashboards/rag-overview.json", "{}"),
    ]

    assert manifest_errors(configs, tmp_path) == []


def test_synthetic_entry_without_marker_is_an_error(tmp_path: Path) -> None:
    """A `SYNTHETIC` class that names no disclosure proves nothing."""
    _single_asset_manifest(tmp_path, "offline/unmarked.json", {"path": "offline/unmarked.json", "class": "SYNTHETIC"})

    errors = manifest_errors([], tmp_path)

    assert any("declares no 'marker'" in error for error in errors)


def test_asset_missing_from_human_inventory_is_an_error(tmp_path: Path) -> None:
    """The machine manifest and the human document must agree."""
    boundary_fixture(tmp_path)
    write(tmp_path, "offline/extra.json", "{}")
    manifest = {
        "classes": {"SYNTHETIC": ""},
        "assets": [{"path": "offline/extra.json", "class": "SYNTHETIC"}],
    }
    write(tmp_path, DATA_MANIFEST, json.dumps(manifest))

    errors = manifest_errors([], tmp_path)

    assert any("not listed in the human-readable inventory" in error for error in errors)


def test_undeclared_class_is_an_error(tmp_path: Path) -> None:
    boundary_fixture(tmp_path)
    write(tmp_path, "offline/extra.json", "{}")
    write(tmp_path, DATA_PROVENANCE_DOC, "非关联声明 synthetic 审计 offline/extra.json")
    manifest = {
        "classes": {"SYNTHETIC": ""},
        "assets": [{"path": "offline/extra.json", "class": "MADE_UP"}],
    }
    write(tmp_path, DATA_MANIFEST, json.dumps(manifest))

    errors = manifest_errors([], tmp_path)

    assert any("undeclared class" in error for error in errors)


def test_synthetic_asset_without_sidecar_is_an_error(tmp_path: Path) -> None:
    boundary_fixture(tmp_path)
    write(tmp_path, "offline/data.json", "{}")
    write(tmp_path, DATA_PROVENANCE_DOC, "非关联声明 synthetic 审计 offline/data.json")
    manifest = {
        "classes": {"SYNTHETIC": ""},
        "assets": [
            {
                "path": "offline/data.json",
                "class": "SYNTHETIC",
                "marker": "sidecar:offline/DATA_PROVENANCE.md",
            }
        ],
    }
    write(tmp_path, DATA_MANIFEST, json.dumps(manifest))

    errors = manifest_errors([], tmp_path)

    assert any("missing sidecar" in error for error in errors)


def test_inline_marker_phrase_is_required(tmp_path: Path) -> None:
    """The inline marker must actually be present in the asset."""
    boundary_fixture(tmp_path)
    write(tmp_path, "docs/demo/corpus.json", json.dumps({"data": []}))
    write(tmp_path, DATA_PROVENANCE_DOC, "非关联声明 synthetic 审计 docs/demo/corpus.json")
    manifest = {
        "classes": {"SYNTHETIC": ""},
        "assets": [
            {
                "path": "docs/demo/corpus.json",
                "class": "SYNTHETIC",
                "marker": "_disclaimer",
                "marker_must_contain": ["SYNTHETIC DEMO DATA"],
            }
        ],
    }
    write(tmp_path, DATA_MANIFEST, json.dumps(manifest))

    errors = manifest_errors([], tmp_path)

    assert any("synthetic marker phrase" in error for error in errors)


def test_empty_manifest_asset_list_is_an_error(tmp_path: Path) -> None:
    boundary_fixture(tmp_path)

    assert manifest_errors([], tmp_path) == [f"{DATA_MANIFEST}: no assets declared"]


def test_unreadable_manifest_is_reported_not_raised(tmp_path: Path) -> None:
    boundary_fixture(tmp_path)
    write(tmp_path, DATA_MANIFEST, "{not json")

    errors = manifest_errors([], tmp_path)

    assert errors and "unreadable" in errors[0]


# --------------------------------------------------------------------------
# 7. Markdown references
# --------------------------------------------------------------------------


def test_broken_link_in_boundary_doc_is_an_error(tmp_path: Path) -> None:
    boundary_fixture(tmp_path)
    write(tmp_path, DATA_PROVENANCE_DOC, "非关联声明 synthetic 审计\n\n[gone](docs/missing.md)\n")

    errors = broken_markdown_references([], tmp_path)

    assert any("broken local link" in error for error in errors)


def test_external_links_are_not_resolved(tmp_path: Path) -> None:
    boundary_fixture(tmp_path)
    write(
        tmp_path,
        DATA_PROVENANCE_DOC,
        "非关联声明 synthetic 审计\n\n[site](https://example.com/x)\n",
    )

    assert broken_markdown_references([], tmp_path) == []


# --------------------------------------------------------------------------
# 8. Orchestration and CLI
# --------------------------------------------------------------------------


def test_repository_as_committed_passes() -> None:
    """The committed tree must satisfy its own guard."""
    assert run_checks() == []


def test_cli_exits_zero_on_the_committed_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["check_public_repository_boundary.py"])

    assert main() == 0


def test_list_datasets_covers_every_manifest_asset() -> None:
    manifest = json.loads((ROOT_SCRIPT().parents[1] / DATA_MANIFEST).read_text(encoding="utf-8"))
    declared = {asset["path"] for asset in manifest["assets"]}
    expected = set(list_datasets())

    assert expected == declared


def test_tracked_files_falls_back_without_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unpacked archive must still be scannable."""
    import scripts.check_public_repository_boundary as guard

    def boom(*_args, **_kwargs):
        raise OSError("no git here")

    write(tmp_path, "docs/notes.md", "简历")
    monkeypatch.setattr(guard.subprocess, "run", boom)

    found = guard.tracked_files(tmp_path)

    assert tmp_path / "docs/notes.md" in found
