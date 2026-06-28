"""Integration lifecycle and invocation core."""

from __future__ import annotations

import asyncio
import logging
import shutil
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
    IntegrationCapabilityRef,
    IntegrationFactory,
    IntegrationInstance,
    LocalIntegrationStorage,
    VisibleIntegrationSurface,
)
from yuubot.core.integrations.registry import IntegrationFactoryRegistry
from yuubot.resources.events import ResourceChanged
from yuubot.resources.orm import from_orm
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.records import ConversationRecord, IntegrationRecord
from yuubot.resources.builtin_presets import ALL_INTEGRATIONS_SENTINEL
from yuubot.resources.store.models import (
    ActorORM,
    CapabilitySetORM,
    ConversationORM,
    IntegrationORM,
)

if TYPE_CHECKING:
    from yuubot.core.actors.workspace import ActorWorkspaceResolver
    from yuubot.core.gateway import Gateway

logger = logging.getLogger(__name__)


@dataclass
class CapabilityInstanceInfo:
    """A capability from an existing integration record (enabled or disabled)."""

    capability_id: str
    capability_name: str
    description: str
    namespace: str
    integration_id: str
    integration_name: str
    enabled: bool


@dataclass
class IntegrationCore:
    """Resolves capabilities, invokes integrations, and manages lifecycle."""

    repository: ResourceRepository
    factories: IntegrationFactoryRegistry
    gateway: Gateway | None = None
    integrations_root: Path = field(
        default_factory=lambda: Path("~/.yuubot/integrations")
    )
    workspace_resolver: ActorWorkspaceResolver | None = None
    _instances: dict[str, IntegrationInstance] = field(default_factory=dict, init=False)
    # Records (carrying ``name``) of the running instances, kept in lockstep
    # with ``_instances`` so ``visible_integration_surfaces`` can resolve the
    # integration kind / factory + record-derived ``integration_name`` without
    # a repository round-trip (it runs synchronously at facade-bind time).
    _instance_records: dict[str, IntegrationRecord] = field(
        default_factory=dict, init=False
    )
    _capabilities_index: dict[tuple[str, str], AnyCapability] = field(
        default_factory=dict,
        init=False,
    )
    _owner_allowed: dict[str, Cached[set[IntegrationCapabilityRef]]] = field(
        default_factory=dict, init=False
    )
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def declared_capability_specs(self) -> list[AnyCapabilitySpec]:
        return self.factories.capability_specs()

    async def existing_instance_capabilities(self) -> list[CapabilityInstanceInfo]:
        """Return capabilities from ALL integration records (enabled + disabled).

        This includes disabled instances so the admin form can show them with
        a disabled badge.
        """
        records = await self.repository.list(IntegrationORM)
        result: list[CapabilityInstanceInfo] = []
        for record in records:
            try:
                factory = self.factories.get(record.name)
            except LookupError:
                continue  # factory not registered (e.g., plugin removed)
            for spec in factory.capability_specs():
                result.append(
                    CapabilityInstanceInfo(
                        capability_id=spec.id,
                        capability_name=spec.name,
                        description=spec.description,
                        namespace=spec.namespace,
                        integration_id=record.id,
                        integration_name=record.name,
                        enabled=record.enabled,
                    )
                )
        return result

    async def handle_resource_changed(self, event: ResourceChanged) -> None:
        if event.is_table("integrations"):
            await self.reconcile(event)
        if event.is_table("actors"):
            for actor_id in event.row_ids:
                if actor_id in self._owner_allowed:
                    self._owner_allowed[actor_id].invalidate()
        if event.is_table("conversations") or event.is_table("capability_sets"):
            self._owner_allowed.clear()

    async def invoke(
        self,
        *,
        actor_id: str,
        capability_id: str,
        payload: dict[str, object],
        integration_id: str = "",
        context: InvocationContext | None = None,
        usage: object | None = None,
    ) -> msgspec.Struct:
        allowed = await self._get_owner_allowed(actor_id)
        capability_ref = _resolve_requested_capability_ref(
            allowed,
            integration_id=integration_id,
            capability_id=capability_id,
        )
        if capability_ref not in allowed:
            raise LookupError(
                f"actor {actor_id} is not allowed to use "
                f"{_format_capability_ref(capability_ref)}"
            )

        capability = self._find_capability(capability_ref)
        typed_payload = capability.decode_input(payload)

        context = bind_invocation_context(
            context,
            actor_id=actor_id,
            integration_id=integration_id,
            capability_id=capability_id,
            usage=usage,
        )
        if self.workspace_resolver is not None and not context.workspace_path:
            context = InvocationContext(
                actor_id=context.actor_id,
                workspace_path=str(self.workspace_resolver.resolve(actor_id)),
                source_id=context.source_id,
                source_path=context.source_path,
                integration_id=context.integration_id,
                capability_id=context.capability_id,
                usage=context.usage,
                raw=context.raw,
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
            await instance.close()
            raise
        self._instances[integration_id] = instance
        self._instance_records[integration_id] = record
        self._capabilities_index.update(capabilities)

    async def disable(self, integration_id: str) -> None:
        async with self._lock:
            await self._disable_locked(integration_id)

    async def _disable_locked(self, integration_id: str) -> None:
        instance = self._instances.pop(integration_id, None)
        self._instance_records.pop(integration_id, None)
        for key in list(self._capabilities_index):
            if key[0] == integration_id:
                self._capabilities_index.pop(key, None)
        if instance is not None:
            await instance.close()

    async def enable_all(self) -> None:
        async with self._lock:
            records = await self.repository.list(IntegrationORM)
            for record in records:
                if record.enabled:
                    await self._try_enable_locked(record.id)

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

    def running_integration_ids(self) -> list[str]:
        return sorted(self._instances)

    def running_instance(self, integration_id: str) -> IntegrationInstance:
        try:
            return self._instances[integration_id]
        except KeyError as exc:
            raise LookupError(f"integration {integration_id!r} is not running") from exc

    def visible_integration_surfaces(
        self,
        integration_ids: tuple[str, ...],
    ) -> list[VisibleIntegrationSurface]:
        """Read-models for selected + running integrations (§2.7.1).

        Derivation rule: ``selected integration_ids ∩ enabled
        IntegrationRecord ∩ running IntegrationInstance``. Each selected id
        that is not currently running (or whose factory/record was removed)
        simply contributes nothing — invoke-time authorisation already
        enforces the same boundary, so an absent surface never corrupts the
        facade: the actor sees neither its SDK imports nor its prompt.

        Runs synchronously against the in-memory ``_instances`` /
        ``_instance_records`` caches populated by ``_enable_locked``. Order
        follows ``integration_ids`` (the CapabilitySet's declared selection
        order) for stable prompt rendering.
        """
        selected_ids = self.running_integration_ids() if _selects_all(
            integration_ids
        ) else list(integration_ids)
        surfaces: list[VisibleIntegrationSurface] = []
        for integration_id in selected_ids:
            if integration_id not in self._instances:
                continue
            record = self._instance_records.get(integration_id)
            if record is None:
                continue
            try:
                factory = self.factories.get(record.name)
            except LookupError:
                # Factory unregistered after the instance started (e.g. plugin
                # removed) — surface nothing; reconcile() will tear it down.
                continue
            specs = tuple(factory.capability_specs())
            surfaces.append(
                VisibleIntegrationSurface(
                    integration_id=integration_id,
                    integration_name=record.name,
                    sdk=factory.sdk_spec,
                    capabilities=specs,
                    capability_refs=tuple(
                        IntegrationCapabilityRef(
                            integration_id=integration_id,
                            capability_id=spec.id,
                        )
                        for spec in specs
                    ),
                )
            )
        return surfaces

    def remove_actor_cache(self, actor_id: str) -> None:
        self._owner_allowed.pop(actor_id, None)

    def _find_capability(
        self,
        capability_ref: IntegrationCapabilityRef,
    ) -> AnyCapability:
        capability = self._capabilities_index.get(
            (capability_ref.integration_id, capability_ref.capability_id)
        )
        if capability is None:
            raise LookupError(
                f"capability {_format_capability_ref(capability_ref)} "
                "is not provided by any running integration"
            )
        return capability

    async def _get_owner_allowed(self, owner_id: str) -> set[IntegrationCapabilityRef]:
        cache = self._owner_allowed.get(owner_id)
        if cache is None:
            cache = Cached(loader=lambda oid=owner_id: self._load_owner_allowed(oid))
            self._owner_allowed[owner_id] = cache
        return await cache.get()

    async def _load_owner_allowed(self, owner_id: str) -> set[IntegrationCapabilityRef]:
        """Resolve the integration capability refs an owner may invoke.

        CapabilitySets now declare selected integrations by
        ``integration_ids`` (``IntegrationRecord.id``), not capability ids.
        The owner is authorised for every capability of each selected
        integration that has an existing record with a registered factory
        (matching the previous behaviour, which authorised the stored
        capability ids directly). Whether the integration is actually
        running is enforced at invoke time by ``_find_capability``.
        """
        capability_set = await self._capability_set_for_owner(owner_id)
        if capability_set is None:
            return set()
        selected = set(capability_set.integration_ids)
        if not selected:
            return set()
        allows_all = ALL_INTEGRATIONS_SENTINEL in selected
        result: set[IntegrationCapabilityRef] = set()
        records = await self.repository.list(IntegrationORM)
        for record in records:
            if not allows_all and record.id not in selected:
                continue
            try:
                factory = self.factories.get(record.name)
            except LookupError:
                continue
            for spec in factory.capability_specs():
                result.add(
                    IntegrationCapabilityRef(
                        integration_id=record.id,
                        capability_id=spec.id,
                    )
                )
        return result

    async def _capability_set_for_owner(self, owner_id: str):
        """Resolve the CapabilitySetRecord backing an actor owner_id.

        The owner is either an actor id (direct) or a conversation id
        (indirected through the conversation's actor). Returns ``None`` when
        no enabled actor + capability set is reachable.
        """
        actor = await self.repository.get(ActorORM, owner_id)
        if actor is not None and actor.enabled:
            return await self.repository.get(
                CapabilitySetORM,
                actor.capability_set_id,
            )
        with self.repository.store.db.activate():
            row = await ConversationORM.get_or_none(
                conversation_id=owner_id
            )
            if row is None:
                return None
            conversation = await from_orm(
                row,
                ConversationRecord,
                secret_codec=self.repository.secret_codec,
            )
        actor = await self.repository.get(ActorORM, conversation.actor_id)
        if actor is None or not actor.enabled:
            return None
        return await self.repository.get(
            CapabilitySetORM,
            actor.capability_set_id,
        )

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
            await self._try_enable_locked(integration_id)

    async def _try_enable_locked(self, integration_id: str) -> None:
        try:
            await self._enable_locked(integration_id)
        except Exception:
            logger.exception("failed to enable integration %s", integration_id)

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


def _selects_all(integration_ids: tuple[str, ...]) -> bool:
    return ALL_INTEGRATIONS_SENTINEL in integration_ids


def _resolve_requested_capability_ref(
    allowed_refs: set[IntegrationCapabilityRef],
    *,
    integration_id: str,
    capability_id: str,
) -> IntegrationCapabilityRef:
    if integration_id:
        return IntegrationCapabilityRef(
            integration_id=integration_id,
            capability_id=capability_id,
        )
    matches = sorted(
        (
            ref for ref in allowed_refs
            if ref.capability_id == capability_id
        ),
        key=lambda ref: ref.integration_id,
    )
    if not matches:
        return IntegrationCapabilityRef(
            integration_id="",
            capability_id=capability_id,
        )
    if len(matches) == 1:
        return matches[0]
    choices = ", ".join(_format_capability_ref(ref) for ref in matches)
    raise LookupError(
        f"capability {capability_id!r} is ambiguous across integrations: {choices}"
    )


def _format_capability_ref(ref: IntegrationCapabilityRef) -> str:
    if not ref.integration_id:
        return ref.capability_id
    return f"{ref.integration_id}:{ref.capability_id}"


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
