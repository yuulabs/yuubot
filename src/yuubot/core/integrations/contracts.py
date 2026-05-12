"""Integration extension contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from yuubot.core.capabilities import AnyCapability, AnyCapabilitySpec
from yuubot.resources.records import IntegrationRecord
from yuubot.resources.repository import ResourceRepository

if TYPE_CHECKING:
    from yuubot.core.gateway import Gateway


class IntegrationFactory(Protocol):
    """Registered once at startup; creates integration instances from records."""

    @property
    def plugin_id(self) -> str: ...

    def capability_specs(self) -> list[AnyCapabilitySpec]: ...

    async def create(
        self,
        record: IntegrationRecord,
        repository: ResourceRepository,
        *,
        gateway: Gateway,
    ) -> IntegrationInstance: ...


class IntegrationInstance(Protocol):
    def capabilities(self) -> list[AnyCapability]: ...

    async def close(self) -> None: ...
