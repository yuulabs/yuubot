"""Disk cleanup, log retention, span pruning, and disk space alerts."""

from __future__ import annotations

import fnmatch
import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from attrs import define, field

from ..db import Database
from ..util.asyncio_ import BackgroundSweeper
from .host_stats import HostStats, collect_host_stats
from .logging_config import rotated_log_paths
from .resource_config import ResourceConfig

_log = logging.getLogger(__name__)

DiskAlertLevel = Literal["ok", "warning", "critical"]
EmitFn = Callable[..., None]


def resolve_tmp_dir(data_dir: Path, config: ResourceConfig) -> Path:
    if config.tmp_dir:
        return Path(config.tmp_dir).expanduser().resolve()
    return data_dir / "tmp"


def prune_old_files(root: Path, *, max_age_s: float, now: float | None = None) -> int:
    if not root.is_dir():
        return 0
    cutoff = (now if now is not None else time.time()) - max_age_s
    removed = 0
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        try:
            if not path.exists():
                continue
            if path.stat().st_mtime > cutoff:
                continue
            if path.is_dir():
                if any(path.iterdir()):
                    continue
                path.rmdir()
            else:
                path.unlink()
            removed += 1
        except OSError:
            _log.exception("failed to remove stale tmp path %s", path)
    return removed


def prune_system_tmp(
    *,
    globs: tuple[str, ...],
    max_age_s: float,
    now: float | None = None,
    tmp_root: Path | None = None,
) -> int:
    root = tmp_root or Path("/tmp")
    if not root.is_dir():
        return 0
    cutoff = (now if now is not None else time.time()) - max_age_s
    uid = os.getuid()
    removed = 0
    for entry in root.iterdir():
        try:
            if not entry.is_file():
                continue
            if entry.stat().st_uid != uid:
                continue
            if entry.stat().st_mtime > cutoff:
                continue
            if not any(fnmatch.fnmatch(entry.name, pattern) for pattern in globs):
                continue
            entry.unlink()
            removed += 1
        except OSError:
            _log.exception("failed to remove stale system tmp file %s", entry)
    return removed


def prune_rotated_logs(logs_dir: Path, *, retention_days: int, now: datetime | None = None) -> int:
    if retention_days <= 0:
        return 0
    current = now or datetime.now(UTC)
    cutoff = current - timedelta(days=retention_days)
    removed = 0
    for path in rotated_log_paths(logs_dir):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if mtime >= cutoff:
                continue
            path.unlink()
            removed += 1
        except OSError:
            _log.exception("failed to remove stale log file %s", path)
    return removed


async def prune_app_spans(db: Database, *, retention_days: int) -> int:
    if retention_days <= 0:
        return 0
    cursor = await db.execute("select name from sqlite_master where type = 'table' and name = 'app_spans'")
    row = await cursor.fetchone()
    if row is None:
        return 0
    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
    result = await db.execute("delete from app_spans where started_at < ?", (cutoff,))
    return result.rowcount if result.rowcount is not None else 0


def disk_alert_level(*, used_pct: float, warn_used_pct: float, critical_used_pct: float) -> DiskAlertLevel:
    if used_pct >= critical_used_pct:
        return "critical"
    if used_pct >= warn_used_pct:
        return "warning"
    return "ok"


@define
class ResourceSupervisor:
    data_dir: Path
    logs_dir: Path
    db: Database
    config: ResourceConfig
    emit: EmitFn
    _sweeper: BackgroundSweeper = field(factory=BackgroundSweeper, init=False)
    _host_stats: HostStats | None = field(default=None, init=False)
    _disk_alert_level: DiskAlertLevel = field(default="ok", init=False)

    @property
    def tmp_dir(self) -> Path:
        return resolve_tmp_dir(self.data_dir, self.config)

    @property
    def host_stats(self) -> HostStats | None:
        return self._host_stats

    async def start(self) -> None:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        await self.sweep()
        interval = min(
            self.config.tmp_cleanup_interval_s,
            self.config.disk_alert.interval_s,
        )
        await self._sweeper.start(interval, self.sweep)

    async def stop(self) -> None:
        await self._sweeper.stop()

    async def sweep(self) -> None:
        self._refresh_host_stats()
        self._check_disk_alert()
        prune_old_files(self.tmp_dir, max_age_s=self.config.tmp_max_age_s)
        prune_system_tmp(globs=self.config.tmp_globs, max_age_s=self.config.tmp_max_age_s)
        prune_rotated_logs(self.logs_dir, retention_days=self.config.logs.retention_days)
        await prune_app_spans(self.db, retention_days=self.config.spans_retention_days)

    def _refresh_host_stats(self) -> None:
        self._host_stats = collect_host_stats(disk_path=self.data_dir)

    def _check_disk_alert(self) -> None:
        stats = self._host_stats
        if stats is None:
            return
        next_level = disk_alert_level(
            used_pct=stats.disk_percent,
            warn_used_pct=self.config.disk_alert.warn_used_pct,
            critical_used_pct=self.config.disk_alert.critical_used_pct,
        )
        if next_level == self._disk_alert_level:
            return
        previous = self._disk_alert_level
        self._disk_alert_level = next_level
        if next_level == "critical":
            self.emit(
                "resource.disk_critical",
                disk_path=stats.disk_path,
                disk_percent=stats.disk_percent,
                disk_free_bytes=stats.disk_free_bytes,
            )
            return
        if next_level == "warning":
            self.emit(
                "resource.disk_warning",
                disk_path=stats.disk_path,
                disk_percent=stats.disk_percent,
                disk_free_bytes=stats.disk_free_bytes,
            )
            return
        if previous in {"warning", "critical"}:
            self.emit(
                "resource.disk_ok",
                disk_path=stats.disk_path,
                disk_percent=stats.disk_percent,
                disk_free_bytes=stats.disk_free_bytes,
            )
