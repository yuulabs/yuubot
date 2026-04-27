"""RFC2 agent-facing function package.

Each domain is a sub-package importable directly by characters::

    ya.PythonImport("yuubot.agent_fns.im", alias="im")
    ya.PythonImport("yuubot.agent_fns.mem", alias="mem")
    # … etc.

Functions call the daemon-local service boundary via ``_proxy._DaemonProxy``.
"""

from __future__ import annotations

from typing import Any

from yuubot.agent_fns.context import (
    SessionState,
    current_session_state,
    session_state_from_mapping,
)
from yuubot.agent_fns._proxy import _DaemonProxy
from yuubot.agent_fns.ops import bash

session_state = current_session_state


async def describe_image(media: str, *, refresh: bool = False) -> dict[str, Any]:
    """Describe a QQ, URL, or workspace image using the configured vision service.

    Only available when SESSION_STATE.supports_vision is True.
    """
    return await _DaemonProxy().call("media", "describe_image", media=media, refresh=refresh)


async def image_metadata(media: str) -> dict[str, Any]:
    """Resolve image metadata without generating a new description."""
    return await _DaemonProxy().call("media", "resolve_media", media=media)

__all__ = [
    "SessionState",
    "current_session_state",
    "describe_image",
    "image_metadata",
    "session_state",
    "session_state_from_mapping",
    "bash",
]
