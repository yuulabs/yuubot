"""Vision functions exposed to RFC2 Python sessions."""

from __future__ import annotations

from typing import TypedDict, cast

from yuubot.agent_fns.local import ensure_db_ready, local_config, service_payload
from yuubot.services.media import MediaService

__all__ = ["describe_image", "image_metadata"]


class ImageMetadata(TypedDict, total=False):
    url: str
    local_path: str
    media_id: str
    mime_type: str
    bytes: int


class ImageDescription(ImageMetadata):
    media: str
    description: str
    cached: bool


async def describe_image(media: str, *, refresh: bool = False) -> ImageDescription:
    """Describe one image and return the text description plus resolved url/local_path metadata.

    Only available when SESSION_STATE.supports_vision is True.
    """
    await ensure_db_ready()
    return cast(
        ImageDescription,
        await MediaService(config=local_config()).describe_image(
            service_payload(media=media, refresh=refresh)
        ),
    )


async def image_metadata(media: str) -> ImageMetadata:
    """Resolve an image reference without vision inference; returns url or local_path plus mime/bytes if local."""
    return cast(
        ImageMetadata,
        await MediaService(config=local_config()).resolve_media(service_payload(media=media)),
    )
