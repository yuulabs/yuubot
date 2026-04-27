from __future__ import annotations

from pathlib import Path

from yuubot.config import Config
from yuubot.core import env
from yuubot.daemon.runtime import YuubotRuntimeFactory


def test_runtime_workspace_root_uses_standard_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(env.WORKSPACE_ROOT, str(tmp_path / "workspace"))

    root = YuubotRuntimeFactory(Config())._workspace_root(42)

    assert root == tmp_path / "workspace" / "ctx-42"


def test_runtime_workspace_root_prefers_config_over_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(env.WORKSPACE_ROOT, str(tmp_path / "env-workspace"))
    cfg = Config(yuuagents={"workspace_root": str(tmp_path / "config-workspace")})

    root = YuubotRuntimeFactory(cfg)._workspace_root(7)

    assert root == tmp_path / "config-workspace" / "ctx-7"


def test_runtime_workspace_root_is_stable_across_factory_recreation(tmp_path: Path) -> None:
    cfg = Config(yuuagents={"workspace_root": str(tmp_path / "workspace")})
    first = YuubotRuntimeFactory(cfg)._workspace_root(99)
    first.mkdir(parents=True)
    marker = first / "BOOTSTRAP.md"
    marker.write_text("persisted", encoding="utf-8")

    second = YuubotRuntimeFactory(cfg)._workspace_root(99)

    assert second == first
    assert (second / "BOOTSTRAP.md").read_text(encoding="utf-8") == "persisted"
