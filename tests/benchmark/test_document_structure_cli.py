"""CLI smoke: real offline TXT parsing plus sanitized error reporting."""

import json

from scripts.audit_document_structure import main


def test_real_txt_cli_preflight(tmp_path, capsys):
    source = tmp_path / "approved.txt"
    source.write_text("Verified sample text", encoding="utf-8")
    assert main([str(source)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["document_type"] == "txt"
    assert report["status"] == "PASS_STRUCTURE"
    assert len(report["source_sha256"]) == 64
    assert "Verified sample text" not in json.dumps(report)
    assert str(source) not in json.dumps(report)


def test_cli_missing_source_is_fail_closed_and_redacted(tmp_path, capsys):
    absent = tmp_path / "private-student-identifier.pdf"
    assert main([str(absent)]) == 4
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "FAIL"
    assert report["reasons"] == ["SOURCE_PROCESSING_ERROR"]
    assert "private-student-identifier" not in json.dumps(report)
