"""Message gateway: route inbound messages to actor mailboxes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from yuuagents.core.mailbox import MailBox as Mailbox

from yuubot.core.messages import IncomingMessage, MessageSource
from yuubot.core.routing import RouteBindings


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
        if actor_id in self._mailboxes:
            return self._mailboxes[actor_id]
        mailbox = Mailbox(mailbox_id=f"actor:{actor_id}")
        self._mailboxes[actor_id] = mailbox
        return mailbox

    def find_mailbox(self, actor_id: str) -> Mailbox | None:
        return self._mailboxes.get(actor_id)

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
