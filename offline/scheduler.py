"""Framework-independent offline scheduler.

The same business logic is callable from cron, Airflow, or the CLI. The
scheduler builds, validates and (optionally) seals an epoch. It never switches
the active ``knowledge_version_epoch``: activation stays an explicit operator
action.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from offline.rebuild import run_full_rebuild
from offline.snapshot_builder import BuildResult, IngestionSource, configured_snapshot_builder
from offline.source_discovery import discover_sources, resolve_permission


@dataclass(frozen=True)
class SchedulerConfig:
    incremental_enabled: bool = True
    full_rebuild_enabled: bool = True
    incremental_cron: str = "0 2 * * 0"
    full_rebuild_cron: str = "0 3 1 * *"
    auto_seal: bool = False


def load_scheduler_config(config: dict) -> SchedulerConfig:
    section = (config or {}).get("offline", {}).get("scheduler", {})
    return SchedulerConfig(
        incremental_enabled=bool(section.get("incremental_enabled", True)),
        full_rebuild_enabled=bool(section.get("full_rebuild_enabled", True)),
        incremental_cron=section.get("incremental_cron", "0 2 * * 0"),
        full_rebuild_cron=section.get("full_rebuild_cron", "0 3 1 * *"),
        auto_seal=bool(section.get("auto_seal", False)),
    )


def generate_epoch(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    return moment.strftime("%Y%m%d_%H%M%S")


def run_incremental_cycle(
    builder,
    sources: list[IngestionSource],
    *,
    from_epoch: str,
    to_epoch: str,
    seal: bool = False,
) -> BuildResult:
    return builder.build_incremental(sources, from_epoch, to_epoch, seal=seal, validate=True)


def run_full_rebuild_cycle(
    builder,
    sources: list[IngestionSource],
    *,
    epoch: str,
    seal: bool = False,
) -> BuildResult:
    return run_full_rebuild(builder, sources, epoch, seal=seal)


class OfflineScheduler:
    """Discover sources and run build cycles with config-driven cadence."""

    def __init__(self, builder=None, config: dict | None = None):
        if config is None:
            from common.config import get_config_dict

            config = get_config_dict()
        self.config = config
        self._builder = builder
        self.scheduler_config = load_scheduler_config(config)

    @property
    def builder(self):
        if self._builder is None:
            self._builder = configured_snapshot_builder(self.config)
        return self._builder

    def resolve_permission(self, relative_path: str) -> tuple[int, int]:
        return resolve_permission(relative_path, self.config.get("permission_rules", {}))

    def discover_sources(self) -> list[IngestionSource]:
        knowledge_base = self.config.get("knowledge_base", {})
        return discover_sources(
            knowledge_base.get("data_dir", "./data"),
            permission_rules=self.config.get("permission_rules", {}),
        )

    def run_incremental_cycle(
        self, *, from_epoch: str | None = None, to_epoch: str | None = None, seal: bool | None = None
    ) -> BuildResult:
        from_epoch = from_epoch or self.config.get("knowledge_version_epoch", "default")
        to_epoch = to_epoch or generate_epoch()
        return run_incremental_cycle(
            self.builder,
            self.discover_sources(),
            from_epoch=from_epoch,
            to_epoch=to_epoch,
            seal=self.scheduler_config.auto_seal if seal is None else seal,
        )

    def run_full_rebuild_cycle(self, *, epoch: str | None = None, seal: bool | None = None) -> BuildResult:
        epoch = epoch or generate_epoch()
        return run_full_rebuild_cycle(
            self.builder,
            self.discover_sources(),
            epoch=epoch,
            seal=self.scheduler_config.auto_seal if seal is None else seal,
        )

    # Backward-compatible aliases for the DAG contract.
    def run_incremental_update(self, **kwargs) -> dict:
        return _as_dict(self.run_incremental_cycle(**kwargs))

    def run_full_rebuild(self, **kwargs) -> dict:
        return _as_dict(self.run_full_rebuild_cycle(**kwargs))

    def bump_version_epoch(self) -> str:
        """Return a fresh epoch name without mutating the active configuration."""
        return generate_epoch()


def _as_dict(result: BuildResult) -> dict:
    return {
        "epoch": result.epoch,
        "documents_processed": result.documents_processed,
        "chunks_written": result.chunks_written,
        "images_written": result.images_written,
        "carried_forward": result.carried_forward,
        "sealed": result.sealed,
        "validation_ok": result.validation.ok if result.validation else None,
    }
