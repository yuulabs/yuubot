"""Context helpers for RFC2 ``import yb`` functions."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import Any

import attrs
import msgspec

from yuubot.agent_fns.clients import DaemonClient, RecorderClient
from yuubot.services.base import InvalidScope


class Actor(msgspec.Struct, frozen=True):
    user_id: int = 0
    nickname: str = ""


class SessionState(msgspec.Struct, frozen=True):
    """Parsed yuubot session context, accessible in Python sessions as SESSION_STATE.

    Access fields via attribute (e.g. SESSION_STATE.ctx_id), not via json.dumps.
    """

    bot_kind: str = ""
    ctx_id: int = 0
    chat_type: str = ""
    group_id: int = 0
    user_id: int = 0
    conversation_id: str = ""
    agent_name: str = ""
    character_name: str = ""
    agent_id: str = ""
    task_id: str = ""
    bot_id: int = 0
    bot_name: str = ""
    workspace_root: str = ""
    database_path: str = ""
    database_simple_ext: str = ""
    recorder_base_url: str = ""
    napcat_http_base_url: str = ""
    daemon_base_url: str = ""
    daemon_self_url: str = ""
    delegate_depth: int = 0
    token: str = ""
    python_backend: str = ""
    supports_vision: bool = False
    raw: dict[str, Any] = msgspec.field(default_factory=dict)


@attrs.define(frozen=True)
class AgentFnContext:
    """Resolved host context for a single agent function call."""

    state: SessionState
    actor: Actor
    daemon: DaemonClient
    recorder: RecorderClient

    @property
    def ctx_id(self) -> int:
        return self.state.ctx_id

    @property
    def bot_kind(self) -> str:
        if self.state.bot_kind:
            return self.state.bot_kind
        return "group"

    def require_ctx(self, ctx_id: int | None = None) -> int:
        requested = self.ctx_id if ctx_id is None else ctx_id
        if requested != self.ctx_id and self.bot_kind != "master":
            raise InvalidScope(f"ctx {requested} is outside current group scope")
        return requested

    def service_payload(self, **extra: Any) -> dict[str, Any]:
        payload = {
            "bot_kind": self.bot_kind,
            "ctx_id": self.state.ctx_id,
            "chat_type": self.state.chat_type,
            "group_id": self.state.group_id,
            "user_id": self.state.user_id,
            "conversation_id": self.state.conversation_id,
            "agent_name": self.state.agent_name,
            "character_name": self.state.character_name or self.state.agent_name,
            "task_id": self.state.task_id,
            "bot_id": self.state.bot_id,
            "workspace_root": self.state.workspace_root,
            "database_path": self.state.database_path,
            "database_simple_ext": self.state.database_simple_ext,
            "recorder_base_url": self.state.recorder_base_url,
            "napcat_http_base_url": self.state.napcat_http_base_url,
            "daemon_base_url": self.state.daemon_base_url,
            "daemon_self_url": self.state.daemon_self_url,
        }
        payload.update(extra)
        return payload


def current_context() -> AgentFnContext:
    state = current_session_state()
    token = state.token or os.environ.get("YUUBOT_AGENT_TOKEN", "")
    recorder_base_url = state.recorder_base_url or os.environ.get("YUUBOT_RECORDER_URL", "")
    daemon_base_url = state.daemon_base_url or os.environ.get("YUUBOT_DAEMON_URL", "")
    return AgentFnContext(
        state=state,
        actor=Actor(user_id=state.user_id),
        recorder=RecorderClient(base_url=recorder_base_url, token=token),
        daemon=DaemonClient(base_url=daemon_base_url, token=token),
    )


def current_session_state() -> SessionState:
    """Return the current agent session's parsed SessionState.

    Also aliased as ``session_state`` in the ``yb`` module.
    """
    raw: Mapping[str, Any] = {}
    for source in _session_state_sources():
        try:
            state = source()
            as_dict = getattr(state, "as_dict", None)
            raw = as_dict() if callable(as_dict) else dict(state)
            break
        except Exception:
            raw = {}
    return session_state_from_mapping(raw)


def _session_state_sources() -> list[Any]:
    sources: list[Any] = []
    try:
        import builtins

        gss = getattr(builtins, "get_session_state", None)
        if callable(gss):
            sources.append(gss)
    except Exception:
        pass
    main = sys.modules.get("__main__")
    if main is not None:
        gss = getattr(main, "get_session_state", None)
        if callable(gss):
            sources.append(gss)
        state = getattr(main, "SESSION_STATE", None)
        if isinstance(state, Mapping):
            sources.append(lambda state=state: state)
    try:
        from yuubot.daemon.restricted_python import get_session_state

        sources.append(get_session_state)
    except Exception:
        pass
    return sources


def session_state_from_mapping(raw: Mapping[str, Any]) -> SessionState:
    """Build a SessionState from a plain mapping (e.g. from yuuagents kernel state)."""
    data = dict(raw)
    return SessionState(
        bot_kind=str(data.get("bot_kind", "") or ""),
        ctx_id=_int(data.get("ctx_id")),
        chat_type=str(data.get("chat_type", "") or ""),
        group_id=_int(data.get("group_id")),
        user_id=_int(data.get("user_id")),
        conversation_id=str(data.get("conversation_id", "") or ""),
        agent_name=str(data.get("agent_name", "") or ""),
        character_name=str(data.get("character_name", data.get("agent_name", "")) or ""),
        agent_id=str(data.get("agent_id", "") or ""),
        task_id=str(data.get("task_id", "") or ""),
        bot_id=_int(data.get("bot_id")),
        bot_name=str(data.get("bot_name", "") or ""),
        workspace_root=str(data.get("workspace_root", "") or ""),
        database_path=str(data.get("database_path", "") or ""),
        database_simple_ext=str(data.get("database_simple_ext", "") or ""),
        recorder_base_url=str(data.get("recorder_base_url", "") or ""),
        napcat_http_base_url=str(data.get("napcat_http_base_url", "") or ""),
        daemon_base_url=str(data.get("daemon_base_url", "") or ""),
        daemon_self_url=str(data.get("daemon_self_url", "") or ""),
        delegate_depth=_int(data.get("delegate_depth")),
        token=str(data.get("token", "") or ""),
        python_backend=str(data.get("python_backend", "") or ""),
        supports_vision=bool(data.get("supports_vision", False)),
        raw=data,
    )


def _int(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    try:
        if isinstance(value, int | float | str | bytes | bytearray):
            return int(value)
    except (TypeError, ValueError):
        return 0
    return 0
