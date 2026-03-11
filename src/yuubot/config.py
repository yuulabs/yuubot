"""YAML config loading with env-var substitution and path expansion."""

import os
import re
from copy import deepcopy
from pathlib import Path

import msgspec
import yaml
from dotenv import load_dotenv

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


class DatabaseConfig(msgspec.Struct):
    path: str = "~/.yuubot/yuubot.db"
    simple_ext: str = ""  # path to libsimple (without .so), auto-detected if empty


class LLMConfig(msgspec.Struct):
    provider: str = "openai"
    model: str = "gpt-4o"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""


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


class ResponseConfig(msgspec.Struct):
    group_default: str = "at"
    dm_whitelist: list[int] = msgspec.field(default_factory=list)


class SessionConfig(msgspec.Struct):
    ttl: int = 300  # seconds before session expires
    max_tokens: int = 60000  # context window token limit
    summarizer_provider: str = ""  # provider for summarizer/compressor LLM (required)
    summarizer_model: str = ""  # model for summarizer/compressor LLM (required)
    summarize_steps_span: int = 8  # steps to look back when generating compression summary


class Config(msgspec.Struct):
    bot: BotConfig = msgspec.field(default_factory=BotConfig)
    recorder: RecorderConfig = msgspec.field(default_factory=RecorderConfig)
    daemon: DaemonConfig = msgspec.field(default_factory=DaemonConfig)
    database: DatabaseConfig = msgspec.field(default_factory=DatabaseConfig)
    log_dir: str = "~/.yuubot/logs"
    yuuagents: dict[str, object] = msgspec.field(default_factory=dict)
    llm: LLMConfig = msgspec.field(default_factory=LLMConfig)
    api_keys: dict[str, str] = msgspec.field(default_factory=dict)
    cron_jobs: list[CronJob] = msgspec.field(default_factory=list)
    memory: MemoryConfig = msgspec.field(default_factory=MemoryConfig)
    web: WebConfig = msgspec.field(default_factory=WebConfig)
    response: ResponseConfig = msgspec.field(default_factory=ResponseConfig)
    schedule: ScheduleConfig = msgspec.field(default_factory=ScheduleConfig)
    session: SessionConfig = msgspec.field(default_factory=SessionConfig)

    def agent_min_role(self, agent_name: str) -> "Role":
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
        skills = self.yuuagents.get("skills", {})
        paths = skills.get("paths", ["~/.yagents/skills"])
        return [str(Path(p).expanduser()) for p in paths]


# ── Loading Logic ────────────────────────────────────────────────


def _resolve_env(value: str) -> str:
    """Replace ${VAR} with os.environ[VAR]."""

    def _sub(m: re.Match) -> str:
        name = m.group(1)
        val = os.environ.get(name, "")
        return val

    return _ENV_RE.sub(_sub, value)


def _walk_resolve(obj):
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


def _walk_expand_paths(obj, path_keys: set[str] | None = None):
    """Expand ~ in known path fields."""
    _path_keys = path_keys or {"path", "browser_profile", "download_dir", "media_dir", "log_dir"}
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


def load_config(path: str | None = None) -> Config:
    """Load, resolve env vars, expand paths, and return Config."""
    config_path = _find_config(path)
    os.environ[_ENV_CONFIG_KEY] = str(config_path.resolve())
    load_dotenv(dotenv_path=config_path.with_name(".env"), override=False)
    load_dotenv(override=False)
    raw = yaml.safe_load(config_path.read_text()) or {}
    yuuagents_path = config_path.with_name("yuuagents.config.yaml")
    if yuuagents_path.exists():
        yuuagents_raw = yaml.safe_load(yuuagents_path.read_text()) or {}
        if not isinstance(yuuagents_raw, dict):
            raise TypeError(f"{yuuagents_path} must be a mapping at the top level")
        raw_yuuagents = raw.get("yuuagents") or {}
        if raw_yuuagents and not isinstance(raw_yuuagents, dict):
            raise TypeError("config.yaml key 'yuuagents' must be a mapping if present")
        raw["yuuagents"] = _deep_merge(raw_yuuagents, yuuagents_raw)
    raw = _walk_resolve(raw)
    raw = _walk_expand_paths(raw)
    cfg = msgspec.convert(raw, Config)
    cfg.validate_agent_permissions()
    cfg.validate_subagent_tools()
    return cfg
