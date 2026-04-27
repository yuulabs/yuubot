from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from yuubot.cli import (
    _daemon_api_alive,
    _daemon_api_url_for_host,
    _docker_deploy_dir,
    _docker_source_root,
    _docker_update_deployment,
    _screen_quit,
    _screen_session_ids,
)
from yuubot.config import Config, DaemonApiConfig, DaemonConfig, DockerConfig


def test_screen_session_ids_match_exact_screen_name(monkeypatch) -> None:
    stdout = """
There are screens on:
    1452.yuubot\t(Detached)
    2201.yuubot-helper\t(Detached)
    3344.recorder\t(Detached)
    9988.yuubot\t(Detached)
4 Sockets in /run/screen/S-user.
"""

    monkeypatch.setattr(
        "yuubot.cli.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=stdout),
    )

    assert _screen_session_ids("yuubot") == ["1452.yuubot", "9988.yuubot"]


def test_screen_quit_terminates_each_matching_session(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    stdout = """
There are screens on:
    1452.yuubot\t(Detached)
    9988.yuubot\t(Detached)
2 Sockets in /run/screen/S-user.
"""

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["screen", "-ls"]:
            return SimpleNamespace(stdout=stdout)
        calls.append(tuple(cmd))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr("yuubot.cli.subprocess.run", fake_run)

    _screen_quit("yuubot")

    assert calls == [
        ("screen", "-S", "1452.yuubot", "-X", "quit"),
        ("screen", "-S", "9988.yuubot", "-X", "quit"),
    ]


def test_daemon_api_alive_rejects_non_2xx_status(monkeypatch) -> None:
    monkeypatch.setattr(
        "yuubot.cli.httpx.get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=503),
    )

    assert _daemon_api_alive("http://127.0.0.1:8780") is False


def test_daemon_api_url_for_host_normalizes_bind_all_host() -> None:
    cfg = Config(daemon=DaemonConfig(api=DaemonApiConfig(host="0.0.0.0", port=18780)))

    assert _daemon_api_url_for_host(cfg) == "http://127.0.0.1:18780"


def test_docker_paths_come_from_config(tmp_path) -> None:
    deploy_dir = tmp_path / "deploy"
    source_root = tmp_path / "source"
    cfg = Config(
        docker=DockerConfig(
            deploy_dir=str(deploy_dir),
            source_root=str(source_root),
        )
    )

    assert _docker_deploy_dir(cfg) == deploy_dir.resolve()
    assert _docker_source_root(cfg) == source_root.resolve()


def test_docker_deploy_dir_allows_explicit_override(tmp_path) -> None:
    configured = tmp_path / "configured"
    override = tmp_path / "override"
    cfg = Config(docker=DockerConfig(deploy_dir=str(configured)))

    assert _docker_deploy_dir(cfg, override) == override.resolve()


def test_docker_update_rebuilds_and_recreates_only_yuubot(tmp_path, monkeypatch) -> None:
    calls: list[tuple[tuple[str, ...], str]] = []
    health_checks: list[tuple[str, int]] = []

    def fake_run(cmd, **kwargs):
        calls.append((tuple(cmd), str(kwargs.get("cwd"))))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr("yuubot.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        "yuubot.cli._wait_daemon_api",
        lambda api, timeout: health_checks.append((api, timeout)) or True,
    )
    cfg = Config(daemon=DaemonConfig(api=DaemonApiConfig(host="0.0.0.0", port=8780)))

    _docker_update_deployment(
        tmp_path,
        cfg,
        health_timeout=12,
        health_check=True,
    )

    compose = ("docker", "compose", "-f", str(tmp_path / "compose.yaml"))
    assert calls == [
        ((*compose, "build", "yuubot"), str(tmp_path)),
        (
            (
                *compose,
                "up",
                "-d",
                "--no-deps",
                "--force-recreate",
                "yuubot",
                "traces-ui",
            ),
            str(tmp_path),
        ),
    ]
    assert health_checks == [("http://127.0.0.1:8780", 12)]


def test_docker_update_can_skip_health_check(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(cmd, **kwargs):
        calls.append(tuple(cmd))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr("yuubot.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        "yuubot.cli._wait_daemon_api",
        lambda *_args, **_kwargs: pytest.fail("health check should be skipped"),
    )

    _docker_update_deployment(
        tmp_path,
        Config(),
        health_timeout=1,
        health_check=False,
    )

    assert calls[0][-2:] == ("build", "yuubot")
    assert calls[1][-3:] == ("--force-recreate", "yuubot", "traces-ui")


def test_daemon_api_alive_handles_request_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "yuubot.cli.httpx.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(httpx.RequestError("boom")),
    )

    assert _daemon_api_alive("http://127.0.0.1:8780") is False
