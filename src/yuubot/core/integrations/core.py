"""Integration lifecycle and invocation core."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import msgspec

from yuubot.core.cache import Cached
from yuubot.core.capabilities import (
    AnyCapability,
    AnyCapabilitySpec,
    Capability,
    CapabilitySpec,
)
from yuubot.core.integrations.context import InvocationContext, bind_invocation_context
from yuubot.core.integrations.contracts import (
    IntegrationFactory,
    IntegrationInstance,
)
from yuubot.core.integrations.registry import IntegrationFactoryRegistry
from yuubot.resources.events import ResourceChanged
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.store.models import ActorORM, IntegrationORM

if TYPE_CHECKING:
    from yuubot.core.gateway import Gateway


@dataclass
class IntegrationCore:
    """Resolves capabilities, invokes integrations, and manages lifecycle."""

    repository: ResourceRepository
    factories: IntegrationFactoryRegistry
    gateway: Gateway | None = None
    _instances: dict[str, IntegrationInstance] = field(default_factory=dict, init=False)
    _capabilities_index: dict[tuple[str, str], AnyCapability] = field(
        default_factory=dict,
        init=False,
    )
    _enabled_capability_ids: Cached[set[str]] = field(init=False)
    _actor_allowed: dict[str, Cached[set[str]]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._enabled_capability_ids = Cached(loader=self._resolve_enabled_capability_ids)

    async def refresh_capabilities(self) -> None:
        self._enabled_capability_ids.invalidate()

    def declared_capability_specs(self) -> tuple[AnyCapabilitySpec, ...]:
        return self.factories.capability_specs()

    async def handle_resource_changed(self, event: ResourceChanged) -> None:
        if event.is_table("integrations"):
            self._enabled_capability_ids.invalidate()
            await self.reconcile(event)
        if event.is_table("actors"):
            for actor_id in event.row_ids:
                if actor_id in self._actor_allowed:
                    self._actor_allowed[actor_id].invalidate()

    async def invoke(
        self,
        *,
        actor_id: str,
        capability_id: str,
        payload: dict[str, object],
        context: InvocationContext | None = None,
        usage: object | None = None,
    ) -> msgspec.Struct:
        allowed = await self._get_actor_allowed(actor_id)
        if capability_id not in allowed:
            raise LookupError(f"actor {actor_id} is not allowed to use {capability_id!r}")

        capability = self._find_capability(capability_id)
        integration_id = self._integration_id_for(capability_id)
        typed_payload = capability.decode_input(payload)

        context = bind_invocation_context(
            context,
            actor_id=actor_id,
            integration_id=integration_id,
            capability_id=capability_id,
            usage=usage,
        )
        return await capability.invoke(typed_payload, context)

    async def enable(self, integration_id: str) -> None:
        if integration_id in self._instances:
            return
        record = await self.repository.get(IntegrationORM, integration_id)
        if record is None:
            raise LookupError(f"integration {integration_id} does not exist")
        if not record.enabled:
            raise ValueError(f"integration {integration_id} is disabled")
        if self.gateway is None:
            raise RuntimeError("gateway not injected")

        factory = self.factories.get(record.plugin_id)
        instance = await factory.create(record, self.repository, gateway=self.gateway)
        try:
            capabilities = _index_capabilities(integration_id, factory, instance)
        except Exception:
            with suppress(Exception):
                await instance.close()
            raise
        self._instances[integration_id] = instance
        self._capabilities_index.update(capabilities)

    async def disable(self, integration_id: str) -> None:
        instance = self._instances.pop(integration_id, None)
        for key in tuple(self._capabilities_index):
            if key[0] == integration_id:
                self._capabilities_index.pop(key, None)
        if instance is not None:
            await instance.close()

    async def enable_all(self) -> None:
        records = await self.repository.list(IntegrationORM)
        for record in records:
            if record.enabled:
                await self.enable(record.id)

    async def disable_all(self) -> None:
        for integration_id in list(self._instances):
            await self.disable(integration_id)

    async def reconcile(self, event: ResourceChanged | None = None) -> None:
        records = await self.repository.list(IntegrationORM)
        enabled_ids = {record.id for record in records if record.enabled}

        await self._disable_removed_or_disabled_integrations(enabled_ids)
        await self._refresh_enabled_integrations(event, enabled_ids)
        self._enabled_capability_ids.invalidate()

    def running_integration_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._instances))

    def running_instance(self, integration_id: str) -> IntegrationInstance:
        try:
            return self._instances[integration_id]
        except KeyError as exc:
            raise LookupError(f"integration {integration_id!r} is not running") from exc

    def remove_actor_cache(self, actor_id: str) -> None:
        self._actor_allowed.pop(actor_id, None)

    def _find_capability(self, capability_id: str) -> AnyCapability:
        for (_, cap_id), capability in self._capabilities_index.items():
            if cap_id == capability_id:
                return capability
        raise LookupError(f"capability {capability_id!r} is not provided by any integration")

    def _integration_id_for(self, capability_id: str) -> str:
        for (integration_id, cap_id) in self._capabilities_index:
            if cap_id == capability_id:
                return integration_id
        raise LookupError(f"capability {capability_id!r} is not provided by any integration")

    async def _get_actor_allowed(self, actor_id: str) -> set[str]:
        cache = self._actor_allowed.get(actor_id)
        if cache is None:
            cache = Cached(loader=lambda aid=actor_id: self._load_actor_allowed(aid))
            self._actor_allowed[actor_id] = cache
        return await cache.get()

    async def _load_actor_allowed(self, actor_id: str) -> set[str]:
        actor = await self.repository.get(ActorORM, actor_id)
        if actor is None or not actor.enabled:
            return set()
        return set(actor.allowed_capability_ids)

    async def _disable_removed_or_disabled_integrations(
        self,
        enabled_ids: set[str],
    ) -> None:
        for integration_id in tuple(self._instances):
            if integration_id not in enabled_ids:
                await self.disable(integration_id)

    async def _refresh_enabled_integrations(
        self,
        event: ResourceChanged | None,
        enabled_ids: set[str],
    ) -> None:
        for integration_id in sorted(_integration_refresh_ids(event, enabled_ids)):
            if integration_id in self._instances and event is not None:
                await self.disable(integration_id)
            await self.enable(integration_id)

    async def _resolve_enabled_capability_ids(self) -> set[str]:
        result: set[str] = set()
        records = await self.repository.list(IntegrationORM)
        for record in records:
            if not record.enabled:
                continue
            try:
                factory = self.factories.get(record.plugin_id)
            except LookupError:
                continue
            for spec in factory.capability_specs():
                result.add(spec.id)
        return result


def _integration_refresh_ids(
    event: ResourceChanged | None,
    enabled_ids: set[str],
) -> set[str]:
    if event is None or not event.is_table("integrations"):
        return enabled_ids
    return enabled_ids.intersection(event.row_ids)


def _index_capabilities(
    integration_id: str,
    factory: IntegrationFactory,
    instance: IntegrationInstance,
) -> dict[tuple[str, str], AnyCapability]:
    declared = {spec.id: spec for spec in factory.capability_specs()}
    capabilities: dict[tuple[str, str], AnyCapability] = {}
    capability_ids: set[str] = set()
    for capability in instance.capabilities():
        declared_spec = declared.get(capability.id)
        if declared_spec is None:
            raise RuntimeError(
                f"integration {integration_id!r} capability {capability.id!r} "
                "was not declared by its factory"
            )
        _ensure_capability_shape(capability, declared_spec)
        key = (integration_id, capability.id)
        capabilities[key] = capability
        capability_ids.add(capability.id)

    missing = set(declared).difference(capability_ids)
    if missing:
        formatted = ", ".join(sorted(missing))
        raise RuntimeError(
            f"integration {integration_id!r} is missing capabilities for: {formatted}"
        )
    return capabilities


def _ensure_capability_shape(
    capability: Capability[Any, Any],
    expected: CapabilitySpec[Any, Any],
) -> None:
    if (
        capability.id != expected.id
        or capability.input_type is not expected.input_type
        or capability.output_type is not expected.output_type
    ):
        raise TypeError(f"capability {expected.id!r} schema does not match declaration")
