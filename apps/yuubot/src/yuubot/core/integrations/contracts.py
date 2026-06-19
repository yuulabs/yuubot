"""Integration extension contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import msgspec

from yuubot.core.capabilities import AnyCapability, AnyCapabilitySpec
from yuubot.core.secrets import secret_schema_hook
from yuubot.resources.records import IntegrationRecord

if TYPE_CHECKING:
    from starlette.routing import Route

    from yuubot.core.gateway import Gateway
    from yuubot.core.integrations.core import IntegrationCore


ReactionKind = str
"""Fast acknowledgement signals for `IntegrationInstance.response`.

Agents can emit platform-specific reaction strings through the integration SDK.
Integrations that cannot represent a reaction should silently ignore it.
"""


@dataclass
class IntegrationKindInfo:
    """Static metadata about a registered integration kind.

    The admin UI uses this to render a create/edit form for each integration
    kind (name, description, JSON Schema for the `config` field, and the list
    of capabilities the kind exposes).

    ``source_path_convention`` is a human-readable description of how this
    integration kind constructs the ``source.path`` on inbound messages.
    Integration developers document their path naming scheme here so that
    users can write correct ``source_path_pattern`` globs in Ingress Rules.
    """

    name: str
    description: str = ""
    config_schema: dict[str, object] = field(default_factory=dict)
    capabilities: tuple[AnyCapabilitySpec, ...] = ()
    source_path_convention: str = ""


class IntegrationFactory(Protocol):
    """Registered once at startup; creates integration instances from records."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def config_schema(self) -> type[msgspec.Struct] | dict[str, object]: ...

    @property
    def source_path_convention(self) -> str:
        """Human-readable description of how this kind constructs source.path.

        Describe the path naming scheme so users can write correct glob
        patterns in Ingress Rules.  Return ``""`` if the integration does
        not produce inbound messages or if the path is purely external.
        """
        return ""

    def capability_specs(self) -> list[AnyCapabilitySpec]: ...

    async def create(
        self,
        record: IntegrationRecord,
        *,
        gateway: Gateway,
        storage: IntegrationStorage,
    ) -> IntegrationInstance: ...

    def routes(self, integrations: IntegrationCore) -> list[Route]: ...


class IntegrationStorage(Protocol):
    @property
    def data_dir(self) -> Path: ...


@dataclass
class LocalIntegrationStorage:
    data_dir: Path


class IntegrationInstance(Protocol):
    def capabilities(self) -> list[AnyCapability]: ...

    async def response(
        self,
        target_msg_id: str,
        *,
        path: str = "",
        msg: str = "",
        react: ReactionKind | None = None,
    ) -> None:
        """Reply or react to a previously received inbound message.

        ``path`` is the target channel path (the actor passes
        ``message.source.path`` directly so the integration knows
        where to route the response without a message-id lookup).

        ``msg`` is human-visible text (typically an error message); ``react``
        is a fast acknowledgement signal (e.g. an emoji reaction). Platforms
        that cannot react must silently ignore that argument.

        Implementations should not raise on missing target ids — the actor
        loop calls this opportunistically and tolerates failures.
        """

    async def close(self) -> None: ...


def integration_kind_info(factory: IntegrationFactory) -> IntegrationKindInfo:
    """Project a factory to its admin-facing kind descriptor."""
    config_type = factory.config_schema
    schema: dict[str, object] = {}
    if isinstance(config_type, type) and issubclass(config_type, msgspec.Struct):
        schema = _inline_root_schema(
            msgspec.json.schema(config_type, schema_hook=secret_schema_hook)
        )
    elif isinstance(config_type, dict):
        schema = dict(config_type)
    return IntegrationKindInfo(
        name=factory.name,
        description=factory.description,
        config_schema=schema,
        capabilities=tuple(factory.capability_specs()),
        source_path_convention=factory.source_path_convention,
    )


def _inline_root_schema(schema: dict[str, object]) -> dict[str, object]:
    """Inline the top-level $ref so forms see a self-contained object schema.

    `msgspec.json.schema()` returns `{"$ref": "#/$defs/X", "$defs": {...}}`
    even for a single-struct type. For form rendering we prefer the
    referenced definition directly, carrying over remaining `$defs` only
    when they are still needed by nested types.
    """
    ref = schema.get("$ref")
    defs = schema.get("$defs")
    if not isinstance(ref, str) or not isinstance(defs, dict):
        return schema
    prefix = "#/$defs/"
    if not ref.startswith(prefix):
        return schema
    root_name = ref[len(prefix) :]
    defs_map = cast(dict[str, object], defs)
    root = defs_map.get(root_name)
    if not isinstance(root, dict):
        return schema
    inlined = dict(cast(dict[str, object], root))
    remaining = {k: v for k, v in defs_map.items() if k != root_name}
    if remaining:
        inlined["$defs"] = remaining
    return inlined
