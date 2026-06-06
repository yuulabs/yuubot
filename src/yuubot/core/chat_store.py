"""Async persistence for chat messages with FTS search and dialog browsing.

Provides a ChatStore class with dependency-injected Store that supports
CRUD, cursor-based browsing, FTS5 full-text search, and aggregated
dialog listing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
import msgspec
from tortoise import connections

from yuubot.core.chat_render import render_dialog_line, render_message_text
from yuubot.resources.records import ChatMessageRecord
from yuubot.resources.store.resource import Store


@dataclass
class DialogSummary:
    """Aggregated metadata for a single dialog thread."""

    dialog_id: str
    message_count: int
    last_message_preview: str
    updated_at: str  # ISO format


@dataclass
class BrowseResult:
    """Cursor-based pagination result with has_more sentinel."""

    messages: list[ChatMessageRecord]
    has_more: bool


def _dict_to_record(d: dict[str, object]) -> ChatMessageRecord:
    return msgspec.convert(d, type=ChatMessageRecord, strict=False)


@dataclass
class ChatStore:
    """Async persistence for chat messages with FTS search.

    Receives a Store instance via dependency injection so all DB
    operations execute within an active Tortoise context.
    """

    store: Store

    def _conn(self):
        return connections.get("default")

    async def save_message(
        self,
        dialog_id: str,
        message_id: str,
        role: str,
        raw_content: str,
        actor_id: str,
        sender_id: str,
        sender_name: str,
        timestamp: int | None = None,
    ) -> ChatMessageRecord:
        """Persist a single message, computing text_content from raw_content."""
        content_items: list[dict[str, object]] = msgspec.json.decode(
            raw_content.encode()
        )
        extracted = render_message_text(content_items)
        msg_ts = timestamp if timestamp is not None else int(time.time())
        text_content = render_dialog_line(
            message_id, sender_name, msg_ts, extracted
        )

        with self.store.db.activate():
            conn = self._conn()
            now = datetime.now(timezone.utc).isoformat()
            result = await conn.execute_insert(
                """INSERT INTO chat_messages
                   (dialog_id, message_id, role, raw_content, text_content,
                    actor_id, sender_id, sender_name, timestamp, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    dialog_id, message_id, role, raw_content, text_content,
                    actor_id, sender_id, sender_name, msg_ts, now,
                ],
            )
        row_id = result
        return ChatMessageRecord(
            id=row_id,
            dialog_id=dialog_id,
            message_id=message_id,
            role=role,
            raw_content=raw_content,
            text_content=text_content,
            actor_id=actor_id,
            sender_id=sender_id,
            sender_name=sender_name,
            timestamp=msg_ts,
        )

    async def get_message(
        self, message_id: str
    ) -> ChatMessageRecord | None:
        """Resolve a single message by its unique message_id."""
        with self.store.db.activate():
            conn = self._conn()
            rows = await conn.execute_query_dict(
                "SELECT * FROM chat_messages WHERE message_id = ? LIMIT 1",
                [message_id],
            )
        if not rows:
            return None
        return _dict_to_record(rows[0])

    async def list_dialogs(self) -> list[DialogSummary]:
        """Return aggregated dialog list ordered by most recent activity."""
        with self.store.db.activate():
            conn = self._conn()
            rows = await conn.execute_query_dict(
                """
                SELECT
                    dialog_id,
                    COUNT(*) as message_count,
                    (SELECT text_content FROM chat_messages cm2
                     WHERE cm2.dialog_id = cm.dialog_id
                     ORDER BY cm2.timestamp DESC LIMIT 1) as last_message_preview,
                    MAX(created_at) as updated_at
                FROM chat_messages cm
                GROUP BY dialog_id
                ORDER BY updated_at DESC
                """
            )
        return [
            DialogSummary(
                dialog_id=row["dialog_id"],
                message_count=row["message_count"],
                last_message_preview=row["last_message_preview"] or "",
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    async def browse_messages(
        self,
        dialog_id: str,
        *,
        before: str | None = None,
        after: str | None = None,
        since: int | None = None,
        until: int | None = None,
        limit: int = 50,
        role: str | None = None,
    ) -> BrowseResult:
        """Browse messages in a dialog with cursor- and time-based pagination.

        Cursor parameters (before/after) use message_id; time parameters
        (since/until) use Unix epoch timestamps.  When both are supplied
        the filters are AND'd together.
        """
        with self.store.db.activate():
            conn = self._conn()
            where = ["dialog_id = ?"]
            params: list[object] = [dialog_id]

            if before:
                ts = await self._resolve_msg_timestamp(before)
                if ts is not None:
                    where.append("timestamp < ?")
                    params.append(ts)

            if after:
                ts = await self._resolve_msg_timestamp(after)
                if ts is not None:
                    where.append("timestamp >= ?")
                    params.append(ts)
                    where.append("message_id != ?")
                    params.append(after)

            if since is not None:
                where.append("timestamp >= ?")
                params.append(since)
            if until is not None:
                where.append("timestamp <= ?")
                params.append(until)
            if role:
                where.append("role = ?")
                params.append(role)

            params.append(limit + 1)
            sql = (
                f"SELECT * FROM chat_messages WHERE {' AND '.join(where)} "
                f"ORDER BY timestamp LIMIT ?"
            )
            rows = await conn.execute_query_dict(sql, params)

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        return BrowseResult(
            messages=[_dict_to_record(row) for row in rows],
            has_more=has_more,
        )

    async def _resolve_msg_timestamp(self, message_id: str) -> int | None:
        """Resolve the timestamp of a message by its message_id."""
        conn = self._conn()
        rows = await conn.execute_query_dict(
            "SELECT timestamp FROM chat_messages WHERE message_id = ? LIMIT 1",
            [message_id],
        )
        if rows:
            return int(rows[0]["timestamp"])
        return None

    async def search_messages(
        self,
        dialog_id: str,
        q: str,
        *,
        limit: int = 50,
    ) -> BrowseResult:
        """Full-text search messages within a dialog using SQLite FTS5.

        The search query is treated as a phrase match against the
        chat_messages_fts virtual table, filtered to a single dialog.
        """
        with self.store.db.activate():
            conn = self._conn()
            safe_q = q.replace('"', '""')
            rows = await conn.execute_query_dict(
                """
                SELECT cm.* FROM chat_messages cm
                JOIN chat_messages_fts fts ON cm.id = fts.rowid
                WHERE cm.dialog_id = ? AND chat_messages_fts MATCH ?
                ORDER BY cm.timestamp DESC
                LIMIT ?
                """,
                [dialog_id, f'"{safe_q}"', limit + 1],
            )

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        return BrowseResult(
            messages=[_dict_to_record(row) for row in rows],
            has_more=has_more,
        )
