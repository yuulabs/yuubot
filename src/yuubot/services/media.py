"""Media, vision, and image-library services for RFC2 agent functions."""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import attrs
from tortoise import connections

import yuullm
from yuubot.config import Config
from yuubot.core.media_paths import input_to_host
from yuubot.core.models import ImageEntry, VisionCache
from yuubot.services.base import InvalidScope, YuubotServiceError


_VISION_PROMPT = (
    "请用中文描述图片内容。按顺序说明画面主体、动作/表情、构图色调、"
    "可见文字和整体氛围。不要使用标题或编号。"
)


def _split(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _int(value: object, default: int = 0) -> int:
    try:
        if isinstance(value, int | float | str | bytes | bytearray) and not isinstance(value, bool):
            return int(value)
    except (TypeError, ValueError):
        return default
    return default


def _mime_for(path: Path) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "image/jpeg")


def _collect_text(item: yuullm.StreamItem) -> str:
    if isinstance(item, yuullm.Response):
        value = item.item
        if yuullm.is_text_item(value):
            return value["text"]
        return ""
    if isinstance(item, yuullm.Reasoning):
        value = item.item
        if yuullm.is_text_item(value):
            return value["text"]
        return ""
    return ""


@attrs.define
class MediaService:
    config: Config | None = None

    async def describe_image(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        media = str(payload.get("media", payload.get("path", "")) or "").strip()
        if not media:
            raise YuubotServiceError("media is required")
        resolved = await self.resolve_media(payload)
        cache_key = resolved.get("local_path") or resolved.get("url") or media
        refresh = bool(payload.get("refresh", False))
        if cache_key and not refresh:
            cached = await VisionCache.filter(host_path=cache_key).first()
            if cached is not None:
                return {
                    "media": media,
                    "description": cached.description,
                    "cached": True,
                    **resolved,
                }
        image_url = resolved.get("url", "")
        local_path = resolved.get("local_path", "")
        if local_path:
            path = Path(local_path)
            if not path.is_file():
                raise YuubotServiceError(f"image file not found: {local_path}")
            data = base64.b64encode(path.read_bytes()).decode()
            image_url = f"data:{_mime_for(path)};base64,{data}"
        if not image_url:
            raise YuubotServiceError("could not resolve image URL or local path")
        if self.config is None:
            raise YuubotServiceError("vision model requires config")

        from yuubot.model_resolution import ModelResolver

        resolver = ModelResolver(self.config)
        client, resolved_model = await resolver.resolve_role_llm("vision")
        stream, _store = await client.stream(
            [
                yuullm.user(
                    _VISION_PROMPT,
                    {"type": "image_url", "image_url": {"url": image_url}},
                )
            ],
            model=resolved_model.resolved_model,
        )
        description = ""
        async for item in stream:
            description += _collect_text(item)
        description = description.strip()
        if not description:
            raise YuubotServiceError("vision model returned an empty description")
        if cache_key:
            await VisionCache.update_or_create(
                host_path=cache_key,
                defaults={"description": description},
            )
        return {
            "media": media,
            "description": description,
            "cached": False,
            **resolved,
        }

    async def resolve_media(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        media = str(payload.get("media", payload.get("path", "")) or "").strip()
        if not media:
            raise YuubotServiceError("media is required")
        if media.startswith(("http://", "https://", "data:")):
            return {"url": media, "local_path": "", "media_id": ""}
        local_path = input_to_host(media)
        path = Path(local_path).expanduser()
        if not path.is_file():
            raise YuubotServiceError(f"media file not found: {path}")
        return {
            "url": "",
            "local_path": str(path),
            "media_id": str(path),
            "mime_type": _mime_for(path),
            "bytes": path.stat().st_size,
        }

    async def save_image(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        resolved = await self.resolve_media(payload)
        local_path = resolved.get("local_path", "")
        if not local_path:
            raise InvalidScope("only local files can be saved to the image library")
        entry, created = await ImageEntry.get_or_create(
            local_path=local_path,
            defaults={
                "description": str(payload.get("description", "") or ""),
                "tags": _split(payload.get("tags")),
                "source_msg_id": _int(payload.get("source_msg_id")) or None,
            },
        )
        if not created:
            entry.description = str(payload.get("description", entry.description) or "")
            entry.tags = _split(payload.get("tags"))
            source_msg_id = _int(payload.get("source_msg_id"))
            if source_msg_id:
                entry.source_msg_id = source_msg_id
            await entry.save()
        return self._image_dict(entry)

    async def search_images(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        query = str(payload.get("query", "") or "").strip()
        tags = set(_split(payload.get("tags")))
        limit = max(1, min(_int(payload.get("limit"), 10), 100))
        entries: list[ImageEntry]
        if query:
            ids = await self._image_fts_ids(query)
            if ids:
                entries = await ImageEntry.filter(id__in=ids).limit(limit)
            else:
                entries = await ImageEntry.filter(description__icontains=query).limit(limit)
        else:
            entries = await ImageEntry.all().order_by("-created_at").limit(limit * (3 if tags else 1))
        if tags:
            entries = [entry for entry in entries if tags & set(entry.tags or [])][:limit]
        return [self._image_dict(entry) for entry in entries[:limit]]

    async def delete_image(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        image_id = _int(payload.get("image_id", payload.get("id")))
        if not image_id:
            raise YuubotServiceError("image_id is required")
        count = await ImageEntry.filter(id=image_id).delete()
        return {"status": "deleted" if count else "not_found", "id": image_id}

    async def list_tags(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        del payload
        entries = await ImageEntry.all()
        counts: dict[str, int] = {}
        for entry in entries:
            for tag in entry.tags or []:
                counts[str(tag)] = counts.get(str(tag), 0) + 1
        return [
            {"tag": tag, "count": count}
            for tag, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]

    def _image_dict(self, entry: ImageEntry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "local_path": entry.local_path,
            "description": entry.description,
            "tags": list(entry.tags or []),
            "source_msg_id": entry.source_msg_id,
            "created_at": entry.created_at.isoformat() if entry.created_at else "",
        }

    async def _image_fts_ids(self, query: str) -> list[int]:
        try:
            conn = connections.get("default")
            rows = await conn.execute_query_dict(
                "SELECT rowid FROM images_fts WHERE images_fts MATCH ?",
                [" OR ".join(query.split())],
            )
        except Exception:
            return []
        return [int(row["rowid"]) for row in rows]
