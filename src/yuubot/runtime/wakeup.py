"""Actor mailbox wakeup delivery."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .event_payloads import EmitFn, WakeupDeliveredPayload

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
    emit: EmitFn

    async def deliver(self, target: WakeupTarget, payload: WakeupPayload) -> None:
        await self._mailboxes.get(target.actor_id).send(
            ActorMessage(
                payload.text,
                target.conversation_id,
                dict(payload.source) | {"inbound_kind": target.kind},
            )
        )
        self.emit(WakeupDeliveredPayload(target.actor_id, target.kind))
