from __future__ import annotations

from pathlib import Path

import pytest

from yuubot.app import Yuubot
from yuubot.app.snapshots import runtime_snapshot
from yuubot.runtime.host_stats import collect_host_stats


def test_collect_host_stats_returns_expected_fields(tmp_path: Path) -> None:
    stats = collect_host_stats(tmp_path)

    assert stats.disk_path == str(tmp_path)
    assert stats.memory_total_bytes > 0
    assert stats.disk_total_bytes > 0
    assert stats.cpu_percent >= 0.0


@pytest.mark.asyncio
async def test_runtime_snapshot_includes_host(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    snapshot = runtime_snapshot(app)

    assert snapshot.host.memory_total_bytes > 0
    assert snapshot.host.disk_path == str(app.runtime.data_dir)
