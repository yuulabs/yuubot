"""Push subscription persistence."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import msgspec
from attrs import define

from ...util.time import utc_now_iso

from .notifications import PushSubscription

if TYPE_CHECKING:
    from ...db import Database

def new_push_subscription_id() -> str:
    return f"ps-{uuid.uuid4().hex[:12]}"


@define
class PushSubscriptionStore:
    _db: Database

    async def list_subscriptions(self) -> list[PushSubscription]:
        cursor = await self._db.execute("select payload from app_push_subscriptions order by id")
        rows = await cursor.fetchall()
        return [msgspec.json.decode(payload, type=PushSubscription) for payload, in rows]

    async def put(self, subscription: PushSubscription) -> PushSubscription:
        timestamp = utc_now_iso()
        stored = PushSubscription(
            subscription.id,
            subscription.endpoint,
            subscription.keys,
            subscription.created_at or timestamp,
            timestamp,
        )
        await self._db.execute(
            """
            insert into app_push_subscriptions (id, payload, created_at, updated_at)
            values (?, ?, ?, ?)
            on conflict(id) do update set
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (stored.id, msgspec.json.encode(stored), stored.created_at, stored.updated_at),
        )
        await self._db.commit()
        return stored

    async def delete(self, subscription_id: str) -> bool:
        cursor = await self._db.execute("delete from app_push_subscriptions where id = ?", (subscription_id,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def find_by_endpoint(self, endpoint: str) -> PushSubscription | None:
        for subscription in await self.list_subscriptions():
            if subscription.endpoint == endpoint:
                return subscription
        return None
