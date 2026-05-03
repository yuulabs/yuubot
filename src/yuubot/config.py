"""YAML config loading with env-var substitution and path expansion."""

from __future__ import annotations

import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import msgspec
import yaml
from dotenv import load_dotenv
from loguru import logger

_ENV_RE = re.compile(r"\$\{(\w+)\}")

# ── Config Structs ───────────────────────────────────────────────


class HostPort(msgspec.Struct):
    host: str = "127.0.0.1"
    port: int = 0


class BotConfig(msgspec.Struct):
    qq: int = 0
    master: int = 0
    entries: list[str] = msgspec.field(default_factory=lambda: ["/y", "/yuu"])


class RecorderConfig(msgspec.Struct):
    napcat_ws: HostPort = msgspec.field(
        default_factory=lambda: HostPort(host="0.0.0.0", port=8765)
    )
    relay_ws: HostPort = msgspec.field(
        default_factory=lambda: HostPort(host="127.0.0.1", port=8766)
    )
    api: HostPort = msgspec.field(
        default_factory=lambda: HostPort(host="127.0.0.1", port=8767)
    )
    napcat_http: str = "http://127.0.0.1:3000"
    napcat_webui_port: int = 6099
    media_dir: str = "~/.yuubot/media"


class DaemonApiConfig(msgspec.Struct):
    host: str = "127.0.0.1"
    port: int = 8780


class DaemonConfig(msgspec.Struct):
    recorder_ws: str = "ws://127.0.0.1:8766"
    recorder_api: str = "http://127.0.0.1:8767"
    api: DaemonApiConfig = msgspec.field(default_factory=DaemonApiConfig)
    self_url: str = "http://127.0.0.1:8780"


class DatabaseConfig(msgspec.Struct):
    path: str = "~/.yuubot/yuubot.db"
    simple_ext: str = ""  # path to libsimple (without .so), auto-detected if empty



class MemoryConfig(msgspec.Struct):
    forget_days: int = 90
    max_length: int = 500


class WebConfig(msgspec.Struct):
    browser_profile: str = "~/.yuubot/browser_profile"
    headless: bool = True
    download_dir: str = "~/.yuubot/downloads"



class ResponseConfig(msgspec.Struct):
    group_default: str = "at"
    dm_whitelist: list[int] = msgspec.field(default_factory=list)


class NetworkConfig(msgspec.Struct):
    qq_direct: bool = True


class SessionConfig(msgspec.Struct):
    summarize_steps_span: int = (
        8  # steps to look back when generating compression summary
    )


class DockerConfig(msgspec.Struct):
    deploy_dir: str = "~/.local/share/yuubot-docker"
    source_root: str = ""
    health_timeout: int = 60
    traces_ui_port: int = 8782


class AdminConfig(msgspec.Struct):
    host: str = "0.0.0.0"
    port: int = 8781
    enabled: bool = True
    persistent_paths: list[str] = msgspec.field(default_factory=list)
    persist_base: str = ""  # defaults to "data/yuubot/persist" at runtime
    secret: str = ""  # if non-empty, required as Bearer token or cookie


class RoutingDefaultsConfig(msgspec.Struct):
    group: str = "yuu"
    private: str = "shiori"
    thread: str = "yuu"
    session: str = "shiori"
    other: str = "yuu"


class RoutingConfig(msgspec.Struct):
    defaults: RoutingDefaultsConfig = msgspec.field(default_factory=RoutingDefaultsConfig)
    rules: list[dict] = msgspec.field(default_factory=list)


class Config(msgspec.Struct):
    bot: BotConfig = msgspec.field(default_factory=BotConfig)
    recorder: RecorderConfig = msgspec.field(default_factory=RecorderConfig)
    daemon: DaemonConfig = msgspec.field(default_factory=DaemonConfig)
    database: DatabaseConfig = msgspec.field(default_factory=DatabaseConfig)
    timezone: str = "Asia/Shanghai"
    log_dir: str = "~/.yuubot/logs"
    yuuagents: dict[str, Any] = msgspec.field(default_factory=dict)
    provider_priorities: dict[str, int] = msgspec.field(default_factory=dict)
    provider_affinity: dict[str, dict[str, int]] = msgspec.field(default_factory=dict)
    llm_roles: dict[str, str] = msgspec.field(default_factory=dict)
    agent_llm_refs: dict[str, str] = msgspec.field(default_factory=dict)
    capabilities: dict[str, Any] = msgspec.field(default_factory=dict)
    families: dict[str, Any] = msgspec.field(default_factory=dict)
    selectors: list[str] = msgspec.field(default_factory=list)
    api_keys: dict[str, str] = msgspec.field(default_factory=dict)
    memory: MemoryConfig = msgspec.field(default_factory=MemoryConfig)
    web: WebConfig = msgspec.field(default_factory=WebConfig)
    response: ResponseConfig = msgspec.field(default_factory=ResponseConfig)
    network: NetworkConfig = msgspec.field(default_factory=NetworkConfig)
    session: SessionConfig = msgspec.field(default_factory=SessionConfig)
    docker: DockerConfig = msgspec.field(default_factory=DockerConfig)
    admin: AdminConfig = msgspec.field(default_factory=AdminConfig)
    routing: RoutingConfig = msgspec.field(default_factory=RoutingConfig)

    @property
    def skill_paths(self) -> list[str]:
        skills_obj = self.yuuagents.get("skills")
        skills: dict[str, Any] = skills_obj if isinstance(skills_obj, dict) else {}
        paths_obj = skills.get("paths")
        paths = paths_obj if isinstance(paths_obj, list) else ["~/.yagents/skills"]
        return [str(Path(p).expanduser()) for p in paths]

    def agent_llm_ref(self, agent_name: str) -> str:
        ref = str(self.agent_llm_refs.get(agent_name, "") or "").strip()
        if ref:
            return ref

        agents_obj = self.yuuagents.get("agents")
        agents = agents_obj if isinstance(agents_obj, dict) else {}
        agent_cfg = agents.get(agent_name, agents.get("yuu", {}))
        if isinstance(agent_cfg, dict):
            provider = str(agent_cfg.get("provider", "") or "").strip()
            model = str(agent_cfg.get("model", "") or "").strip()
            if provider and model:
                return f"{provider}/{model}"
        if agent_name != "yuu":
            return self.agent_llm_ref("yuu")
        raise ValueError(f"agent {agent_name!r} has no llm ref configured")

