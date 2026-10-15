"""The QLoRA training output must carry its input's provenance.

`offline/finetune_data.json` is declared SYNTHETIC and disclosed in
`offline/DATA_PROVENANCE.md`. That disclosure covers the *input*. The *output* is
the part that leaves the machine: the adapter is copied into `models/`, loaded by
vLLM and attached to a serving stack, while the fixture it was trained on stays
behind. A model card listing hyperparameters and a data path, with no statement
of what that path contains, invites exactly the wrong conclusion.

Scope and honesty note: no GPU training is run here and no adapter weights exist
in this repository. These tests exercise the disclosure builder and drive the
real model-card writer with mocked arguments. They verify that the statement is
produced and is correct; they do not and cannot verify anything about the model's
behaviour.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.provenance import SYNTHETIC_DATA_CLASS, UNCLASSIFIED_DATA_CLASS
from offline.training_provenance import (
    TRAINING_PROVENANCE_FILENAME,
    TRAINING_PROVENANCE_SCHEMA_VERSION,
    build_training_provenance,
    render_markdown_section,
    write_training_provenance,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FINETUNE_DATA = "offline/finetune_data.json"


def _provenance(**overrides: Any) -> dict:
    kwargs: dict[str, Any] = {"data_path": FINETUNE_DATA, "output_dir": "/tmp/adapter"}
    kwargs.update(overrides)
    return build_training_provenance(**kwargs)


# --- input classification is derived, never assumed -------------------------


def test_registered_synthetic_training_data_is_labelled_synthetic() -> None:
    training_input = _provenance()["training_input"]

    assert training_input["data_class"] == SYNTHETIC_DATA_CLASS
    assert training_input["carries_real_regulatory_claims"] is False
    assert "虚构占位值" in training_input["notice"]


def test_unregistered_training_data_is_never_defaulted_to_synthetic(tmp_path: Path) -> None:
    """`--data_path` accepts any JSON. Unknown input must not become 'synthetic'."""
    foreign = tmp_path / "operator-corpus.json"
    foreign.write_text(json.dumps([{"instruction": "a", "input": "b", "output": "c"}]), encoding="utf-8")

    training_input = _provenance(data_path=str(foreign))["training_input"]

    assert training_input["data_class"] == UNCLASSIFIED_DATA_CLASS
    assert training_input["carries_real_regulatory_claims"] is None
    assert "unconfirmed" in training_input["resolution"]


def test_unregistered_training_data_is_never_called_compliance_capable(tmp_path: Path) -> None:
    foreign = tmp_path / "licensed.json"
    foreign.write_text(json.dumps([{"instruction": "a", "input": "b", "output": "c"}]), encoding="utf-8")

    restrictions = _provenance(data_path=str(foreign))["usage_restrictions"]

    assert any("未经登记" in restriction for restriction in restrictions)
    assert not any("合规判定" in restriction for restriction in restrictions)


def test_a_registered_non_synthetic_class_is_not_called_unregistered(monkeypatch: pytest.MonkeyPatch) -> None:
    """A future licensed corpus must not produce a self-contradicting card.

    Registered-but-not-SYNTHETIC is the case that was previously collapsed into
    the synthetic branch: it received the synthetic compliance ban, and its
    empty manifest notice was backfilled with "this path is not registered",
    which is plainly false about a path that *is* in the manifest.
    """
    monkeypatch.setattr(
        "offline.training_provenance.dataset_data_provenance",
        lambda path: {
            "data_class": "LICENSED_CORPUS",
            "carries_real_regulatory_claims": None,
            "notice": "",  # exactly what the manifest lookup returns for this class
        },
    )

    block = _provenance()

    assert block["training_input"]["data_class"] == "LICENSED_CORPUS"
    assert block["training_input"]["notice"] == ""
    assert block["training_input"]["resolution"] == "resolved from DATA_MANIFEST.json"
    # Neither the synthetic ban nor the unconfirmed warning, because neither applies.
    joined = " ".join(block["usage_restrictions"])
    assert "配方合规判断" not in joined
    assert "未经登记" not in joined
    # The capability disclaimer is unconditional.
    assert block["capability_evidence"]["regulatory_correctness_evaluated"] is False


def test_missing_training_file_reports_no_digest_instead_of_a_fake_one() -> None:
    """An unreadable corpus has no SHA-256; the digest of "" is not one."""
    training_input = _provenance(data_path="/tmp/definitely-not-here.json")["training_input"]

    assert training_input["sha256"] is None
    assert training_input["records"] is None


def test_training_data_digest_is_the_real_file_digest() -> None:
    from benchmarks.provenance import sha256_bytes

    training_input = _provenance()["training_input"]

    assert training_input["sha256"] == sha256_bytes((REPO_ROOT / FINETUNE_DATA).read_bytes())
    assert training_input["records"] == 40


# --- the three things that get conflated are kept apart ---------------------


def test_training_input_run_and_capability_evidence_are_separate() -> None:
    """Conflating them is the failure: 'we trained it' reads as 'it is correct'."""
    provenance = _provenance(base_model="./models/Qwen3-14B-Instruct", quantization="4-bit nf4")

    assert set(provenance) >= {"training_input", "training_run", "capability_evidence"}
    assert provenance["training_input"]["path"] == FINETUNE_DATA
    assert provenance["training_run"]["base_model"] == "./models/Qwen3-14B-Instruct"
    # Capability evidence is a separate section and it is empty.
    assert provenance["capability_evidence"]["regulatory_correctness_evaluated"] is False


def test_a_completed_run_never_asserts_regulatory_correctness() -> None:
    """Passing every hyper-parameter in must not produce a capability claim."""
    provenance = _provenance(
        base_model="./models/Qwen3-14B-Instruct",
        quantization="4-bit nf4",
        lora={"rank": 16, "alpha": 32},
        hyperparameters={"num_epochs": 3, "learning_rate": 2e-4},
        dataset_size=40,
    )

    capability = provenance["capability_evidence"]
    assert capability["regulatory_correctness_evaluated"] is False
    assert capability["benchmark"] is None
    assert capability["reviewed_by"] is None
    assert any("训练执行成功不代表法规能力已获证明" in r for r in provenance["usage_restrictions"])


def test_training_run_is_marked_as_self_reported() -> None:
    """The run block is the script describing its own arguments, not a witness."""
    provenance = _provenance(lora={"rank": 16})

    assert "Not independently reproduced" in provenance["training_run"]["evidence"]
    assert provenance["training_run"]["lora"] == {"rank": 16}


# --- the banned use, stated explicitly --------------------------------------


def test_synthetic_training_output_bans_compliance_judgement() -> None:
    restrictions = _provenance()["usage_restrictions"]

    joined = " ".join(restrictions)
    assert "配方合规判断" in joined
    assert "成分限用判定" in joined
    assert "Must not be used for real cosmetic formulation compliance" in joined


def test_markdown_section_leads_with_the_notice() -> None:
    """The Markdown is what a human actually reads; it cannot be a footnote."""
    section = render_markdown_section(_provenance(base_model="./models/Qwen3-14B-Instruct"))

    lines = [line for line in section.splitlines() if line.strip()]
    assert "虚构占位值" in lines[1]
    assert lines.index(next(line for line in lines if "虚构占位值" in line)) < next(
        i for i, line in enumerate(lines) if line.startswith("- **base model**")
    )


def test_markdown_names_the_unverified_state_for_a_human() -> None:
    section = render_markdown_section(_provenance())

    assert "regulatory correctness evaluated: **false**" in section
    assert "benchmark: none" in section
    assert "reviewed by: nobody" in section
    assert TRAINING_PROVENANCE_FILENAME in section


# --- the sidecar -----------------------------------------------------------


def test_sidecar_is_written_next_to_the_adapter(tmp_path: Path) -> None:
    provenance = _provenance(output_dir=str(tmp_path))
    written = write_training_provenance(str(tmp_path), provenance)

    assert written == tmp_path / TRAINING_PROVENANCE_FILENAME
    assert json.loads(written.read_text(encoding="utf-8")) == provenance


def test_sidecar_is_machine_readable_and_versioned(tmp_path: Path) -> None:
    written = write_training_provenance(str(tmp_path), _provenance())
    payload = json.loads(written.read_text(encoding="utf-8"))

    assert payload["schema_version"] == TRAINING_PROVENANCE_SCHEMA_VERSION
    assert payload["generated_by"] == "offline/finetune_qlora.py"


# --- the real writer, with mocked arguments ---------------------------------


def test_model_card_writer_emits_both_the_card_and_the_sidecar(tmp_path: Path) -> None:
    """Drives the writer that a real training run calls. No GPU, no weights."""
    from offline.finetune_qlora import _write_model_card
    from tests.test_finetune_pipeline import mock_data_args, mock_lora_args, mock_model_args

    args = mock_data_args(output_dir=str(tmp_path), data_path=FINETUNE_DATA)

    _write_model_card(args, mock_lora_args(), mock_model_args(), dataset_size=40)

    card = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "虚构占位值" in card
    assert "不得用于" in card
    assert "regulatory correctness evaluated: **false**" in card
    # The pre-existing model card contract is untouched.
    assert "QLoRA Adapter" in card
    assert "40 examples" in card
    assert "vLLM" in card

    sidecar = json.loads((tmp_path / TRAINING_PROVENANCE_FILENAME).read_text(encoding="utf-8"))
    assert sidecar["training_input"]["data_class"] == SYNTHETIC_DATA_CLASS


def test_model_card_writer_follows_an_unregistered_data_path(tmp_path: Path) -> None:
    from offline.finetune_qlora import _write_model_card
    from tests.test_finetune_pipeline import mock_data_args, mock_lora_args, mock_model_args

    foreign = tmp_path / "operator.json"
    foreign.write_text(json.dumps([{"instruction": "a", "input": "b", "output": "c"}]), encoding="utf-8")
    args = mock_data_args(output_dir=str(tmp_path), data_path=str(foreign))

    _write_model_card(args, mock_lora_args(), mock_model_args(), dataset_size=1)

    card = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert f"`{UNCLASSIFIED_DATA_CLASS}`" in card
    assert "虚构占位值" not in card
