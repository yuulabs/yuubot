"""Actor mailbox wakeup delivery."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import msgspec
from attrs import define

from ..domain.messages import ActorMessage

if TYPE_CHECKING:
    from .core import ActorMailboxRegistry


class WakeupTarget(msgspec.Struct, frozen=True):
    kind: str
    actor_id: str
    conversation_id: str | None = None


class WakeupPayload(msgspec.Struct, frozen=True):
    text: str
    source: dict[str, object] = msgspec.field(default_factory=dict)


@define
class WakeupDelivery:
    """Delivers ActorMessage to actor mailboxes and emits wakeup.delivered."""

    _mailboxes: ActorMailboxRegistry
    emit: Callable[..., None]

    async def deliver(self, target: WakeupTarget, payload: WakeupPayload) -> None:
        await self._mailboxes.get(target.actor_id).send(
            ActorMessage(
                text=payload.text,
                conversation_id=target.conversation_id,
                source=dict(payload.source) | {"inbound_kind": target.kind},
            )
        )
        self.emit(
            "wakeup.delivered",
            actor_id=target.actor_id,
            inbound_kind=target.kind,
        )