# ── Loading Logic ────────────────────────────────────────────────


def _resolve_env(value: str) -> str:
    """Replace ${VAR} with os.environ[VAR]."""

    def _sub(m: re.Match) -> str:
        name = m.group(1)
        val = os.environ.get(name, "")
        return val

    return _ENV_RE.sub(_sub, value)


def _walk_resolve(obj: Any) -> Any:
    """Recursively resolve env vars in a nested dict/list."""
    if isinstance(obj, str):
        return _resolve_env(obj)
    if isinstance(obj, dict):
        return {k: _walk_resolve(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk_resolve(v) for v in obj]
    return obj


def _expand_path(p: str) -> str:
    return str(Path(p).expanduser())


def _walk_expand_paths(obj: Any, path_keys: set[str] | None = None) -> Any:
    """Expand ~ in known path fields."""
    _path_keys = path_keys or {
        "path",
        "browser_profile",
        "download_dir",
        "media_dir",
        "log_dir",
        "deploy_dir",
        "source_root",
        "db_path",
        "workspace_root",
    }
    if isinstance(obj, dict):
        return {
            k: (
                _expand_path(v)
                if k in _path_keys and isinstance(v, str) and v
                else _walk_expand_paths(v, _path_keys)
            )
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_walk_expand_paths(v, _path_keys) for v in obj]
    return obj


def deep_merge(base: dict, override: dict) -> dict:
    result = deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = deepcopy(v)
    return result


_ENV_CONFIG_KEY = "YUUBOT_CONFIG"


def _find_config(explicit: str | None = None) -> Path:
    """Search for config file."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    env_path = os.environ.get(_ENV_CONFIG_KEY)
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path("config.yaml"))
    candidates.append(Path.home() / ".yuubot" / "config.yaml")
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        f"Config not found. Searched: {[str(c) for c in candidates]}"
    )


def _find_llm_config(config_path: Path) -> Path | None:
    """Look for llm.yaml next to the main config file."""
    llm_path = config_path.with_name("llm.yaml")
    return llm_path if llm_path.exists() else None


def _derive_agent_llm_refs(raw_yuuagents: dict[str, Any]) -> dict[str, str]:
    agents_raw = raw_yuuagents.get("agents", {})
    if not isinstance(agents_raw, dict):
        return {}
    result: dict[str, str] = {}
    for name, payload in agents_raw.items():
        if not isinstance(payload, dict):
            continue
        provider = str(payload.get("provider", "") or "").strip()
        model = str(payload.get("model", "") or "").strip()
        if provider and model:
            result[str(name)] = f"{provider}/{model}"
    return result


def _strip_provider_model_catalogs(raw_yuuagents: dict[str, Any]) -> dict[str, int]:
    providers_raw = raw_yuuagents.get("providers", {})
    if not isinstance(providers_raw, dict):
        return {}
    removed: dict[str, int] = {}
    for provider_name, provider_cfg in providers_raw.items():
        if not isinstance(provider_cfg, dict):
            continue
        if "models" not in provider_cfg:
            continue
        models_raw = provider_cfg.pop("models")
        removed[str(provider_name)] = (
            len(models_raw) if isinstance(models_raw, dict) else 0
        )
    return removed


def load_config(path: str | None = None) -> Config:
    """Load, resolve env vars, expand paths, and return Config."""
    config_path = _find_config(path)
    os.environ[_ENV_CONFIG_KEY] = str(config_path.resolve())
    load_dotenv(dotenv_path=config_path.with_name(".env"), override=False)
    load_dotenv(override=False)
    # llm.yaml is the LLM-provider base; config.yaml overrides on top
    raw: dict[str, Any] = {}
    llm_path = _find_llm_config(config_path)
    if llm_path is not None:
        llm_raw = yaml.safe_load(llm_path.read_text(encoding="utf-8")) or {}
        if isinstance(llm_raw, dict):
            raw = llm_raw
    raw = deep_merge(raw, yaml.safe_load(config_path.read_text()) or {})
    raw_yuuagents = raw.get("yuuagents") or {}
    if raw_yuuagents and not isinstance(raw_yuuagents, dict):
        raise TypeError("config.yaml key 'yuuagents' must be a mapping if present")
    raw["yuuagents"] = raw_yuuagents
    removed_catalogs = _strip_provider_model_catalogs(raw_yuuagents)
    if removed_catalogs:
        logger.warning(
            "Ignoring deprecated yuuagents.providers.*.models config: {}",
            ", ".join(
                f"{provider}({count})"
                for provider, count in sorted(removed_catalogs.items())
            ),
        )
    if not raw.get("agent_llm_refs"):
        derived_refs = _derive_agent_llm_refs(raw["yuuagents"])
        if derived_refs:
            raw["agent_llm_refs"] = derived_refs
    raw = _walk_resolve(raw)
    raw = _walk_expand_paths(raw)
    cfg = msgspec.convert(raw, Config)
    return cfg
