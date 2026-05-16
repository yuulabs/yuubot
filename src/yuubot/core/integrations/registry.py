"""Integration factory registry."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from yuubot.core.capabilities import AnyCapabilitySpec
from yuubot.core.integrations.contracts import (
    IntegrationFactory,
    IntegrationKindInfo,
    integration_kind_info,
)


@dataclass
class IntegrationFactoryRegistry:
    """Code registry for integration factory implementations."""

    _factories: dict[str, IntegrationFactory] = field(default_factory=dict)

    def register(self, factory: IntegrationFactory) -> None:
        self._factories[factory.name] = factory

    def capability_specs(self) -> list[AnyCapabilitySpec]:
        return list(_unique_capabilities(self._all_capabilities()))

    def integration_kinds(self) -> list[IntegrationKindInfo]:
        """Return static admin-facing descriptors for every registered kind.

        The admin UI renders create/edit forms from this list. Ordering
        follows registration order so the UI can display a stable catalog.
        """
        return [
            integration_kind_info(factory)
            for factory in self._factories.values()
        ]

    def get(self, name: str) -> IntegrationFactory:
        try:
            return self._factories[name]
        except KeyError as exc:
            raise LookupError(
                f"integration factory {name!r} is not registered"
            ) from exc

    def _all_capabilities(self) -> Iterable[AnyCapabilitySpec]:
        for factory in self._factories.values():
            yield from factory.capability_specs()


def _unique_capabilities(
    capabilities: Iterable[AnyCapabilitySpec],
) -> list[AnyCapabilitySpec]:
    result: dict[str, AnyCapabilitySpec] = {}
    for capability in capabilities:
        result.setdefault(capability.id, capability)
    return list(result.values())


def default_integration_factories() -> IntegrationFactoryRegistry:
    """Factories available to a normal daemon process."""
    from yuubot.core.integrations.echo import EchoIntegrationFactory

    registry = IntegrationFactoryRegistry()
    registry.register(EchoIntegrationFactory())
    return registry
