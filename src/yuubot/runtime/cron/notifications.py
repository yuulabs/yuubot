"""Notification delivery for cron reminders."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

import msgspec
from attrs import define, field

from ..event_payloads import NotificationDeliveredPayload
from .models import NotificationChannel, ReminderAction

if TYPE_CHECKING:
    from ..core import Runtime

_log = logging.getLogger(__name__)


class PushSubscription(msgspec.Struct, frozen=True):
    id: str
    endpoint: str
    keys: dict[str, str]
    created_at: str = ""
    updated_at: str = ""


class NotificationChannelHandler(Protocol):
    async def deliver(
        self,
        job_id: str,
        title: str,
        body: str,
        meta: dict[str, object],
    ) -> None: ...


@define
class BrowserNotificationHandler:
    _runtime: Runtime

    async def deliver(
        self,
        job_id: str,
        title: str,
        body: str,
        meta: dict[str, object],
    ) -> None:
        self._runtime.emit(
            NotificationDeliveredPayload(job_id, title, body, meta)
        )


@define
class WebPushNotificationHandler:
    _runtime: Runtime

    async def deliver(
        self,
        job_id: str,
        title: str,
        body: str,
        meta: dict[str, object],
    ) -> None:
        from .vapid import send_web_push

        subscriptions = await self._runtime.push_subscriptions.list_subscriptions()
        if not subscriptions:
            return
        payload = msgspec.json.encode({"title": title, "body": body, "job_id": job_id, "meta": meta}).decode()
        for subscription in subscriptions:
            await send_web_push(
                self._runtime.data_dir,
                subscription,
                payload,
            )


@define
class EmailNotificationHandler:
    async def deliver(
        self,
        job_id: str,
        title: str,
        body: str,
        meta: dict[str, object],
    ) -> None:
        _log.info("email notification not implemented for cron job %s: %s", job_id, title)


@define
class NotificationDispatcher:
    handlers: dict[str, NotificationChannelHandler] = field(factory=dict)

    @classmethod
    def create(cls, runtime: Runtime) -> NotificationDispatcher:
        return cls(
            handlers={
                "browser": BrowserNotificationHandler(runtime),
                "web_push": WebPushNotificationHandler(runtime),
                "email": EmailNotificationHandler(),
            }
        )

    async def deliver(self, job_id: str, action: ReminderAction, meta: dict[str, object]) -> None:
        for channel in action.channels or (NotificationChannel("browser"),):
            handler = self.handlers.get(channel.kind)
            if handler is None:
                _log.warning("unknown notification channel %s for cron job %s", channel.kind, job_id)
                continue
            await handler.deliver(job_id=job_id, title=action.title, body=action.body, meta=meta)
