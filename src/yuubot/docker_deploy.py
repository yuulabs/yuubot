"""Docker deployment bundle generation for full yuubot instances."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import msgspec
import yaml

from yuubot.config import Config, deep_merge
from yuubot.napcat import onebot_config_payload

DEFAULT_DEPLOY_DIR = Path("~/.local/share/yuubot-docker").expanduser()
CONTAINER_CONFIG_PATH = "/config/config.yaml"
CONTAINER_WORKSPACE_ROOT = "/workspace"
DEFAULT_TIMEZONE = "Asia/Shanghai"


@dataclass(frozen=True)
class DockerDeployment:
    deploy_dir: Path
    compose_path: Path
    config_path: Path
    env_path: Path
    import_path: Path | None = None


def _copy_napcat_state(deploy_dir: Path) -> None:
    source = Path("~/.config/QQ").expanduser()
    target = deploy_dir / "napcat" / "qq"
    if not source.exists() or not source.is_dir():
        return
    if any(target.iterdir()):
        return
    shutil.copytree(source, target, dirs_exist_ok=True)


def _find_docker_config() -> Path | None:
    env_path = os.environ.get("YUUBOT_CONFIG")
    if env_path:
        candidate = Path(env_path).with_name("docker_config.yaml")
        if candidate.exists():
            return candidate
    candidate = Path("docker_config.yaml")
    return candidate if candidate.exists() else None


def _container_config(cfg: Config) -> dict[str, Any]:
    payload = msgspec.to_builtins(cfg)
    assert isinstance(payload, dict)

    docker_config = _find_docker_config()
    if docker_config is None:
        raise FileNotFoundError(
            "docker_config.yaml not found next to config.yaml or in the current directory"
        )
    overrides = yaml.safe_load(docker_config.read_text(encoding="utf-8")) or {}
    payload = deep_merge(payload, overrides)

    yuuagents = payload.setdefault("yuuagents", {})
    assert isinstance(yuuagents, dict)
    for key in ("db", "daemon", "docker", "skills"):
        yuuagents.pop(key, None)

    return payload


def _api_key_env_names(cfg: Config) -> list[str]:
    names: list[str] = []
    for value in cfg.api_keys.values():
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            names.append(value[2:-1])
    providers = cfg.yuuagents.get("providers") or {}
    if isinstance(providers, dict):
        for provider in providers.values():
            if isinstance(provider, dict):
                env_name = str(provider.get("api_key_env") or "").strip()
                if env_name:
                    names.append(env_name)
    tavily = cfg.yuuagents.get("tavily") or {}
    if isinstance(tavily, dict):
        env_name = str(tavily.get("api_key_env") or "").strip()
        if env_name:
            names.append(env_name)
    return sorted(set(names))


def _write_env_file(path: Path, cfg: Config) -> None:
    timezone = str(cfg.timezone or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    lines = [
        f"YUUBOT_CONFIG={CONTAINER_CONFIG_PATH}",
        "YUU_DEPLOYMENT_MODE=container",
        f"YUU_WORKSPACE_ROOT={CONTAINER_WORKSPACE_ROOT}",
        f"TZ={timezone}",
        f"NAPCAT_UID={os.getuid()}",
        f"NAPCAT_GID={os.getgid()}",
        f"VNC_PASSWD={os.environ.get('VNC_PASSWD', 'vncpasswd')}",
    ]
    for name in _api_key_env_names(cfg):
        lines.append(f"{name}={os.environ.get(name, '')}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _compose_payload(repo_root: Path, cfg: Config) -> dict[str, Any]:
    webui_port = int(cfg.recorder.napcat_webui_port)
    timezone = str(cfg.timezone or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    return {
        "name": "yuubot-docker",
        "services": {
            "napcat": {
                "image": "mlikiowa/napcat-framework-docker:latest",
                "restart": "unless-stopped",
                "env_file": [".env"],
                "environment": {
                    "TZ": timezone,
                },
                "volumes": [
                    "./napcat/config:/app/napcat/config",
                    "./napcat/qq:/app/.config/QQ",
                ],
                "ports": [
                    "3010:3000",
                    f"{webui_port}:{webui_port}",
                    "6081:6081",
                ],
            },
            "yuubot": {
                "build": {
                    "context": str(repo_root),
                    "dockerfile": "yuubot/Dockerfile",
                },
                "image": "yuubot:local",
                "restart": "unless-stopped",
                "env_file": [".env"],
                "environment": {
                    "YUUBOT_CONFIG": CONTAINER_CONFIG_PATH,
                    "YUU_DEPLOYMENT_MODE": "container",
                    "YUU_WORKSPACE_ROOT": CONTAINER_WORKSPACE_ROOT,
                    "TZ": timezone,
                },
                "volumes": [
                    f"./config/config.yaml:{CONTAINER_CONFIG_PATH}:ro",
                    "./data:/data",
                    f"./workspace:{CONTAINER_WORKSPACE_ROOT}",
                    "./import:/import:ro",
                ],
                "ports": [
                    "8780:8780",
                    f"{cfg.admin.port}:{cfg.admin.port}",
                ],
                "depends_on": ["napcat"],
            },
            "traces-ui": {
                "image": "yuubot:local",
                "command": [
                    "ytrace", "ui",
                    "--db", "/data/yuubot/traces.db",
                    "--host", "0.0.0.0",
                    "--port", "8080",
                ],
                "restart": "unless-stopped",
                "environment": {"TZ": timezone},
                "volumes": ["./data:/data:ro"],
                "ports": [f"{cfg.docker.traces_ui_port}:8080"],
                "depends_on": ["yuubot"],
            },
        },
    }


def _write_napcat_config(deploy_dir: Path, cfg: Config) -> None:
    napcat_config_dir = deploy_dir / "napcat" / "config"
    napcat_config_dir.mkdir(parents=True, exist_ok=True)
    onebot_path = napcat_config_dir / f"onebot11_{cfg.bot.qq}.json"
    onebot_path.write_text(
        json.dumps(
            onebot_config_payload(
                cfg.bot.qq,
                ws_port=cfg.recorder.napcat_ws.port,
                http_port=3000,
                ws_host="yuubot",
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    webui_path = napcat_config_dir / "webui.json"
    existing: dict[str, Any] = {}
    if webui_path.exists():
        existing = json.loads(webui_path.read_text(encoding="utf-8"))
    existing["host"] = "0.0.0.0"
    existing["port"] = int(cfg.recorder.napcat_webui_port)
    webui_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def write_deployment_bundle(
    cfg: Config,
    *,
    deploy_dir: Path = DEFAULT_DEPLOY_DIR,
    repo_root: Path,
    import_archive: Path | None = None,
    copy_napcat_state: bool = False,
) -> DockerDeployment:
    deploy_dir = deploy_dir.expanduser().resolve()
    repo_root = repo_root.resolve()
    for relative in (
        "config",
        "data",
        "workspace",
        "import",
        "napcat/config",
        "napcat/qq",
    ):
        (deploy_dir / relative).mkdir(parents=True, exist_ok=True)

    config_path = deploy_dir / "config" / "config.yaml"
    env_path = deploy_dir / ".env"
    compose_path = deploy_dir / "compose.yaml"
    config_path.write_text(
        yaml.safe_dump(_container_config(cfg), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    _write_env_file(env_path, cfg)
    compose_path.write_text(
        yaml.safe_dump(_compose_payload(repo_root, cfg), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    _write_napcat_config(deploy_dir, cfg)
    if copy_napcat_state:
        _copy_napcat_state(deploy_dir)

    copied_import: Path | None = None
    if import_archive is not None:
        copied_import = deploy_dir / "import" / import_archive.name
        shutil.copy2(import_archive.expanduser(), copied_import)

    return DockerDeployment(
        deploy_dir=deploy_dir,
        compose_path=compose_path,
        config_path=config_path,
        env_path=env_path,
        import_path=copied_import,
    )
