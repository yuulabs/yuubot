"""Integration lifecycle and invocation core."""

from __future__ import annotations

import asyncio
import shutil
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
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
    LocalIntegrationStorage,
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
    integrations_root: Path = field(
        default_factory=lambda: Path("~/.yuubot/integrations")
    )
    _instances: dict[str, IntegrationInstance] = field(default_factory=dict, init=False)
    _capabilities_index: dict[tuple[str, str], AnyCapability] = field(
        default_factory=dict,
        init=False,
    )
    _capability_by_id: dict[str, AnyCapability] = field(default_factory=dict, init=False)
    _integration_by_capability: dict[str, str] = field(default_factory=dict, init=False)
    _enabled_capability_ids: Cached[set[str]] = field(init=False)
    _actor_allowed: dict[str, Cached[set[str]]] = field(default_factory=dict, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._enabled_capability_ids = Cached(loader=self._resolve_enabled_capability_ids)

    async def refresh_capabilities(self) -> None:
        self._enabled_capability_ids.invalidate()

    def declared_capability_specs(self) -> list[AnyCapabilitySpec]:
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
        async with self._lock:
            await self._enable_locked(integration_id)

    async def _enable_locked(self, integration_id: str) -> None:
        if integration_id in self._instances:
            return
        record = await self.repository.get(IntegrationORM, integration_id)
        if record is None:
            raise LookupError(f"integration {integration_id} does not exist")
        if not record.enabled:
            raise ValueError(f"integration {integration_id} is disabled")
        if self.gateway is None:
            raise RuntimeError("gateway not injected")

        factory = self.factories.get(record.name)
        storage = self._storage_for(integration_id)
        instance = await factory.create(
            record,
            gateway=self.gateway,
            storage=storage,
        )
        try:
            capabilities = _index_capabilities(integration_id, factory, instance)
        except Exception:
            with suppress(Exception):
                await instance.close()
            raise
        self._instances[integration_id] = instance
        self._capabilities_index.update(capabilities)
        for (intg_id, cap_id), capability in capabilities.items():
            self._capability_by_id[cap_id] = capability
            self._integration_by_capability[cap_id] = intg_id

    async def disable(self, integration_id: str) -> None:
        async with self._lock:
            await self._disable_locked(integration_id)

    async def _disable_locked(self, integration_id: str) -> None:
        instance = self._instances.pop(integration_id, None)
        for key in list(self._capabilities_index):
            if key[0] == integration_id:
                self._capabilities_index.pop(key, None)
                self._capability_by_id.pop(key[1], None)
                self._integration_by_capability.pop(key[1], None)
        if instance is not None:
            await instance.close()

    async def enable_all(self) -> None:
        async with self._lock:
            records = await self.repository.list(IntegrationORM)
            for record in records:
                if record.enabled:
                    await self._enable_locked(record.id)

    async def disable_all(self) -> None:
        async with self._lock:
            for integration_id in list(self._instances):
                await self._disable_locked(integration_id)

    async def reconcile(self, event: ResourceChanged | None = None) -> None:
        async with self._lock:
            records = await self.repository.list(IntegrationORM)
            enabled_ids = {record.id for record in records if record.enabled}

            await self._disable_removed_or_disabled_locked(enabled_ids)
            await self._refresh_enabled_locked(event, enabled_ids)
            if event is not None and event.is_table("integrations"):
                self._delete_removed_storage_locked(event)
            self._enabled_capability_ids.invalidate()

    def running_integration_ids(self) -> list[str]:
        return sorted(self._instances)

    def running_instance(self, integration_id: str) -> IntegrationInstance:
        try:
            return self._instances[integration_id]
        except KeyError as exc:
            raise LookupError(f"integration {integration_id!r} is not running") from exc

    def remove_actor_cache(self, actor_id: str) -> None:
        self._actor_allowed.pop(actor_id, None)

    def _find_capability(self, capability_id: str) -> AnyCapability:
        capability = self._capability_by_id.get(capability_id)
        if capability is None:
            raise LookupError(f"capability {capability_id!r} is not provided by any integration")
        return capability

    def _integration_id_for(self, capability_id: str) -> str:
        integration_id = self._integration_by_capability.get(capability_id)
        if integration_id is None:
            raise LookupError(f"capability {capability_id!r} is not provided by any integration")
        return integration_id

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

    async def _disable_removed_or_disabled_locked(
        self,
        enabled_ids: set[str],
    ) -> None:
        for integration_id in list(self._instances):
            if integration_id not in enabled_ids:
                await self._disable_locked(integration_id)

    async def _refresh_enabled_locked(
        self,
        event: ResourceChanged | None,
        enabled_ids: set[str],
    ) -> None:
        for integration_id in sorted(_integration_refresh_ids(event, enabled_ids)):
            if integration_id in self._instances and event is not None:
                await self._disable_locked(integration_id)
            await self._enable_locked(integration_id)

    async def _resolve_enabled_capability_ids(self) -> set[str]:
        result: set[str] = set()
        records = await self.repository.list(IntegrationORM)
        for record in records:
            if not record.enabled:
                continue
            try:
                factory = self.factories.get(record.name)
            except LookupError:
                continue
            for spec in factory.capability_specs():
                result.add(spec.id)
        return result

    def _storage_for(self, integration_id: str) -> LocalIntegrationStorage:
        data_dir = Path(self.integrations_root).expanduser() / integration_id
        data_dir.mkdir(parents=True, exist_ok=True)
        return LocalIntegrationStorage(data_dir=data_dir)

    def _delete_removed_storage_locked(self, event: ResourceChanged) -> None:
        if event.action != "deleted":
            return
        for integration_id in event.row_ids:
            data_dir = Path(self.integrations_root).expanduser() / integration_id
            shutil.rmtree(data_dir, ignore_errors=True)


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
