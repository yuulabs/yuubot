"""Message gateway: route inbound messages to actor mailboxes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from yuubot.core.messages import IncomingMessage, MessageSource
from yuubot.core.routing import RouteBindings


@dataclass
class Mailbox:
    """An actor input port."""

    _queue: asyncio.Queue[IncomingMessage] = field(default_factory=asyncio.Queue)

    async def put(self, message: IncomingMessage) -> None:
        await self._queue.put(message)

    async def get(self) -> IncomingMessage:
        return await self._queue.get()


@dataclass
class IntegrationIngress:
    """Trusted integration-side ingress that stamps source identity."""

    integration_id: str
    _ingest: Callable[[IncomingMessage], Awaitable[None]]

    async def emit(self, message: IncomingMessage) -> None:
        message.source = MessageSource(
            producer="integration",
            id=self.integration_id,
            path=message.source.path,
        )
        await self._ingest(message)


@dataclass
class Gateway:
    """Low-level message gateway."""

    routes: RouteBindings
    _mailboxes: dict[str, Mailbox] = field(default_factory=dict, init=False)

    def get_mailbox(self, actor_id: str) -> Mailbox:
        mailbox = Mailbox()
        self._mailboxes[actor_id] = mailbox
        return mailbox

    def close_mailbox(self, actor_id: str) -> None:
        self._mailboxes.pop(actor_id, None)

    def open_integration(self, integration_id: str) -> IntegrationIngress:
        return IntegrationIngress(
            integration_id=integration_id,
            _ingest=self.ingest,
        )

    def update_bindings(self, bindings: RouteBindings) -> None:
        self.routes = bindings

    def mailbox_count(self) -> int:
        return len(self._mailboxes)

    async def ingest(self, message: IncomingMessage) -> None:
        for actor_id in self.routes.resolve(message):
            mailbox = self._mailboxes.get(actor_id)
            if mailbox is not None:
                await mailbox.put(message)

    async def on_message(self, message: IncomingMessage) -> None:
        await self.ingest(message)
