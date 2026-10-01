"""Full rebuild workflow.

A full rebuild reprocesses every source into a new epoch. It never activates
the epoch automatically: the operator validates, seals, then switches the
``knowledge_version_epoch`` configuration.
"""

from __future__ import annotations

from offline.snapshot_builder import BuildResult, IngestionSource


def run_full_rebuild(
    builder,
    sources: list[IngestionSource],
    epoch: str,
    *,
    seal: bool = False,
) -> BuildResult:
    """Process all sources into ``epoch`` and optionally seal after validation."""
    return builder.build_full(sources, epoch, seal=seal, validate=True)
