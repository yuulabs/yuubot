"""Local helpers for agent-facing functions that run inside Python workers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import msgspec.structs
from tortoise import Tortoise

from yuubot.agent_fns.context import current_session_state
from yuubot.config import (
    Config,
    DaemonApiConfig,
    DaemonConfig,
    MemoryConfig,
    RecorderConfig,
    WebConfig,
    load_config,
)
from yuubot.core.db import init_db

_DB_READY_FOR: str | None = None


async def ensure_db_ready() -> None:
    """Initialize the worker-local Tortoise connection for Yuubot's main DB."""
    global _DB_READY_FOR
    db_path = _database_path()
    if not db_path:
        raise RuntimeError("YUUBOT_DB_PATH is not available in this Python worker")
    if Tortoise.is_inited() and _DB_READY_FOR == db_path:
        return
    if Tortoise.is_inited() and _DB_READY_FOR is None:
        _DB_READY_FOR = db_path
        return
    if Tortoise.is_inited():
        raise RuntimeError(f"worker DB is already initialized for {_DB_READY_FOR}, not {db_path}")
    await init_db(db_path, simple_ext=_db_simple_ext())
    _DB_READY_FOR = db_path


def service_payload(**extra: Any) -> dict[str, Any]:
    """Build the local-service payload from SESSION_STATE plus explicit args."""
    state = current_session_state()
    payload: dict[str, Any] = {
        "bot_kind": state.bot_kind or "group",
        "ctx_id": state.ctx_id,
        "chat_type": state.chat_type,
        "group_id": state.group_id,
        "user_id": state.user_id,
        "conversation_id": state.conversation_id,
        "agent_name": state.agent_name,
        "character_name": state.character_name or state.agent_name,
        "task_id": state.task_id,
        "bot_id": state.bot_id,
        "workspace_root": state.workspace_root,
        "recorder_base_url": state.recorder_base_url,
        "napcat_http_base_url": state.napcat_http_base_url,
        "daemon_base_url": state.daemon_base_url,
        "daemon_self_url": state.daemon_self_url,
        "tavily_api_key": os.environ.get("TAVILY_API_KEY", ""),
    }
    payload.update({k: v for k, v in extra.items() if v is not None})
    return payload


def local_config() -> Config:
    """Construct the subset of Config local services need inside a worker."""
    state = current_session_state()
    daemon_url = state.daemon_base_url or os.environ.get("YUUBOT_DAEMON_URL", "")
    parsed = urlsplit(daemon_url)
    daemon_api = DaemonApiConfig(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 8780,
    )
    fallback = Config(
        daemon=DaemonConfig(
            api=daemon_api,
            self_url=state.daemon_self_url or os.environ.get("YUUBOT_DAEMON_SELF_URL", daemon_url),
            recorder_api=state.recorder_base_url or os.environ.get("YUUBOT_RECORDER_URL", ""),
        ),
        recorder=RecorderConfig(
            napcat_http=state.napcat_http_base_url or os.environ.get("YUUBOT_NAPCAT_HTTP_URL", ""),
        ),
        memory=MemoryConfig(max_length=_int_env("YUUBOT_MEMORY_MAX_LENGTH", 500)),
        web=WebConfig(download_dir=os.environ.get("YUUBOT_WEB_DOWNLOAD_DIR", "~/.yuubot/downloads")),
        api_keys={"tavily": os.environ.get("TAVILY_API_KEY", "")},
    )
    try:
        cfg = load_config(os.environ.get("YUUBOT_CONFIG") or None)
    except Exception:
        return fallback
    return msgspec.structs.replace(
        cfg,
        daemon=fallback.daemon,
        recorder=fallback.recorder,
        memory=fallback.memory,

        web=fallback.web,
        api_keys={**cfg.api_keys, **fallback.api_keys},
    )


def current_ctx_id(ctx_id: int | None = None) -> int:
    state = current_session_state()
    requested = state.ctx_id if ctx_id is None else int(ctx_id)
    if requested != state.ctx_id and (state.bot_kind or "group") != "master":
        raise PermissionError(f"ctx {requested} is outside current group scope")
    if not requested:
        raise ValueError("ctx_id is required")
    return requested


def is_master() -> bool:
    return (current_session_state().bot_kind or "group").lower() == "master"


def _database_path() -> str:
    return current_session_state().database_path or os.environ.get("YUUBOT_DB_PATH", "")


def _db_simple_ext() -> str:
    return current_session_state().database_simple_ext or os.environ.get("YUUBOT_DB_SIMPLE_EXT", "")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def workspace_root() -> Path:
    raw = current_session_state().workspace_root or os.environ.get("YUUBOT_WORKSPACE_ROOT", "")
    if not raw:
        raise RuntimeError("workspace_root is not available in this Python worker")
    return Path(raw).expanduser().resolve()
