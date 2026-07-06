"""Host resource metrics for runtime monitoring."""

from __future__ import annotations

from pathlib import Path

import msgspec
import psutil


class HostStats(msgspec.Struct, frozen=True, kw_only=True):
    cpu_percent: float
    memory_used_bytes: int
    memory_total_bytes: int
    memory_percent: float
    disk_used_bytes: int
    disk_total_bytes: int
    disk_free_bytes: int
    disk_percent: float
    disk_path: str
    net_bytes_sent: int
    net_bytes_recv: int


def collect_host_stats(*, disk_path: Path) -> HostStats:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(disk_path))
    net = psutil.net_io_counters()
    return HostStats(
        cpu_percent=psutil.cpu_percent(interval=None),
        memory_used_bytes=memory.used,
        memory_total_bytes=memory.total,
        memory_percent=memory.percent,
        disk_used_bytes=disk.used,
        disk_total_bytes=disk.total,
        disk_free_bytes=disk.free,
        disk_percent=disk.percent,
        disk_path=str(disk_path),
        net_bytes_sent=net.bytes_sent if net is not None else 0,
        net_bytes_recv=net.bytes_recv if net is not None else 0,
    )
