"""Asset detection for the real-model smoke harnesses.

`scripts/smoke_bge_ingestion.py` is the only gate that can turn "the real BGE
weights have never run here" into an executed result. Its availability check
accepted a bare `config.json`, so an interrupted download was reported as a
present asset: the harness skipped its `EXTERNAL_MODEL_ASSET_REQUIRED` branch
(exit 3), died inside `from_pretrained`, and exited 1 with a traceback — which
reads as a smoke *failure* rather than the missing-asset state it actually is.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.smoke_bge_ingestion import _model_asset_present


def _write_config(directory: Path) -> None:
    (directory / "config.json").write_text(json.dumps({"model_type": "bert"}), encoding="utf-8")


def test_a_missing_directory_is_not_an_asset(tmp_path: Path) -> None:
    assert _model_asset_present(str(tmp_path / "nope")) is False


def test_an_empty_directory_is_not_an_asset(tmp_path: Path) -> None:
    assert _model_asset_present(str(tmp_path)) is False


def test_a_bare_config_json_is_not_an_asset(tmp_path: Path) -> None:
    """The defect: config alone was accepted, so an interrupted download read as ready."""
    _write_config(tmp_path)
    assert _model_asset_present(str(tmp_path)) is False


def test_weights_without_tokenizer_assets_are_not_an_asset(tmp_path: Path) -> None:
    """`AutoTokenizer.from_pretrained` would fail, so the smoke must not start."""
    _write_config(tmp_path)
    (tmp_path / "model.safetensors").write_bytes(b"not-really-weights")
    assert _model_asset_present(str(tmp_path)) is False


@pytest.mark.parametrize("tokenizer_file", ["tokenizer.json", "vocab.txt", "tokenizer_config.json"])
def test_a_complete_asset_directory_is_accepted(tmp_path: Path, tokenizer_file: str) -> None:
    _write_config(tmp_path)
    (tmp_path / "model.safetensors").write_bytes(b"not-really-weights")
    (tmp_path / tokenizer_file).write_text("{}", encoding="utf-8")
    assert _model_asset_present(str(tmp_path)) is True


def test_torch_pickle_weights_count_as_weights(tmp_path: Path) -> None:
    _write_config(tmp_path)
    (tmp_path / "pytorch_model.bin").write_bytes(b"not-really-weights")
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    assert _model_asset_present(str(tmp_path)) is True


def test_the_smoke_reports_a_missing_asset_instead_of_raising(tmp_path: Path) -> None:
    """The documented contract: exit 3, not a traceback."""
    from scripts.smoke_bge_ingestion import run_smoke

    _write_config(tmp_path)
    assert run_smoke(str(tmp_path), 768, 4, None) == 3
