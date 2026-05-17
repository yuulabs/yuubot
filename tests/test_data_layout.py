"""Verify the daemon writes its on-disk artifacts under DataLayout subpaths."""

from __future__ import annotations

from pathlib import Path

import msgspec

from yuubot.bootstrap.config import BootstrapConfig, DatabaseConfig, PathsConfig
from yuubot.bootstrap.layout import DataLayout
from yuubot.runtime.daemon import build_daemon


async def test_build_daemon_materializes_canonical_layout(
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    config = msgspec.structs.replace(
        yuubot_config,
        database=DatabaseConfig(path=str(data_dir / "yuubot" / "yuubot.db")),
        paths=PathsConfig(data_dir=str(data_dir)),
    )

    daemon = await build_daemon(config)
    try:
        layout = DataLayout.from_path(data_dir)
        assert layout.yuubot_dir.is_dir()
        assert layout.logs_dir.is_dir()
        assert layout.runtime_facades_dir.is_dir()
        assert layout.plugins_dir.is_dir()
        assert layout.integrations_root.is_dir()
        assert layout.workspace_root.is_dir()
        assert layout.skills_dir.is_dir()
    finally:
        await daemon.resources.close()


def test_layout_resolves_subpaths(tmp_path: Path) -> None:
    layout = DataLayout.from_path(tmp_path / "yuu")
    assert layout.db_path == tmp_path / "yuu" / "yuubot" / "yuubot.db"
    assert layout.traces_db_path == tmp_path / "yuu" / "yuubot" / "traces.db"
    assert layout.integrations_root == tmp_path / "yuu" / "integrations"
    assert layout.integration_dir("echo") == tmp_path / "yuu" / "integrations" / "echo"
    assert layout.workspace_root == tmp_path / "yuu" / "workspace"
    assert layout.skills_dir == tmp_path / "yuu" / "skills"
    assert layout.plugins_dir == tmp_path / "yuu" / "yuubot" / "plugins"


def test_layout_ensure_creates_all_directories(tmp_path: Path) -> None:
    layout = DataLayout.from_path(tmp_path / "fresh")
    layout.ensure()
    for path in (
        layout.data_dir,
        layout.yuubot_dir,
        layout.logs_dir,
        layout.runtime_facades_dir,
        layout.plugins_dir,
        layout.integrations_root,
        layout.workspace_root,
        layout.skills_dir,
    ):
        assert path.is_dir(), path
