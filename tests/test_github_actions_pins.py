"""GitHub Actions must use immutable references and retain release labels."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ACTION_LINE = re.compile(r"^\s*(?:-\s*)?uses\s*:\s*(\S+?)(?:\s+#\s*(\S+))?\s*$")
IMMUTABLE_REF = re.compile(r"^[^@]+@[0-9a-f]{40}$")
VERSION_COMMENT = re.compile(r"^v\d+(?:\.\d+){0,2}$")


def _action_references(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "uses":
                yield child
            else:
                yield from _action_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _action_references(child)


def test_action_reference_collection_uses_yaml_keys_not_text_shape():
    workflow = yaml.safe_load("jobs:\n  build:\n    steps:\n      - uses : owner/action@v4 # v4.0.0\n")

    assert list(_action_references(workflow)) == ["owner/action@v4"]


def test_every_workflow_action_is_pinned_to_a_full_sha_with_version_comment():
    workflow_dir = REPOSITORY_ROOT / ".github" / "workflows"
    workflow_paths = sorted({*workflow_dir.rglob("*.yml"), *workflow_dir.rglob("*.yaml")})
    references: list[tuple[str, int, str, str, str | None]] = []

    for workflow_path in workflow_paths:
        text = workflow_path.read_text(encoding="utf-8")
        document = yaml.safe_load(text) or {}
        parsed_refs = list(_action_references(document))
        line_refs = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            match = ACTION_LINE.match(line)
            if match:
                line_refs.append((line_number, match.group(1), match.group(2)))
        assert len(parsed_refs) == len(line_refs), f"{workflow_path} action keys and source lines disagree"
        references.extend(
            (str(workflow_path.relative_to(REPOSITORY_ROOT)), line_number, parsed_ref, line_ref, version)
            for parsed_ref, (line_number, line_ref, version) in zip(parsed_refs, line_refs, strict=True)
        )

    assert references, "expected to inspect at least one third-party workflow action"
    for path, line_number, parsed_ref, reference, version in references:
        assert parsed_ref == reference, f"{path}:{line_number} action value does not match its source line"
        assert isinstance(reference, str) and IMMUTABLE_REF.fullmatch(reference), (
            f"{path}:{line_number} action ref is not pinned to a full commit SHA"
        )
        assert version and VERSION_COMMENT.fullmatch(version), (
            f"{path}:{line_number} action ref lacks its upstream version comment"
        )


def test_dependabot_schedules_github_actions_updates():
    dependabot = (REPOSITORY_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")

    assert "package-ecosystem: github-actions" in dependabot
    assert 'directory: "/"' in dependabot
    assert "interval: weekly" in dependabot
