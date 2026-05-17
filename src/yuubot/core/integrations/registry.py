"""Integration factory registry."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from yuubot.core.capabilities import AnyCapabilitySpec
from yuubot.core.integrations.contracts import (
    IntegrationFactory,
    IntegrationKindInfo,
    integration_kind_info,
)


class IntegrationFactoryLoader(Protocol):
    def load(self, name: str) -> IntegrationFactory | None: ...

    def all_factories(self) -> Iterable[IntegrationFactory]: ...


@dataclass
class IntegrationFactoryRegistry:
    """Code registry for integration factory implementations."""

    _factories: dict[str, IntegrationFactory] = field(default_factory=dict)
    _loaders: list[IntegrationFactoryLoader] = field(default_factory=list)

    def register(self, factory: IntegrationFactory) -> None:
        self._factories[factory.name] = factory

    def register_loader(
        self,
        loader: IntegrationFactoryLoader,
    ) -> None:
        self._loaders.append(loader)

    def capability_specs(self) -> list[AnyCapabilitySpec]:
        return list(_unique_capabilities(self._all_capabilities()))

    def integration_kinds(self) -> list[IntegrationKindInfo]:
        """Return static admin-facing descriptors for every registered kind.

        The admin UI renders create/edit forms from this list. Ordering
        follows registration order so the UI can display a stable catalog.
        """
        return [
            integration_kind_info(factory)
            for factory in self._all_factories()
        ]

    def get(self, name: str) -> IntegrationFactory:
        factory = self._factories.get(name)
        if factory is not None:
            return factory
        factory = self._load_factory(name)
        if factory is not None:
            return factory
        raise LookupError(f"integration factory {name!r} is not registered")

    def _all_capabilities(self) -> Iterable[AnyCapabilitySpec]:
        for factory in self._all_factories():
            yield from factory.capability_specs()

    def _all_factories(self) -> Iterable[IntegrationFactory]:
        loaded_names = set(self._factories)
        yield from self._factories.values()
        for loader in self._loaders:
            for factory in loader.all_factories():
                if factory.name in loaded_names:
                    continue
                loaded_names.add(factory.name)
                yield factory

    def _load_factory(self, name: str) -> IntegrationFactory | None:
        for loader in self._loaders:
            factory = loader.load(name)
            if factory is None:
                continue
            self.register(factory)
            return factory
        return None


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
