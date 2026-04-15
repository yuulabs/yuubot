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
    agent_timeout: float = 300.0


class DatabaseConfig(msgspec.Struct):
    path: str = "~/.yuubot/yuubot.db"
    simple_ext: str = ""  # path to libsimple (without .so), auto-detected if empty


class CronJob(msgspec.Struct):
    task: str = ""
    cron: str = ""
    ctx_id: int | None = None


class MemoryConfig(msgspec.Struct):
    forget_days: int = 90
    max_length: int = 500


class WebConfig(msgspec.Struct):
    browser_profile: str = "~/.yuubot/browser_profile"
    headless: bool = True
    download_dir: str = "~/.yuubot/downloads"


class ScheduleConfig(msgspec.Struct):
    max_long_cycle: int = 5
    tick_seconds: float = 1.0  # clock-drift check interval for scheduler wakeups
    late_grace_seconds: float = 2.0
    catchup_spacing_seconds: float = 10.0
    resume_threshold_seconds: float = 30.0


class ResponseConfig(msgspec.Struct):
    group_default: str = "at"
    dm_whitelist: list[int] = msgspec.field(default_factory=list)


class NetworkConfig(msgspec.Struct):
    qq_direct: bool = True


class SessionConfig(msgspec.Struct):
    ttl: int = 300  # seconds before session expires
    max_tokens: int = 60000  # context window token limit
    summarize_steps_span: int = (
        8  # steps to look back when generating compression summary
    )


class Config(msgspec.Struct):
    bot: BotConfig = msgspec.field(default_factory=BotConfig)
    recorder: RecorderConfig = msgspec.field(default_factory=RecorderConfig)
    daemon: DaemonConfig = msgspec.field(default_factory=DaemonConfig)
    database: DatabaseConfig = msgspec.field(default_factory=DatabaseConfig)
    log_dir: str = "~/.yuubot/logs"
    yuuagents: dict[str, Any] = msgspec.field(default_factory=dict)
    provider_priorities: dict[str, int] = msgspec.field(default_factory=dict)
    provider_affinity: dict[str, dict[str, int]] = msgspec.field(default_factory=dict)
    llm_roles: dict[str, str] = msgspec.field(default_factory=dict)
    agent_llm_refs: dict[str, str] = msgspec.field(default_factory=dict)
    families: dict[str, Any] = msgspec.field(default_factory=dict)
    selectors: list[str] = msgspec.field(default_factory=list)
    api_keys: dict[str, str] = msgspec.field(default_factory=dict)
    cron_jobs: list[CronJob] = msgspec.field(default_factory=list)
    memory: MemoryConfig = msgspec.field(default_factory=MemoryConfig)
    web: WebConfig = msgspec.field(default_factory=WebConfig)
    response: ResponseConfig = msgspec.field(default_factory=ResponseConfig)
    network: NetworkConfig = msgspec.field(default_factory=NetworkConfig)
    schedule: ScheduleConfig = msgspec.field(default_factory=ScheduleConfig)
    session: SessionConfig = msgspec.field(default_factory=SessionConfig)

    def agent_min_role(self, agent_name: str):
        """Return the minimum Role required to invoke the given agent."""
        from yuubot.core.models import Role

        _role_map = {
            "master": Role.MASTER,
            "mod": Role.MOD,
            "folk": Role.FOLK,
            "deny": Role.DENY,
        }

        from yuubot.characters import CHARACTER_REGISTRY

        char = CHARACTER_REGISTRY.get(agent_name)
        if char is not None:
            return _role_map.get(char.min_role.lower(), Role.FOLK)
        return Role.FOLK

    def validate_agent_permissions(self) -> None:
        """Ensure parent min_role >= every subagent's min_role."""
        from yuubot.characters import CHARACTER_REGISTRY

        for name, char in CHARACTER_REGISTRY.items():
            parent_role = self.agent_min_role(name)
            for sub_name in char.spec.subagents:
                sub_role = self.agent_min_role(sub_name)
                if parent_role < sub_role:
                    raise ValueError(
                        f"Privilege escalation: agent {name!r} (min_role={parent_role.name}) "
                        f"can delegate to {sub_name!r} (min_role={sub_role.name}). "
                        f"Parent min_role must be >= subagent min_role."
                    )

    def validate_subagent_tools(self) -> None:
        """Ensure agents with subagents have delegate tool configured."""
        from yuubot.characters import CHARACTER_REGISTRY

        for name, char in CHARACTER_REGISTRY.items():
            if char.spec.subagents and "delegate" not in char.spec.tools:
                raise ValueError(
                    f"Agent {name!r} has subagents {char.spec.subagents!r} but 'delegate' "
                    f"tool is not configured. Add 'delegate' to tools list."
                )

    @property
    def persona(self) -> str:
        from yuubot.characters import get_character

        char = get_character("main")
        return char.resolve_persona()

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

        from yuubot.characters import CHARACTER_REGISTRY

        char = CHARACTER_REGISTRY.get(agent_name)
        if char is not None:
            llm_ref = str(getattr(char, "llm_ref", "") or "").strip()
            if llm_ref:
                return llm_ref
            provider = str(getattr(char, "provider", "") or "").strip()
            model = str(getattr(char, "model", "") or "").strip()
            if provider and model:
                return f"{provider}/{model}"

        agents_obj = self.yuuagents.get("agents")
        agents = agents_obj if isinstance(agents_obj, dict) else {}
        agent_cfg = agents.get(agent_name, agents.get("main", {}))
        if isinstance(agent_cfg, dict):
            provider = str(agent_cfg.get("provider", "") or "").strip()
            model = str(agent_cfg.get("model", "") or "").strip()
            if provider and model:
                return f"{provider}/{model}"
        if agent_name != "main":
            return self.agent_llm_ref("main")
        raise ValueError(f"agent {agent_name!r} has no llm ref configured")

    def build_yuuagents_config(self) -> dict[str, Any]:
        return build_yuuagents_config(self)


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
    }
    if isinstance(obj, dict):
        return {
            k: (
                _expand_path(v)
                if k in _path_keys and isinstance(v, str)
                else _walk_expand_paths(v, _path_keys)
            )
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_walk_expand_paths(v, _path_keys) for v in obj]
    return obj


