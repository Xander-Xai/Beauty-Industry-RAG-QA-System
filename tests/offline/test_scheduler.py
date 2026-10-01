"""Scheduler config and cycle delegation tests."""

from __future__ import annotations

import re

from offline.scheduler import (
    OfflineScheduler,
    generate_epoch,
    load_scheduler_config,
    run_full_rebuild_cycle,
    run_incremental_cycle,
)
from offline.snapshot_builder import BuildResult


def test_load_scheduler_config_defaults_and_overrides():
    defaults = load_scheduler_config({})
    assert defaults.incremental_cron == "0 2 * * 0"
    assert defaults.auto_seal is False

    configured = load_scheduler_config({"offline": {"scheduler": {"incremental_cron": "0 5 * * 1", "auto_seal": True}}})
    assert configured.incremental_cron == "0 5 * * 1"
    assert configured.auto_seal is True


def test_generate_epoch_format():
    assert re.fullmatch(r"\d{8}_\d{6}", generate_epoch()) is not None


class _FakeBuilder:
    def __init__(self):
        self.calls = []

    def build_incremental(self, sources, from_epoch, to_epoch, *, seal, validate):
        self.calls.append(("incremental", from_epoch, to_epoch, seal, validate))
        return BuildResult(epoch=to_epoch, sealed=seal)

    def build_full(self, sources, epoch, *, seal, validate):
        self.calls.append(("full", epoch, seal, validate))
        return BuildResult(epoch=epoch, sealed=seal)


def test_cycle_functions_delegate_to_builder():
    builder = _FakeBuilder()
    incremental = run_incremental_cycle(builder, [], from_epoch="a", to_epoch="b", seal=True)
    assert incremental.sealed is True
    assert builder.calls[-1] == ("incremental", "a", "b", True, True)

    full = run_full_rebuild_cycle(builder, [], epoch="c", seal=False)
    assert full.epoch == "c"
    assert builder.calls[-1] == ("full", "c", False, True)


def test_offline_scheduler_bump_does_not_mutate_config():
    config = {"knowledge_version_epoch": "active_1", "offline": {"scheduler": {}}}
    scheduler = OfflineScheduler(builder=_FakeBuilder(), config=config)
    generated = scheduler.bump_version_epoch()
    assert generated != "active_1"
    assert config["knowledge_version_epoch"] == "active_1"


def test_offline_scheduler_resolves_permissions():
    config = {
        "offline": {"scheduler": {}},
        "permission_rules": {
            "rules": [{"path_pattern": "*/法规/*", "role_mask": 4, "dept_mask": 4}],
            "default_role_mask": 0,
            "default_dept_mask": 0,
        },
    }
    scheduler = OfflineScheduler(builder=_FakeBuilder(), config=config)
    assert scheduler.resolve_permission("docs/法规/guide.txt") == (4, 4)
    assert scheduler.resolve_permission("docs/public/guide.txt") == (0, 0)
