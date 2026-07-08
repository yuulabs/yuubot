"""Configuration for disk and resource management."""

from __future__ import annotations

from typing import cast

import msgspec

DEFAULT_TMP_CLEANUP_INTERVAL_S = 3600.0
DEFAULT_TMP_MAX_AGE_S = 86400.0
DEFAULT_TMP_GLOBS = ("yuubot-*", "jupyter-*", "ipykernel-*")
DEFAULT_LOG_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 5
DEFAULT_LOG_RETENTION_DAYS = 14
DEFAULT_DISK_ALERT_INTERVAL_S = 60.0
DEFAULT_DISK_WARN_USED_PCT = 85.0
DEFAULT_DISK_CRITICAL_USED_PCT = 95.0
DEFAULT_SPANS_RETENTION_DAYS = 30


class LogsResourceConfig(msgspec.Struct, frozen=True):
    max_bytes: int = DEFAULT_LOG_MAX_BYTES
    backup_count: int = DEFAULT_LOG_BACKUP_COUNT
    retention_days: int = DEFAULT_LOG_RETENTION_DAYS


class DiskAlertConfig(msgspec.Struct, frozen=True):
    interval_s: float = DEFAULT_DISK_ALERT_INTERVAL_S
    warn_used_pct: float = DEFAULT_DISK_WARN_USED_PCT
    critical_used_pct: float = DEFAULT_DISK_CRITICAL_USED_PCT


class ResourceConfig(msgspec.Struct, frozen=True):
    tmp_dir: str = ""
    tmp_cleanup_interval_s: float = DEFAULT_TMP_CLEANUP_INTERVAL_S
    tmp_max_age_s: float = DEFAULT_TMP_MAX_AGE_S
    tmp_globs: tuple[str, ...] = DEFAULT_TMP_GLOBS
    logs: LogsResourceConfig = msgspec.field(default_factory=LogsResourceConfig)
    disk_alert: DiskAlertConfig = msgspec.field(default_factory=DiskAlertConfig)
    spans_retention_days: int = DEFAULT_SPANS_RETENTION_DAYS


def resource_config_from_raw(raw: object) -> ResourceConfig:
    if not isinstance(raw, dict):
        return ResourceConfig()
    data = cast(dict[str, object], raw)
    tmp_globs = data.get("tmp_globs")
    if isinstance(tmp_globs, list):
        data = dict(data)
        data["tmp_globs"] = tuple(str(item) for item in tmp_globs)
    return msgspec.convert(data, ResourceConfig)
