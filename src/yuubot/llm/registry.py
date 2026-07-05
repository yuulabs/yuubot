"""Protocol registry: protocol name -> config schema + provider factory."""

from collections.abc import Callable
from typing import Protocol, cast

import msgspec
from attrs import frozen

from ..util.secrets import merge_redacted_config, redact_config as redact_secret_config

from ..domain.messages import ModelCard
from .protocol import Provider
from .records import ProviderRecord
from .types import ProviderProtocolSpec


class ProviderFactory(Protocol):
    def __call__(self, record: ProviderRecord, config: msgspec.Struct) -> Provider: ...


ProviderBuilder = Callable[[ProviderRecord, msgspec.Struct], Provider]


@frozen
class ProviderSpec:
    title: str
    config_type: type[msgspec.Struct]
    factory: ProviderBuilder
    default_endpoint: str
    secret_fields: tuple[str, ...] = ("api_key",)


@frozen
class ProviderRegistry:
    _items: dict[str, ProviderSpec]

    def register(self, protocol: str, spec: ProviderSpec) -> None:
        self._items[protocol] = spec

    def specs(self) -> dict[str, ProviderSpec]:
        return dict(self._items)

    def protocol_specs(self) -> list[ProviderProtocolSpec]:
        return [
            ProviderProtocolSpec(
                protocol=protocol,
                title=spec.title,
                default_endpoint=spec.default_endpoint,
                config_schema=msgspec.json.schema(spec.config_type),
                secret_fields=spec.secret_fields,
            )
            for protocol, spec in sorted(self._items.items())
        ]

    def config_type(self, protocol: str) -> type[msgspec.Struct]:
        return self._items[protocol].config_type

    def secret_fields(self, protocol: str) -> tuple[str, ...]:
        return self._items[protocol].secret_fields

    def build(self, record: ProviderRecord) -> Provider:
        spec = self._items[record.protocol]
        config = msgspec.convert(record.config, spec.config_type)
        return spec.factory(record, config)

    def decode_config(self, protocol: str, config: dict[str, object]) -> msgspec.Struct:
        return msgspec.convert(config, self.config_type(protocol))

    def redact_config(self, protocol: str, config: dict[str, object]) -> dict[str, object]:
        return redact_secret_config(config, secret_fields=frozenset(self.secret_fields(protocol)))

    def merge_config(
        self,
        protocol: str,
        incoming: dict[str, object],
        stored: dict[str, object] | None,
    ) -> dict[str, object]:
        return merge_redacted_config(
            incoming,
            stored,
            secret_fields=frozenset(self.secret_fields(protocol)),
        )


def default_registry() -> ProviderRegistry:
    from .openai import OpenAIProviderConfig, make_openai_provider

    registry = ProviderRegistry({})
    registry.register(
        "openai-compatible",
        ProviderSpec(
            title="OpenAI-compatible",
            config_type=OpenAIProviderConfig,
            factory=cast(ProviderBuilder, make_openai_provider),
            default_endpoint="https://api.openai.com/v1",
            secret_fields=("api_key",),
        ),
    )
    return registry
