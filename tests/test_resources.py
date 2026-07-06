from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from yuubot.app.snapshots import _runtime_event_view
from yuubot.db import Database
from yuubot.runtime.events import EventBus, RuntimeEvent
from yuubot.runtime.logging_config import LOG_FILENAME, rotated_log_paths
from yuubot.runtime.resource_config import DiskAlertConfig, ResourceConfig
from yuubot.runtime.resources import (
    ResourceSupervisor,
    disk_alert_level,
    prune_old_files,
    prune_rotated_logs,
    prune_system_tmp,
)


def test_disk_alert_level_thresholds() -> None:
    assert disk_alert_level(used_pct=70.0, warn_used_pct=85.0, critical_used_pct=95.0) == "ok"
    assert disk_alert_level(used_pct=90.0, warn_used_pct=85.0, critical_used_pct=95.0) == "warning"
    assert disk_alert_level(used_pct=96.0, warn_used_pct=85.0, critical_used_pct=95.0) == "critical"


def test_prune_old_files_removes_stale_entries(tmp_path: Path) -> None:
    root = tmp_path / "tmp"
    root.mkdir()
    stale = root / "old.txt"
    fresh = root / "new.txt"
    stale.write_text("old", encoding="utf-8")
    fresh.write_text("new", encoding="utf-8")
    now = time.time()
    os.utime(stale, (now - 100_000, now - 100_000))
    os.utime(fresh, (now, now))

    removed = prune_old_files(root, max_age_s=3600, now=now)

    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()


def test_prune_system_tmp_only_matches_owned_globs(tmp_path: Path) -> None:
    tmp_root = tmp_path / "system-tmp"
    tmp_root.mkdir()
    now = time.time()
    match = tmp_root / "jupyter-abc"
    other = tmp_root / "other.txt"
    match.write_text("x", encoding="utf-8")
    other.write_text("y", encoding="utf-8")
    os.utime(match, (now - 100_000, now - 100_000))
    os.utime(other, (now - 100_000, now - 100_000))

    removed = prune_system_tmp(globs=("jupyter-*",), max_age_s=3600, now=now, tmp_root=tmp_root)

    assert removed == 1
    assert not match.exists()
    assert other.exists()


def test_prune_rotated_logs_deletes_old_backups(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    old = logs_dir / f"{LOG_FILENAME}.1"
    old.write_text("old", encoding="utf-8")
    old_time = datetime.now(UTC) - timedelta(days=30)
    os.utime(old, (old_time.timestamp(), old_time.timestamp()))

    removed = prune_rotated_logs(logs_dir, retention_days=14, now=datetime.now(UTC))

    assert removed == 1
    assert old not in rotated_log_paths(logs_dir)


@pytest.mark.asyncio
async def test_resource_supervisor_emits_disk_warning_and_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = await Database.open(tmp_path / "db")
    eventbus = EventBus()
    config = ResourceConfig(
        tmp_cleanup_interval_s=3600,
        disk_alert=DiskAlertConfig(interval_s=60, warn_used_pct=85, critical_used_pct=95),
    )
    supervisor = ResourceSupervisor(
        data_dir=tmp_path,
        logs_dir=tmp_path / "logs",
        db=db,
        config=config,
        emit=eventbus.emit,
    )

    class Disk:
        percent = 90.0
        free = 1024
        used = 900
        total = 1000

    monkeypatch.setattr(
        "yuubot.runtime.resources.collect_host_stats",
        lambda *, disk_path: type("Stats", (), {
            "disk_percent": Disk.percent,
            "disk_free_bytes": Disk.free,
            "disk_path": str(disk_path),
        })(),
    )
    supervisor._refresh_host_stats()
    supervisor._check_disk_alert()
    assert eventbus.events[-1].kind == "resource.disk_warning"

    Disk.percent = 70.0
    supervisor._refresh_host_stats()
    supervisor._check_disk_alert()
    assert eventbus.events[-1].kind == "resource.disk_ok"
    await db.close()


def test_runtime_event_view_formats_disk_alerts() -> None:
    event = RuntimeEvent(
        ts="2026-07-06T00:00:00+00:00",
        kind="resource.disk_critical",
        payload={"disk_percent": 96.5, "disk_free_bytes": 1234},
    )
    view = _runtime_event_view(event)
    assert view is not None
    assert view.title == "Disk space critical"
    assert "96.5%" in view.detail
