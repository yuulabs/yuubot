"""Integration factory registry."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from yuubot.core.capabilities import AnyCapabilitySpec
from yuubot.core.integrations.contracts import IntegrationFactory


@dataclass
class IntegrationFactoryRegistry:
    """Code registry for integration factory implementations."""

    _factories: dict[str, IntegrationFactory] = field(default_factory=dict)

    def register(self, factory: IntegrationFactory) -> None:
        self._factories[factory.plugin_id] = factory

    def capability_specs(self) -> list[AnyCapabilitySpec]:
        return list(_unique_capabilities(self._all_capabilities()))

    def get(self, plugin_id: str) -> IntegrationFactory:
        try:
            return self._factories[plugin_id]
        except KeyError as exc:
            raise LookupError(
                f"integration factory {plugin_id!r} is not registered"
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
