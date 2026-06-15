"""Actor extension contracts."""

from __future__ import annotations

from typing import Protocol

from yuubot.core.bindings import ActorBinding
from yuubot.core.gateway import Mailbox
from yuubot.core.messages import IncomingMessage
from yuubot.resources.events import ResourceChanged


class Actor(Protocol):
    @property
    def actor_id(self) -> str: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def handle_resource_changed(self, event: ResourceChanged) -> None: ...

    async def handle_message(self, message: IncomingMessage) -> None: ...


class ActorFactory(Protocol):
    actor_type: str

    async def create(self, binding: ActorBinding, mailbox: Mailbox) -> Actor: ...
