"""Vision functions exposed to RFC2 Python sessions."""

from __future__ import annotations

from typing import Any

from yuubot.agent_fns._proxy import _DaemonProxy

_p = _DaemonProxy()


async def describe_image(media: str, *, refresh: bool = False) -> dict[str, Any]:
    """Describe a QQ, URL, or workspace image using the configured vision service.

    Only available when SESSION_STATE.supports_vision is True.
    """
    return await _p.call("media", "describe_image", media=media, refresh=refresh)


async def image_metadata(media: str) -> dict[str, Any]:
    """Resolve image metadata without generating a new description."""
    return await _p.call("media", "resolve_media", media=media)
