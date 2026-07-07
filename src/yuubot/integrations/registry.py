"""Integration registry: type name -> spec (facade package, config schema, factory)."""

from collections.abc import Callable
from typing import Protocol

import msgspec
from attrs import frozen

from ..runtime.inbound import DEFAULT_INBOUND_ADAPTER, IntegrationInboundAdapter
from .coding_cli import CodexConfig, OpenCodeConfig, make_codex, make_opencode
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


class IntegrationHealth(msgspec.Struct, frozen=True, kw_only=True):
    status: str
    reason: str = ""
    details: dict[str, object] = msgspec.field(default_factory=dict)
    action_hint: dict[str, object] | None = None


class HealthCheckedIntegration(Protocol):
    async def health_check(self) -> object: ...


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

    def default_config(self, integration_type: str) -> dict[str, object] | None:
        config_type = self._items[integration_type].config_type
        try:
            config = config_type()
        except TypeError:
            return None
        payload = msgspec.to_builtins(config)
        return payload if isinstance(payload, dict) else {}

    def inbound_adapter(self, integration_type: str) -> IntegrationInboundAdapter:
        return self._items[integration_type].inbound_adapter


async def integration_health(integration: Integration) -> IntegrationHealth | None:
    health_check = getattr(integration, "health_check", None)
    if not callable(health_check):
        return None
    result = await health_check()
    return msgspec.convert(result, IntegrationHealth)


def default_registry() -> IntegrationRegistry:
    registry = IntegrationRegistry({})
    registry.register(
        "codex",
        IntegrationSpec(package_path="yext.codex", config_type=CodexConfig, factory=make_codex),
    )
    registry.register(
        "opencode",
        IntegrationSpec(package_path="yext.opencode", config_type=OpenCodeConfig, factory=make_opencode),
    )
    registry.register(
        "tavily_web",
        IntegrationSpec(package_path="yext.web", config_type=TavilyWebConfig, factory=make_tavily_web),
    )
    registry.register(
        "github",
        IntegrationSpec(package_path="yext.github", config_type=GitHubConfig, factory=make_github),
    )
    return registry
