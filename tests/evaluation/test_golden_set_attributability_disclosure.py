"""The golden-set format validator must disclose unattributability (Issue #86).

The format validator (`tests/evaluation/validate_golden_set.py`) only checks field
presence and shape. Before this change it printed ``301 条数据全部合规`` with exit 0 and
said nothing about the fact that **0 / 301 rows carry a stable identity**, so its
"all compliant" line could be read as "the relevance labels are validated". Those are
different claims: the first is about shape, the second is about whether a retrieval
metric can be attributed.

These tests pin the disclosure and the optional attributability gate. They do **not**
assert a label was created — the dataset is still 0/301 attributable and stays blocked
on a real corpus + human annotation.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import tests.evaluation.validate_golden_set as vgs

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_GOLDEN_SET = REPO_ROOT / "tests" / "evaluation" / "golden_set.jsonl"


# ── the measured state, re-derived rather than hard-coded ───────────────────


def test_format_check_still_passes_on_the_committed_set():
    """The format validator's job is unchanged; it must still pass on shape."""
    assert vgs.validate_golden_set(str(COMMITTED_GOLDEN_SET)) == []


def test_committed_set_is_still_zero_of_301_attributable():
    report = vgs.attributability_report(str(COMMITTED_GOLDEN_SET))

    assert report["total"] == 301
    assert report["valid"] == 0
    assert report["invalid"] == 301
    assert report["attributable"] is False


def test_the_missing_fields_are_named_not_guessed():
    report = vgs.attributability_report(str(COMMITTED_GOLDEN_SET))
    reasons = report["invalid_reasons"]

    # The identity/label gaps issue #86 is about, named machine-readably.
    for expected in (
        "missing_sample_id",
        "missing_annotations",
        "missing_visual_required_label",
        "missing_complexity_label",
        "missing_or_invalid_corpus_version",
    ):
        assert reasons.get(expected) == 301, expected


# ── the CLI discloses the difference ────────────────────────────────────────


def test_cli_discloses_unattributability_on_a_format_pass(capsys):
    exit_code = vgs.main(["--dataset", str(COMMITTED_GOLDEN_SET)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "格式校验通过" in out
    assert "可归因行数: 0 / 301" in out
    # The specific misreading this change prevents.
    assert "相关性标签已验证" in out
    assert "格式校验通过 ≠" in out


def test_cli_does_not_claim_the_relevance_labels_are_validated(capsys):
    vgs.main(["--dataset", str(COMMITTED_GOLDEN_SET)])
    out = capsys.readouterr().out

    # The only place "已验证" may appear is the explicit denial.
    for match in re.finditer(r"相关性标签已验证", out):
        window = out[max(0, match.start() - 20) : match.end()]
        assert "≠" in window, "the tool must not affirm that the labels are validated"


def test_cli_has_an_attributability_gate(capsys):
    exit_code = vgs.main(["--dataset", str(COMMITTED_GOLDEN_SET), "--require-attributable"])
    err = capsys.readouterr().err

    assert exit_code == vgs.EXIT_NOT_ATTRIBUTABLE
    assert "拒绝把该数据集当作检索证据" in err


# ── the gate is not hard-wired to fail ──────────────────────────────────────


def _stub_main(monkeypatch, *, attributable: bool) -> list[str]:
    monkeypatch.setattr(vgs, "validate_golden_set", lambda path: [])
    monkeypatch.setattr(
        vgs,
        "load_golden_set",
        lambda path: [{"question": "q", "business_type": "general", "difficulty": "easy"}],
    )
    monkeypatch.setattr(
        vgs,
        "attributability_report",
        lambda path: {
            "total": 1,
            "valid": 1 if attributable else 0,
            "invalid": 0 if attributable else 1,
            "invalid_reasons": {} if attributable else {"missing_annotations": 1},
            "attributable": attributable,
        },
    )
    return ["--dataset", "stub.jsonl"]


def test_require_attributable_passes_when_the_dataset_is_attributable(monkeypatch, capsys):
    argv = _stub_main(monkeypatch, attributable=True)

    assert vgs.main(argv) == 0
    assert vgs.main([*argv, "--require-attributable"]) == 0
    capsys.readouterr()


def test_require_attributable_fails_when_the_dataset_is_not_attributable(monkeypatch, capsys):
    argv = _stub_main(monkeypatch, attributable=False)

    assert vgs.main(argv) == 0
    assert vgs.main([*argv, "--require-attributable"]) == vgs.EXIT_NOT_ATTRIBUTABLE
    capsys.readouterr()


@pytest.mark.parametrize(
    "bad, reason",
    [
        ("missing-fields.jsonl", "missing"),
    ],
)
def test_missing_file_is_a_format_failure(tmp_path, bad, reason):
    assert vgs.main(["--dataset", str(tmp_path / bad)]) == vgs.EXIT_FORMAT_INVALID
