"""Image library and generation functions exposed to RFC2 Python sessions."""

from __future__ import annotations

from typing import Literal, TypedDict, cast

from yuubot.agent_fns.local import ensure_db_ready, local_config, service_payload
from yuubot.services.media import MediaService

__all__ = ["save_image", "search_images", "generate_image"]


class ImageEntry(TypedDict):
    id: int
    local_path: str
    description: str
    tags: list[str]
    source_msg_id: int | None
    created_at: str


class GenerateImageUnavailable(TypedDict):
    status: Literal["unavailable"]
    prompt: str
    message: str


async def save_image(media: str, *, tags: list[str] | None = None) -> ImageEntry:
    """Save a local image into the image library and return its id, local path, description, and tags."""
    await ensure_db_ready()
    return cast(
        ImageEntry,
        await MediaService(config=local_config()).save_image(
            service_payload(media=media, tags=tags or [])
        ),
    )


async def search_images(query: str, *, limit: int = 10) -> list[ImageEntry]:
    """Search the image library by description text and return matching image entries with local paths."""
    await ensure_db_ready()
    return cast(
        list[ImageEntry],
        await MediaService(config=local_config()).search_images(
            service_payload(query=query, limit=limit)
        ),
    )


async def generate_image(prompt: str) -> GenerateImageUnavailable:
    """Return an unavailable status because image generation provider wiring is not configured yet."""
    return {
        "status": "unavailable",
        "prompt": prompt,
        "message": "image generation provider wiring is not configured yet",
    }
