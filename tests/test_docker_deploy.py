from __future__ import annotations

from pathlib import Path

import yaml

from yuubot.config import BotConfig, Config
from yuubot.docker_deploy import (
    CONTAINER_CONFIG_PATH,
    CONTAINER_WORKSPACE_ROOT,
    write_deployment_bundle,
)


def test_docker_deployment_mounts_data_and_workspace_separately(tmp_path: Path) -> None:
    deploy_dir = tmp_path / "deploy"

    deployment = write_deployment_bundle(
        Config(bot=BotConfig(qq=123456), timezone="UTC"),
        deploy_dir=deploy_dir,
        repo_root=tmp_path / "repo",
    )

    assert (deploy_dir / "data").is_dir()
    assert (deploy_dir / "workspace").is_dir()
    assert not (deploy_dir / "data" / "workspace").exists()

    compose = yaml.safe_load(deployment.compose_path.read_text(encoding="utf-8"))
    assert compose["services"]["napcat"]["environment"]["TZ"] == "UTC"
    assert compose["services"]["yuubot"]["environment"]["TZ"] == "UTC"
    volumes = compose["services"]["yuubot"]["volumes"]
    assert f"./config/config.yaml:{CONTAINER_CONFIG_PATH}:ro" in volumes
    assert "./data:/data" in volumes
    assert f"./workspace:{CONTAINER_WORKSPACE_ROOT}" in volumes

    env_lines = deployment.env_path.read_text(encoding="utf-8").splitlines()
    assert "TZ=UTC" in env_lines

    container_config = yaml.safe_load(deployment.config_path.read_text(encoding="utf-8"))
    assert container_config["timezone"] == "UTC"
    assert container_config["database"]["path"] == "/data/yuubot/yuubot.db"
    assert container_config["yuuagents"]["workspace_root"] == CONTAINER_WORKSPACE_ROOT


def test_docker_traces_ui_default_avoids_admin_port(tmp_path: Path) -> None:
    deployment = write_deployment_bundle(
        Config(bot=BotConfig(qq=123456), timezone="UTC"),
        deploy_dir=tmp_path / "deploy",
        repo_root=tmp_path / "repo",
    )

    compose = yaml.safe_load(deployment.compose_path.read_text(encoding="utf-8"))
    assert "8781:8781" in compose["services"]["yuubot"]["ports"]
    assert compose["services"]["traces-ui"]["ports"] == ["8782:8080"]


def test_docker_deployment_inherits_host_proxy_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("http_proxy", "http://proxy.test:7890")
    monkeypatch.setenv("https_proxy", "http://proxy.test:7890")
    monkeypatch.setenv("NO_PROXY", "localhost,example.com")

    deployment = write_deployment_bundle(
        Config(bot=BotConfig(qq=123456), timezone="UTC"),
        deploy_dir=tmp_path / "deploy",
        repo_root=tmp_path / "repo",
    )

    env_lines = deployment.env_path.read_text(encoding="utf-8").splitlines()
    assert "HTTP_PROXY=http://proxy.test:7890" in env_lines
    assert "http_proxy=http://proxy.test:7890" in env_lines
    assert "HTTPS_PROXY=http://proxy.test:7890" in env_lines
    assert "https_proxy=http://proxy.test:7890" in env_lines
    assert (
        "NO_PROXY=localhost,example.com,127.0.0.1,::1,napcat,yuubot,traces-ui,"
        "host.docker.internal"
    ) in env_lines
    assert (
        "no_proxy=localhost,example.com,127.0.0.1,::1,napcat,yuubot,traces-ui,"
        "host.docker.internal"
    ) in env_lines

    compose = yaml.safe_load(deployment.compose_path.read_text(encoding="utf-8"))
    assert compose["services"]["napcat"]["extra_hosts"] == [
        "host.docker.internal:host-gateway"
    ]
    assert compose["services"]["yuubot"]["extra_hosts"] == [
        "host.docker.internal:host-gateway"
    ]
