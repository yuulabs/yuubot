from __future__ import annotations

from pathlib import Path

from yuubot.config import Config
from yuubot.core import env
from yuubot.daemon.actor import _python_kernel_config, _workspace_root


def test_actor_workspace_root_uses_standard_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(env.WORKSPACE_ROOT, str(tmp_path / "workspace"))

    root = Path(_workspace_root(Config(), 42))

    assert root == tmp_path / "workspace" / "ctx-42"


def test_actor_workspace_root_prefers_config_over_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(env.WORKSPACE_ROOT, str(tmp_path / "env-workspace"))
    cfg = Config(yuuagents={"workspace_root": str(tmp_path / "config-workspace")})

    root = Path(_workspace_root(cfg, 7))

    assert root == tmp_path / "config-workspace" / "ctx-7"


def test_actor_workspace_root_is_stable_across_stage_recreation(tmp_path: Path) -> None:
    cfg = Config(yuuagents={"workspace_root": str(tmp_path / "workspace")})
    first = Path(_workspace_root(cfg, 99))
    first.mkdir(parents=True)
    marker = first / "BOOTSTRAP.md"
    marker.write_text("persisted", encoding="utf-8")

    second = Path(_workspace_root(cfg, 99))

    assert second == first
    assert (second / "BOOTSTRAP.md").read_text(encoding="utf-8") == "persisted"


def test_python_kernel_config_carries_configured_sys_path(tmp_path: Path) -> None:
    cfg = Config(
        yuuagents={
            "workspace_root": str(tmp_path / "workspace"),
            "python": {
                "sys_path": [str(tmp_path / "configured")],
            }
        }
    )

    config = _python_kernel_config(cfg, 5)

    assert str(tmp_path / "configured") in config.sys_path
    assert Path(config.cwd or "").name == "ctx-5"


def test_python_kernel_config_merges_configured_extra_env(tmp_path: Path) -> None:
    cfg = Config(
        yuuagents={
            "workspace_root": str(tmp_path / "workspace"),
            "python": {
                "extra_envs": {"YUUBOT_EXTRA": "yes"},
            }
        }
    )

    config = _python_kernel_config(cfg, 5)

    assert config.extra_envs["YUUBOT_EXTRA"] == "yes"
    assert config.extra_envs["YUUBOT_DB_PATH"] == cfg.database.path
