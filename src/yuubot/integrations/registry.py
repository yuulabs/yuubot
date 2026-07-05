"""Integration registry: type name -> spec (facade package, config schema, factory)."""

from collections.abc import Callable
from typing import Protocol

import msgspec
from attrs import frozen

from ..runtime.inbound import DEFAULT_INBOUND_ADAPTER, IntegrationInboundAdapter
from .github import GitHubConfig, make_github
from .records import IntegrationRecord
from .tavily_web import TavilyWebConfig, make_tavily_web


class Integration(Protocol):
    """A platform/service connection enabled in the runtime.

    ``session_context`` returns the environment injected into execute_python
    sessions; the yext facade for the integration reads it from ``os.environ``.
    """

    @property
    def name(self) -> str: ...

    @property
    def package_path(self) -> str: ...

    def session_context(self) -> dict[str, str]: ...

    async def close(self) -> None: ...


IntegrationFactory = Callable[[str, msgspec.Struct, object], Integration]


@frozen
class IntegrationSpec:
    package_path: str
    config_type: type[msgspec.Struct]
    factory: IntegrationFactory
    inbound_adapter: IntegrationInboundAdapter = DEFAULT_INBOUND_ADAPTER


@frozen
class IntegrationRegistry:
    _items: dict[str, IntegrationSpec]

    def register(self, integration_type: str, spec: IntegrationSpec) -> None:
        self._items[integration_type] = spec

    def config_schema(self, integration_type: str) -> type[msgspec.Struct]:
        return self._items[integration_type].config_type

    def specs(self) -> dict[str, IntegrationSpec]:
        return dict(self._items)

    def create(self, record: IntegrationRecord, runtime: object) -> Integration:
        spec = self._items[record.type]
        config = msgspec.convert(record.config, spec.config_type)
        return spec.factory(record.name, config, runtime)

    def inbound_adapter(self, integration_type: str) -> IntegrationInboundAdapter:
        return self._items[integration_type].inbound_adapter


def default_registry() -> IntegrationRegistry:
    registry = IntegrationRegistry({})
    registry.register(
        "tavily_web",
        IntegrationSpec(package_path="yext.web", config_type=TavilyWebConfig, factory=make_tavily_web),
    )
    registry.register(
        "github",
        IntegrationSpec(package_path="yext.github", config_type=GitHubConfig, factory=make_github),
    )
    return registry
