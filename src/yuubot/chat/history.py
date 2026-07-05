"""Durable History store and the in-memory helper used by conversations.

Full History (tool_specs + system_prompt prefix, then interaction items) is
persisted append-only. The prefix is written once at conversation creation and
replayed to the LLM on resume; it is stripped from every frontend-facing view.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import msgspec
from attrs import define

from ..db import Database
from ..domain.messages import (
    ContentItem,
    GenAudio,
    GenImage,
    GenReasoning,
    GenText,
    GenToolCall,
    HistoryItem,
    HistoryToolSpecs,
    InputMessage,
    LLMInput,
    SystemPrompt,
    ToolResult,
)

_TYPES: Final[dict[str, type[HistoryItem]]] = {
    "tool_specs": HistoryToolSpecs,
    "system_prompt": SystemPrompt,
    "input": InputMessage,
    "gen_text": GenText,
    "gen_reasoning": GenReasoning,
    "gen_tool_call": GenToolCall,
    "gen_image": GenImage,
    "gen_audio": GenAudio,
    "tool_result": ToolResult,
}

_KINDS: Final[dict[type[HistoryItem], str]] = {type_: kind for kind, type_ in _TYPES.items()}

PREFIX_KINDS: Final[frozenset[str]] = frozenset({"tool_specs", "system_prompt"})


@define
class HistoryStore:
    _db: Database

    @property
    def path(self) -> Path:
        return self._db.path

    async def append(self, conversation_id: str, item: HistoryItem) -> dict[str, object]:
        return (await self.extend(conversation_id, [item]))[0]

    async def extend(self, conversation_id: str, items: Sequence[HistoryItem]) -> list[dict[str, object]]:
        if not items:
            return []
        created_at = _now()
        rows: list[tuple[str, int, str, bytes, str]] = []
        async with self._db.transaction():
            cursor = await self._db.execute(
                "select coalesce(max(seq) + 1, 0) from history where conversation_id = ?",
                (conversation_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            seq = int(row[0])
            for item in items:
                rows.append((conversation_id, seq, _kind(item), msgspec.json.encode(item), created_at))
                seq += 1
            await self._db.executemany(
                "insert into history (conversation_id, seq, kind, payload, created_at) values (?, ?, ?, ?, ?)",
                rows,
            )
        return [_wrapped(seq, kind, payload, created_at) for _, seq, kind, payload, created_at in rows]

    async def load(self, conversation_id: str) -> list[HistoryItem]:
        cursor = await self._db.execute(
            "select kind, payload from history where conversation_id = ? order by seq",
            (conversation_id,),
        )
        rows = await cursor.fetchall()
        return [msgspec.json.decode(payload, type=_TYPES[kind]) for kind, payload in rows]

    async def list_conversations(self) -> list[dict[str, object]]:
        cursor = await self._db.execute(
            """
            select conversation_id,
                   sum(case when kind in ('tool_specs', 'system_prompt') then 0 else 1 end),
                   max(seq),
                   max(created_at)
            from history
            group by conversation_id
            order by max(seq) desc
            """
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": conversation_id,
                "message_count": message_count,
                "last_seq": last_seq,
                "last_active_at": last_active_at or None,
            }
            for conversation_id, message_count, last_seq, last_active_at in rows
        ]

    async def load_wrapped(self, conversation_id: str) -> list[dict[str, object]]:
        cursor = await self._db.execute(
            "select seq, kind, payload, created_at from history where conversation_id = ? order by seq",
            (conversation_id,),
        )
        rows = await cursor.fetchall()
        return [_wrapped(seq, kind, payload, created_at) for seq, kind, payload, created_at in rows]

    async def load_interaction_wrapped(self, conversation_id: str) -> list[dict[str, object]]:
        items = await self.load_wrapped(conversation_id)
        return [item for item in items if str(item["kind"]) not in PREFIX_KINDS]

    async def delete(self, conversation_id: str) -> bool:
        cursor = await self._db.execute("delete from history where conversation_id = ?", (conversation_id,))
        await self._db.commit()
        return cursor.rowcount > 0


@define
class HistoryHelper:
    """In-memory History for one live conversation, mirrored to HistoryStore by the owner."""

    items: list[HistoryItem]

    @classmethod
    async def load(
        cls,
        store: HistoryStore,
        conversation_id: str,
        *,
        tool_specs: list[dict[str, object]],
        system_prompt: str,
    ) -> "HistoryHelper":
        """Load persisted History, seeding the prefix for brand-new conversations."""
        items = await store.load(conversation_id)
        if items:
            return cls(items=_with_current_tool_specs(items, tool_specs))
        seeded: list[HistoryItem] = []
        if tool_specs:
            seeded.append(HistoryToolSpecs(specs=tool_specs))
        if system_prompt:
            seeded.append(SystemPrompt(text=system_prompt))
        if seeded:
            await store.extend(conversation_id, seeded)
        return cls(items=seeded)

    def append(self, item: HistoryItem) -> None:
        self.items.append(item)

    def extend(self, items: Sequence[HistoryItem]) -> None:
        self.items.extend(items)

    def interaction_items(self) -> list[HistoryItem]:
        return [item for item in self.items if not isinstance(item, (HistoryToolSpecs, SystemPrompt))]

    def to_llm_input(self) -> LLMInput:
        specs: list[dict[str, object]] = []
        messages: list[HistoryItem] = []
        for item in self.items:
            if isinstance(item, HistoryToolSpecs):
                specs = item.specs
            elif isinstance(item, SystemPrompt):
                messages.append(
                    InputMessage(role="developer", name="yuubot", content=[ContentItem(kind="text", text=item.text)])
                )
            else:
                messages.append(item)
        return LLMInput(tool_specs=specs, messages=messages)


def _with_current_tool_specs(items: list[HistoryItem], specs: list[dict[str, object]]) -> list[HistoryItem]:
    current = HistoryToolSpecs(specs=specs)
    replaced = False
    result: list[HistoryItem] = []
    for item in items:
        if isinstance(item, HistoryToolSpecs):
            if not replaced:
                result.append(current)
                replaced = True
            continue
        if not replaced and isinstance(item, SystemPrompt):
            result.append(current)
            replaced = True
        result.append(item)
    if not replaced:
        result.insert(0, current)
    return result


def _kind(item: HistoryItem) -> str:
    return _KINDS[type(item)]


def _wrapped(seq: int, kind: str, payload: bytes, created_at: str) -> dict[str, object]:
    return {
        "seq": seq,
        "kind": kind,
        "payload": msgspec.to_builtins(msgspec.json.decode(payload, type=_TYPES[kind])),
        "created_at": created_at or None,
    }


def _now() -> str:
    return datetime.now(UTC).isoformat()
