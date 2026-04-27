"""Memory domain service shared by CLI and RFC2 agent functions."""

from __future__ import annotations

import builtins
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

import attrs
from tortoise import connections
from tortoise.expressions import Q

from yuubot.config import Config
from yuubot.core.models import Memory, MemoryRecallTerm, MemoryTag
from yuubot.services.base import AccessDenied, YuubotServiceError


def _is_master(payload: Mapping[str, Any]) -> bool:
    return str(payload.get("bot_kind", "")).lower() == "master"


def _ctx_id(payload: Mapping[str, Any]) -> int | None:
    value = payload.get("ctx_id")
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _split(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for part in value.split(",") for item in part.split() if item.strip()]
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _int(value: object, default: int = 0) -> int:
    try:
        if isinstance(value, int | float | str | bytes | bytearray) and not isinstance(value, bool):
            return int(value)
    except (TypeError, ValueError):
        return default
    return default


async def _fts_ids(words: list[str]) -> list[int]:
    if not words:
        return []
    conn = connections.get("default")
    expr = " OR ".join(words)
    try:
        rows = await conn.execute_query_dict(
            "SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?",
            [expr],
        )
    except Exception:
        return []
    return [int(row["rowid"]) for row in rows]


async def _memory_dict(memory: Memory) -> dict[str, Any]:
    tags = await MemoryTag.filter(memory_id=memory.id).values_list("tag", flat=True)
    return {
        "id": memory.id,
        "content": memory.content,
        "ctx_id": memory.ctx_id,  # type: ignore[attr-defined]
        "scope": memory.scope,
        "source_user_id": memory.source_user_id,
        "created_at": memory.created_at.isoformat() if memory.created_at else "",
        "last_accessed": memory.last_accessed.isoformat() if memory.last_accessed else "",
        "trashed_at": memory.trashed_at.isoformat() if memory.trashed_at else "",
        "tags": list(tags),
    }


@attrs.define
class MemoryService:
    _service_name = "mem"
    config: Config | None = None

    async def recall(self, payload: Mapping[str, Any]) -> builtins.list[dict[str, Any]]:
        query = str(payload.get("query", "") or "").strip()
        words = _split(payload.get("words")) or query.split()
        tags = _split(payload.get("tags"))
        limit = max(1, min(_int(payload.get("limit"), 5), 100))
        include_trashed = bool(payload.get("trash", False))
        show_all = _is_master(payload) and str(payload.get("scope", "") or "").lower() in {"all", "global", "*"}

        q = Q()
        fts_ids = await _fts_ids(words)
        if words:
            if fts_ids:
                q &= Q(id__in=fts_ids)
            else:
                word_q = Q()
                for word in words:
                    word_q |= Q(content__icontains=word)
                q &= word_q
        if tags:
            tag_ids = await MemoryTag.filter(tag__in=tags).values_list("memory_id", flat=True)
            if not tag_ids:
                return []
            q &= Q(id__in=list(tag_ids))
        if not include_trashed:
            q &= Q(trashed_at__isnull=True)
        if not show_all:
            ctx_id = _ctx_id(payload)
            if ctx_id is None:
                q &= Q(scope="public")
            else:
                q &= Q(scope="public") | Q(ctx_id=ctx_id, scope="private")
        if not words and not tags:
            return []
        memories = await Memory.filter(q).order_by("-last_accessed", "-id").limit(limit)
        if memories:
            await Memory.filter(id__in=[memory.id for memory in memories]).update(
                last_accessed=datetime.now(timezone.utc),
            )
        return [await _memory_dict(memory) for memory in memories]

    async def save(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        content = str(payload.get("content", "") or "").strip()
        if not content:
            raise YuubotServiceError("memory content is empty")
        max_length = self.config.memory.max_length if self.config is not None else 500
        if len(content) > max_length:
            raise YuubotServiceError(f"memory content is too long ({len(content)} > {max_length})")
        requested_scope = str(payload.get("scope", "private") or "private").lower()
        if requested_scope in {"public", "global"}:
            if not _is_master(payload):
                raise AccessDenied("only master may save public memories")
            scope = "public"
            ctx_id = None
        else:
            scope = "private"
            ctx_id = _ctx_id(payload)
        memory = await Memory.create(
            content=content,
            ctx_id=ctx_id,
            scope=scope,
            source_user_id=_int(payload.get("user_id")) or None,
        )
        for tag in _split(payload.get("tags")):
            await MemoryTag.get_or_create(memory=memory, tag=tag)
        for term in _split(payload.get("recall_terms")):
            await MemoryRecallTerm.get_or_create(memory=memory, term=term)
        return await _memory_dict(memory)

    async def archive(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_curator(payload)
        ids = self._ids(payload)
        if not ids:
            raise YuubotServiceError("memory id is required")
        count = await Memory.filter(id__in=ids, trashed_at__isnull=True).update(
            trashed_at=datetime.now(timezone.utc),
        )
        return {"status": "archived", "count": count, "ids": ids}

    async def restore(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._require_curator(payload)
        ids = self._ids(payload)
        if not ids:
            raise YuubotServiceError("memory id is required")
        count = await Memory.filter(id__in=ids, trashed_at__isnull=False).update(trashed_at=None)
        return {"status": "restored", "count": count, "ids": ids}

    async def list(self, payload: Mapping[str, Any]) -> builtins.list[dict[str, Any]]:
        limit = max(1, min(_int(payload.get("limit"), 50), 200))
        include_trashed = bool(payload.get("trash", False))
        show_all = _is_master(payload) and bool(payload.get("all", False))
        q = Q(trashed_at__isnull=not include_trashed)
        if not show_all:
            ctx_id = _ctx_id(payload)
            if ctx_id is None:
                q &= Q(scope="public")
            else:
                q &= Q(scope="public") | Q(ctx_id=ctx_id, scope="private")
        memories = await Memory.filter(q).order_by("-last_accessed", "-id").limit(limit)
        return [await _memory_dict(memory) for memory in memories]

    async def curate(self, payload: Mapping[str, Any]) -> object:
        action = str(payload.get("action", "") or "")
        if action == "archive":
            return await self.archive(payload)
        if action == "restore":
            return await self.restore(payload)
        if action == "list":
            return await self.list(payload)
        raise YuubotServiceError(f"unknown memory curation action: {action}")

    def _require_curator(self, payload: Mapping[str, Any]) -> None:
        character = str(payload.get("character_name", payload.get("agent_name", "")) or "")
        if not _is_master(payload) and character not in ("group_mem_curator", "master_mem_curator"):
            raise AccessDenied("memory curation is restricted to master or mem_curator")

    def _ids(self, payload: Mapping[str, Any]) -> builtins.list[int]:
        raw = payload.get("ids", payload.get("memory_id"))
        if isinstance(raw, Sequence) and not isinstance(raw, str | bytes | bytearray):
            return [_int(item) for item in raw if _int(item)]
        value = _int(raw)
        return [value] if value else []
