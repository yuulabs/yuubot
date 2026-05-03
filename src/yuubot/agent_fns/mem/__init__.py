"""Memory functions exposed to RFC2 Python sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TypedDict, cast

from tortoise.queryset import QuerySet
from tortoise.expressions import Q

from yuubot.agent_fns.local import current_ctx_id, ensure_db_ready, is_master, local_config, service_payload
from yuubot.core.models import Memory, MemoryRecallTerm, MemoryTag
from yuubot.services.mem import MemoryService

__all__ = [
    "ensure_ready",
    "memories",
    "recall_memory",
    "save_memory",
    "archive_memory",
    "restore_memory",
    "Memory",
    "MemoryTag",
    "MemoryRecallTerm",
]


class MemoryEntry(TypedDict):
    id: int
    content: str
    ctx_id: int | None
    scope: Literal["private", "public"]
    source_user_id: int | None
    created_at: str
    last_accessed: str
    trashed_at: str
    tags: list[str]


class MemoryCurationResult(TypedDict):
    status: Literal["archived", "restored"]
    count: int
    ids: list[int]


async def ensure_ready() -> None:
    """Initialize worker-local ORM access to Yuubot's SQLite database."""
    await ensure_db_ready()


async def memories(
    *,
    ctx_id: int | None = None,
    scope: str | None = None,
    since: datetime | str | None = None,
    until: datetime | str | None = None,
    include_trashed: bool = False,
    limit: int = 200,
) -> QuerySet[Memory]:
    """Return a Tortoise QuerySet[Memory] for local memory queries.

    Memory fields: id, content, ctx_id, scope, created_at, last_accessed,
    source_user_id, trashed_at. Related tables are MemoryTag(tag) and
    MemoryRecallTerm(term).

    Group agents can only see public memories plus private memories in the
    current ctx_id. Master agents may pass another ctx_id or omit ctx_id, then
    chain arbitrary ORM filters:

        qs = await mem.memories(limit=500)
        rows = await qs.filter(content__icontains="猫").order_by("-last_accessed")
    """
    await ensure_db_ready()
    qs = Memory.all()
    if not include_trashed:
        qs = qs.filter(trashed_at__isnull=True)
    if scope:
        qs = qs.filter(scope=scope)
    if ctx_id is not None:
        qs = qs.filter(ctx_id=current_ctx_id(ctx_id))
    elif not is_master():
        current = current_ctx_id(None)
        qs = qs.filter(Q(scope="public") | Q(ctx_id=current, scope="private"))
    if since is not None:
        qs = qs.filter(created_at__gte=since)
    if until is not None:
        qs = qs.filter(created_at__lte=until)
    return qs.order_by("-last_accessed", "-id").limit(max(1, min(int(limit), 5000)))


async def recall_memory(query: str, *, limit: int = 5, scope: str | None = None) -> list[MemoryEntry]:
    """Find saved memories whose content or recall terms match query; returns memory entries with ids/tags/scope."""
    await ensure_db_ready()
    return cast(
        list[MemoryEntry],
        await MemoryService(config=local_config()).recall(
            service_payload(query=query, limit=limit, scope=scope or "")
        ),
    )


async def save_memory(content: str, *, tags: list[str] | None = None, scope: str = "private") -> MemoryEntry:
    """Save durable memory text and return id, content, ctx_id, scope, source_user_id, timestamps, and tags."""
    await ensure_db_ready()
    return cast(
        MemoryEntry,
        await MemoryService(config=local_config()).save(
            service_payload(content=content, tags=tags or [], scope=scope)
        ),
    )


async def archive_memory(memory_id: int) -> MemoryCurationResult:
    """Soft-delete one memory id and return status='archived', affected count, and ids attempted."""
    await ensure_db_ready()
    return cast(
        MemoryCurationResult,
        await MemoryService(config=local_config()).archive(service_payload(memory_id=memory_id)),
    )


async def restore_memory(memory_id: int) -> MemoryCurationResult:
    """Restore one archived memory id and return status='restored', affected count, and ids attempted."""
    await ensure_db_ready()
    return cast(
        MemoryCurationResult,
        await MemoryService(config=local_config()).restore(service_payload(memory_id=memory_id)),
    )
