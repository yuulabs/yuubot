"""Image library and generation functions exposed to RFC2 Python sessions."""

from __future__ import annotations

from typing import Any

from yuubot.agent_fns._proxy import _DaemonProxy

_p = _DaemonProxy()


async def save_image(media: str, *, tags: list[str] | None = None) -> dict[str, Any]:
    """Save an image reference into the controlled image library."""
    return await _p.call("media", "save_image", media=media, tags=tags or [])


async def search_images(query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Search images visible to the current actor."""
    return await _p.call("media", "search_images", query=query, limit=limit)


async def generate_image(prompt: str) -> dict[str, Any]:
    """Generate an image through a configured provider."""
    return {
        "status": "unavailable",
        "prompt": prompt,
        "message": "image generation provider wiring is not configured yet",
    }
