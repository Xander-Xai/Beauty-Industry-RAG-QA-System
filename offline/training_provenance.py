"""Provenance for QLoRA training output.

Why this module exists
----------------------
``offline/finetune_qlora.py`` trains on ``finetune_data.json``, which
``DATA_MANIFEST.json`` classifies as ``SYNTHETIC`` and ``offline/DATA_PROVENANCE.md``
describes as fabricated regulatory placeholder text. The *input* is disclosed
there. The **output** was not: the adapter directory contained a ``README.md``
model card listing the training data path and nothing about what that path
contains, and no machine-readable manifest at all.

A LoRA adapter is the part of the pipeline that leaves the machine. It gets
copied into ``models/``, loaded by vLLM, and attached to a serving stack, so it
is far more likely to be mistaken for a validated component than the fixture it
was trained on is. A reader who finds an adapter directory sees a model card
with hyperparameters and a training data path, and reasonably concludes the
regulatory content was checked — it was not, and no step in this repository
checks it.

What this module records
------------------------
Three things that are routinely conflated, kept separate on purpose:

1. ``training_input`` — what the data *is*: its class and SHA-256, resolved
   through the same ``DATA_MANIFEST.json`` lookup the benchmark and annotation
   writers use, so there is one classification system and not several.
2. ``training_run`` — what the script *did*: base model, LoRA hyper-parameters,
   epoch count. Recorded by the training process at save time and explicitly
   not independently reproduced.
3. ``capability_evidence`` — what anyone has *demonstrated* about the adapter.
   Always ``null`` with a stated reason, because training output is not evidence
   of regulatory correctness and no evaluation in this repository produces one.

Nothing here writes a label, and nothing here reads a weight file. It is
deliberately free of torch/transformers/peft imports so the disclosure can be
tested on any machine, including one that has never run a training job.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.provenance import (  # noqa: E402
    UNCLASSIFIED_DATA_CLASS,
    UNCLASSIFIED_DATA_NOTICE,
    dataset_data_provenance,
    sha256_bytes,
)

TRAINING_PROVENANCE_SCHEMA_VERSION = "training-provenance/v1"

#: File name of the machine-readable sidecar written next to the adapter.
TRAINING_PROVENANCE_FILENAME = "DATA_PROVENANCE.json"

#: Banned uses for an adapter trained on this repository's fixture. Keyed off the
#: training-input class, so the text cannot outlive the classification it was
#: derived from. The wording is "must not be used for", not "is not suitable
#: for": the fixture data was never evaluated against regulatory truth, so its
#: fidelity to real regulation is unknown rather than merely low.
SYNTHETIC_TRAINING_USAGE_RESTRICTIONS = (
    "不得将本训练产物用于真实化妆品配方合规判断、成分限用判定或对外法规答复 / "
    "Must not be used for real cosmetic formulation compliance, ingredient-restriction "
    "determination, or any regulatory answer given to a consumer."
)

UNCLASSIFIED_TRAINING_USAGE_RESTRICTIONS = (
    "训练数据来源未经登记，其数据性质未确认；不得将本训练产物宣称为已核验的法规能力模型 / "
    "The training data's provenance is unconfirmed; this output may not be presented as a "
    "regulatory-capability model that has been verified."
)

_NEVER_CLAIMED = (
    "No evaluation in this repository measures regulatory correctness. A completed training "
    "run records that an optimisation converged, not that any answer it produces is legally "
    "or technically correct."
)


def _file_sha256(path: Path) -> str | None:
    """SHA-256 of a file, or ``None`` when it cannot be read.

    ``None`` is the honest answer for a missing or unreadable corpus; it must not
    become a digest of an empty string, which would look like a real measurement.
    """
    digest = bytearray()
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(65536), b""):
                digest.extend(block)
    except OSError:
        return None
    return sha256_bytes(bytes(digest))


def _record_count(path: Path) -> int | None:
    """Number of training records, or ``None`` when it cannot be established."""
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return len(decoded) if isinstance(decoded, list) else None


def build_training_provenance(
    *,
    data_path: str,
    output_dir: str,
    base_model: str | None = None,
    quantization: str | None = None,
    lora: dict[str, Any] | None = None,
    hyperparameters: dict[str, Any] | None = None,
    dataset_size: int | None = None,
) -> dict[str, Any]:
    """Assemble the provenance block for one training output directory.

    ``data_path`` is resolved through ``DATA_MANIFEST.json`` exactly like every
    other artifact that quotes dataset text. ``--data_path`` accepts any JSON
    file, so an unrecognised path resolves to ``UNCLASSIFIED`` and is described
    as unconfirmed: this module never defaults unknown input to synthetic, and
    never defaults anything to compliance-capable.
    """
    resolved = Path(data_path)
    provenance = dataset_data_provenance(data_path)
    data_class = str(provenance["data_class"])
    unclassified = data_class == UNCLASSIFIED_DATA_CLASS
    notice = provenance["notice"] or UNCLASSIFIED_DATA_NOTICE

    return {
        "schema_version": TRAINING_PROVENANCE_SCHEMA_VERSION,
        "generated_by": "offline/finetune_qlora.py",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "output_dir": output_dir,
        # 1 — what the data is.
        "training_input": {
            "path": data_path,
            "sha256": _file_sha256(resolved),
            "records": _record_count(resolved) if dataset_size is None else dataset_size,
            "data_class": data_class,
            "carries_real_regulatory_claims": provenance["carries_real_regulatory_claims"],
            "resolution": (
                "unregistered in DATA_MANIFEST.json; provenance unconfirmed"
                if unclassified
                else "resolved from DATA_MANIFEST.json"
            ),
            "notice": notice,
        },
        # 2 — what the run did. Self-reported by this process.
        "training_run": {
            "base_model": base_model,
            "quantization": quantization,
            "lora": dict(lora or {}),
            "hyperparameters": dict(hyperparameters or {}),
            "evidence": (
                "Recorded by the training script at save time from its own arguments. "
                "Not independently reproduced or verified by any other process."
            ),
        },
        # 3 — what has been demonstrated about the resulting weights.
        "capability_evidence": {
            "regulatory_correctness_evaluated": False,
            "benchmark": None,
            "reviewed_by": None,
            "statement": _NEVER_CLAIMED,
        },
        "usage_restrictions": [
            UNCLASSIFIED_TRAINING_USAGE_RESTRICTIONS if unclassified else SYNTHETIC_TRAINING_USAGE_RESTRICTIONS,
            ("训练执行成功不代表法规能力已获证明 / A successful training run is not proof of regulatory capability."),
        ],
    }


def write_training_provenance(output_dir: str, provenance: dict[str, Any]) -> Path:
    """Write the sidecar beside the adapter. Returns the path written."""
    target = Path(output_dir) / TRAINING_PROVENANCE_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def render_markdown_section(provenance: dict[str, Any]) -> str:
    """Render the sidecar as the model card section.

    The Markdown is what a human actually reads when they are handed the adapter
    directory, so the notice leads the section rather than closing it as a
    footnote.
    """
    training_input = provenance.get("training_input") or {}
    run = provenance.get("training_run") or {}
    capability = provenance.get("capability_evidence") or {}

    lines = [
        "## Data provenance and limits of use",
        "",
        f"> ⚠️ {training_input.get('notice') or ''}".rstrip(),
        "",
        f"- **training data class**: `{training_input.get('data_class')}`",
        f"- **carries real regulatory claims**: {training_input.get('carries_real_regulatory_claims')}",
        f"- **training data sha256**: `{training_input.get('sha256') or 'unavailable'}`",
        f"- **resolution**: {training_input.get('resolution')}",
        f"- **base model**: {run.get('base_model') or 'unavailable'}",
        "",
        "### What this adapter has and has not demonstrated",
        "",
        f"- regulatory correctness evaluated: **{str(capability.get('regulatory_correctness_evaluated')).lower()}**",
        f"- benchmark: {capability.get('benchmark') or 'none'}",
        f"- reviewed by: {capability.get('reviewed_by') or 'nobody'}",
        "",
        str(capability.get("statement") or ""),
        "",
        "### Usage restrictions",
        "",
    ]
    lines += [f"- {restriction}" for restriction in provenance.get("usage_restrictions") or []]
    lines += ["", f"Machine-readable form: `{TRAINING_PROVENANCE_FILENAME}`.", ""]
    return "\n".join(lines)
