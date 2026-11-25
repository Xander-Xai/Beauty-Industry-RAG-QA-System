"""Declare a paired-answer arm manifest from the real artifacts it describes.

A paired answer comparison is only as trustworthy as the declarations that
bind its two arms together. Writing those declarations by hand means typing
twelve digests that must match the artifacts exactly; a typo turns a valid
experiment into an opaque "invalid input" failure, and a *corrected* typo
turns it into a manifest that no longer describes what was run.

This module builds the manifest from the bytes that actually exist: the frozen
dataset, the observation file, the repository state and the deployed
configuration. Digests that only the operator can know — corpus, model assets,
retrieval contract, permissions, workload, runtime configuration, review
protocol and the Evidence Gate policy under test — are declared explicitly and
validated, never guessed.

The result is a manifest that still declares rather than proves: it binds the
comparison to declared inputs, it does not authenticate them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from tests.evaluation.answer_eval import AnswerEvalError

_HEX = re.compile(r"[a-f0-9]{64}")
_GIT = re.compile(r"[a-f0-9]{40}")
_EPOCH = re.compile(r"[A-Za-z0-9_-]{1,80}")
_ARMS = ("baseline", "candidate")

#: Digests the operator must declare: the tool cannot derive them from a file.
_DECLARED_DIGESTS = (
    "corpus_sha256",
    "model_assets_sha256",
    "retrieval_contract_sha256",
    "permissions_sha256",
    "workload_sha256",
    "runtime_config_sha256",
    "review_protocol_sha256",
    "evidence_gate_policy_sha256",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _repository_head() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    head = completed.stdout.strip()
    return head if _GIT.fullmatch(head) else ""


def _configured_epoch() -> str:
    try:
        from common.config import get_config_dict

        epoch = get_config_dict().get("knowledge_version_epoch", "")
    except Exception:  # noqa: BLE001 - a missing configuration is reported, not guessed
        return ""
    return epoch if isinstance(epoch, str) else ""


def build_manifest(
    *,
    arm: str,
    run_id: str,
    dataset_path: Path,
    observations_path: Path,
    declared: dict[str, str],
    git_sha: str = "",
    knowledge_epoch: str = "",
) -> dict[str, Any]:
    """Build one arm manifest from the artifacts it describes."""
    if arm not in _ARMS:
        raise AnswerEvalError(f"arm must be one of {_ARMS}")
    if not isinstance(run_id, str) or not run_id.strip():
        raise AnswerEvalError("Missing run identity")

    missing = [key for key in _DECLARED_DIGESTS if not _HEX.fullmatch(declared.get(key, ""))]
    if missing:
        raise AnswerEvalError(f"Missing or malformed declared digests: {', '.join(sorted(missing))}")

    epoch = knowledge_epoch or _configured_epoch()
    if not _EPOCH.fullmatch(epoch) or epoch == "default":
        raise AnswerEvalError("An explicit non-default knowledge epoch is required")

    head = git_sha or _repository_head()
    if not _GIT.fullmatch(head):
        raise AnswerEvalError("A 40 character git SHA is required")

    for path in (dataset_path, observations_path):
        if not path.is_file():
            raise AnswerEvalError(f"Missing artifact: {path}")

    manifest = {
        "arm": arm,
        "run_id": run_id.strip(),
        "environment": "ISOLATED_EVALUATION",
        "experiment_variable": "evidence_gate_policy",
        "answer_gate_enabled": True,
        "rbac_enabled": True,
        "git_sha": head,
        "knowledge_epoch": epoch,
        "dataset_sha256": _sha256_file(dataset_path),
        "observations_sha256": _sha256_file(observations_path),
        **{key: declared[key] for key in _DECLARED_DIGESTS},
    }
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", required=True, choices=_ARMS)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--git-sha", default="")
    parser.add_argument("--knowledge-epoch", default="")
    for key in _DECLARED_DIGESTS:
        parser.add_argument("--" + key.replace("_", "-"), required=True, dest=key)
    args = parser.parse_args(argv)

    try:
        manifest = build_manifest(
            arm=args.arm,
            run_id=args.run_id,
            dataset_path=args.dataset,
            observations_path=args.observations,
            declared={key: getattr(args, key) for key in _DECLARED_DIGESTS},
            git_sha=args.git_sha,
            knowledge_epoch=args.knowledge_epoch,
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    except (OSError, ValueError, TypeError, AnswerEvalError):
        parser.error("could not build the paired answer arm manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
