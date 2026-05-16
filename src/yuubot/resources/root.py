"""Top-level persistence context for runtime components."""

from __future__ import annotations

from dataclasses import dataclass, field

from yuubot.events import EventBus
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.secrets import SecretCodec
from yuubot.resources.store.resource import Store


@dataclass
class Resources:
    """Process-local persistence handles shared by runtime components.

    Runtime code reads persisted configuration through `repository` and reacts
    to table-level events from `event_bus`.
    """

    store: Store
    secret_codec: SecretCodec
    event_bus: EventBus = field(default_factory=EventBus)
    repository: ResourceRepository = field(init=False)

    def __post_init__(self) -> None:
        self.repository = ResourceRepository(
            self.store,
            self.event_bus,
            self.secret_codec,
        )

    @classmethod
    async def from_store(
        cls,
        store: Store,
        *,
        secret_codec: SecretCodec,
        event_bus: EventBus | None = None,
    ) -> "Resources":
        return cls(
            store=store,
            secret_codec=secret_codec,
            event_bus=event_bus or EventBus(),
        )

    async def refresh(self) -> None:
        return

    async def close(self) -> None:
        await self.store.close()
