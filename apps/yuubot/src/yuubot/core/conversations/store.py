"""Conversation persistence operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import msgspec
import yuullm
from tortoise import Model

from yuubot.core.assembly._history_codec import decode_prompt_item, encode_prompt_item
from yuubot.resources.orm import from_orm
from yuubot.resources.records import ConversationHistoryItemRecord, ConversationRecord
from yuubot.resources.secrets import SecretCodec
from yuubot.resources.store.models import (
    ConversationHistoryItemORM,
    ConversationMessageORM,
    ConversationORM,
)
from yuubot.resources.store.protocol import to_builtins
from yuubot.resources.store.resource import Store


def _conversation_sort_key(record: ConversationRecord) -> tuple[float, str]:
    timestamp = record.updated_at or record.created_at
    if timestamp is None:
        return (0.0, record.conversation_id)
    return (timestamp.timestamp(), record.conversation_id)


@dataclass
class ConversationStore:
    store: Store
    secret_codec: SecretCodec | None = None

    async def create_conversation_row(
        self,
        *,
        conversation_id: str,
        actor_id: str,
        title: str = "",
        reply_address: str = "",
        metadata: dict[str, object] | None = None,
    ) -> ConversationRecord:
        with self.store.db.activate():
            row = await ConversationORM.create(
                conversation_id=conversation_id,
                actor_id=actor_id,
                title=title,
                reply_address=reply_address,
                metadata=metadata or {},
            )
            return await self._record_from_row(row)

    async def get_conversation(
        self,
        conversation_id: str,
    ) -> ConversationRecord | None:
        with self.store.db.activate():
            row = await ConversationORM.get_or_none(
                conversation_id=conversation_id,
            )
            if row is None:
                return None
            return await self._record_from_row(row)

    async def conversation_exists(self, conversation_id: str) -> bool:
        with self.store.db.activate():
            return await ConversationORM.filter(
                conversation_id=conversation_id,
            ).exists()

    async def list_conversations(
        self,
        *,
        actor_id: str | None = None,
    ) -> list[ConversationRecord]:
        with self.store.db.activate():
            if actor_id:
                query = ConversationORM.filter(actor_id=actor_id)
            else:
                query = ConversationORM.all()
            rows = await query
            records = [await self._record_from_row(r) for r in rows]
        return sorted(records, key=_conversation_sort_key, reverse=True)

    async def _record_from_row(self, row: Model) -> ConversationRecord:
        return await from_orm(
            row,
            ConversationRecord,
            secret_codec=self.secret_codec,
        )

    async def update_title_if_empty(
        self,
        conversation_id: str,
        title: str,
    ) -> bool:
        if not title:
            return False
        with self.store.db.activate():
            updated = await ConversationORM.filter(
                conversation_id=conversation_id,
                title="",
            ).update(title=title)
        return updated > 0

    async def delete_conversation(self, conversation_id: str) -> bool:
        async with self.store.transaction():
            with self.store.db.activate():
                exists = await ConversationORM.filter(
                    conversation_id=conversation_id,
                ).exists()
                if not exists:
                    return False
                await ConversationMessageORM.filter(
                    conversation_id=conversation_id,
                ).delete()
                await ConversationHistoryItemORM.filter(
                    conversation_id=conversation_id,
                ).delete()
                await ConversationORM.filter(
                    conversation_id=conversation_id,
                ).delete()
        return True

    # ── Ordered history items (canonical conversation state) ──────────

    async def append_history_item(
        self,
        conversation_id: str,
        item: yuullm.PromptItem,
    ) -> ConversationHistoryItemRecord:
        item_kind, item_json = encode_prompt_item(item)
        with self.store.db.activate():
            row = await ConversationHistoryItemORM.create(
                conversation_id=conversation_id,
                item_kind=item_kind,
                item_json=item_json,
            )
            now = datetime.now()
            await ConversationORM.filter(
                conversation_id=conversation_id,
            ).update(updated_at=now)
        return msgspec.convert(
            to_builtins(row), type=ConversationHistoryItemRecord, strict=False
        )

    async def append_history_items(
        self,
        conversation_id: str,
        items: list[yuullm.PromptItem],
    ) -> list[ConversationHistoryItemRecord]:
        if not items:
            return []
        encoded = [encode_prompt_item(item) for item in items]
        with self.store.db.activate():
            async with self.store.transaction():
                rows: list[ConversationHistoryItemRecord] = []
                for item_kind, item_json in encoded:
                    row = await ConversationHistoryItemORM.create(
                        conversation_id=conversation_id,
                        item_kind=item_kind,
                        item_json=item_json,
                    )
                    rows.append(
                        msgspec.convert(
                            to_builtins(row),
                            type=ConversationHistoryItemRecord,
                            strict=False,
                        )
                    )
                now = datetime.now()
                await ConversationORM.filter(
                    conversation_id=conversation_id,
                ).update(updated_at=now)
        return rows

    async def list_history_items(
        self,
        conversation_id: str,
    ) -> list[ConversationHistoryItemRecord]:
        with self.store.db.activate():
            rows = (
                await ConversationHistoryItemORM.filter(
                    conversation_id=conversation_id,
                )
                .order_by("id")
                .limit(1000)
            )
        return [
            msgspec.convert(
                to_builtins(r), type=ConversationHistoryItemRecord, strict=False
            )
            for r in rows
        ]

    async def history(self, conversation_id: str) -> yuullm.History:
        rows = await self.list_history_items(conversation_id)
        return [decode_prompt_item(row.item_kind, row.item_json) for row in rows]