def _deep_merge(base: dict, override: dict) -> dict:
    result = deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
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


def _split_llm_ref(ref: str) -> tuple[str, str]:
    ref = ref.strip()
    if "/" not in ref:
        raise ValueError(f"invalid llm ref: {ref!r}")
    provider, model = ref.split("/", 1)
    provider = provider.strip()
    model = model.strip()
    if not provider or not model:
        raise ValueError(f"invalid llm ref: {ref!r}")
    return provider, model


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
    raw = _deep_merge(raw, yaml.safe_load(config_path.read_text()) or {})
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
    cfg.yuuagents = build_yuuagents_config(cfg)
    cfg.validate_agent_permissions()
    cfg.validate_subagent_tools()
    return cfg


def build_yuuagents_config(cfg: Config) -> dict[str, Any]:
    """Build the yuuagents-compatible config payload from yuubot config."""
    payload = deepcopy(cfg.yuuagents)
    _strip_provider_model_catalogs(payload)
    from yuubot.characters import CHARACTER_REGISTRY

    payload["agents"] = {
        name: {
            "description": char.description,
            "provider": provider,
            "model": model,
            "persona": char.resolve_persona(),
            "subagents": list(char.spec.subagents),
            "tools": list(char.spec.tools),
        }
        for name, char in CHARACTER_REGISTRY.items()
        for provider, model in [_split_llm_ref(cfg.agent_llm_ref(name))]
    }
    return payload


def write_yagents_config(cfg: Config, path: Path | None = None) -> Path:
    """Write the generated yuuagents config to the installed runtime location."""
    target = path or (Path.home() / ".yagents" / "config.yaml")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(
            cfg.build_yuuagents_config(), allow_unicode=True, sort_keys=False
        ),
        encoding="utf-8",
    )
    return target
