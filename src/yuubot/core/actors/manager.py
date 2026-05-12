"""Actor lifecycle manager."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from yuubot.core.actors.contracts import Actor
from yuubot.core.actors.events import ActorLifecycleCommand, StartActor, StopActor
from yuubot.core.actors.registry import ActorFactoryRegistry
from yuubot.core.actors.workspace import ActorWorkspaceResolver
from yuubot.core.bindings import ActorBinding, load_actor_binding
from yuubot.core.gateway import Gateway
from yuubot.resources.events import ResourceChanged
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.store.models import ActorORM


@dataclass
class ActorManager:
    """Owns running Actor instances and nothing below the Actor boundary."""

    repository: ResourceRepository
    factories: ActorFactoryRegistry
    gateway: Gateway
    workspace_resolver: ActorWorkspaceResolver
    _actors: dict[str, Actor] = field(default_factory=dict, init=False)
    _actor_workspaces: dict[str, Path] = field(default_factory=dict, init=False)

    async def start_actor(self, actor_id: str) -> Actor:
        if actor_id in self._actors:
            return self._actors[actor_id]
        workspace_path = self.workspace_resolver.resolve(actor_id)
        binding = await load_actor_binding(
            self.repository,
            actor_id,
            workspace_path=workspace_path,
        )
        try:
            actor = await self._create_actor(binding)
            await actor.start()
        except Exception:
            self.gateway.close_mailbox(actor_id)
            raise
        self._actors[actor_id] = actor
        self._actor_workspaces[actor_id] = workspace_path
        return actor

    async def stop_actor(self, actor_id: str) -> None:
        actor = self._actors.pop(actor_id, None)
        self._actor_workspaces.pop(actor_id, None)
        if actor is not None:
            await actor.stop()
        self.gateway.close_mailbox(actor_id)

    async def stop_all(self) -> None:
        for actor_id in list(self._actors):
            await self.stop_actor(actor_id)

    async def reconcile(self) -> None:
        desired_actor_ids = await self._load_desired_actor_ids()
        await self._stop_undesired_actors(desired_actor_ids)
        await self._start_missing_actors(desired_actor_ids)

    async def handle_lifecycle_command(
        self,
        command: ActorLifecycleCommand,
    ) -> None:
        if isinstance(command, StartActor):
            await self.start_actor(command.actor_id)
            return
        if isinstance(command, StopActor):
            await self.stop_actor(command.actor_id)

    async def forward_resource_change(self, event: ResourceChanged) -> None:
        for actor in self._actors.values():
            await actor.handle_resource_changed(event)

    def running_actor(self, actor_id: str) -> Actor | None:
        return self._actors.get(actor_id)

    def running_actor_ids(self) -> list[str]:
        return sorted(self._actors)

    def running_actor_workspace_paths(self) -> dict[str, str]:
        return {
            actor_id: str(self._actor_workspaces[actor_id])
            for actor_id in sorted(self._actor_workspaces)
        }

    async def _create_actor(self, binding: ActorBinding) -> Actor:
        mailbox = self.gateway.get_mailbox(binding.actor.id)
        return await self.factories.get(binding.actor.type).create(
            binding,
            mailbox,
        )

    async def _load_desired_actor_ids(self) -> list[str]:
        routed_actor_ids = set(self.gateway.routes.actor_ids())
        records = await self.repository.list(ActorORM)
        return [
            record.id
            for record in records
            if record.enabled and record.id in routed_actor_ids
        ]

    async def _stop_undesired_actors(self, desired_actor_ids: list[str]) -> None:
        desired = set(desired_actor_ids)
        for actor_id in list(self._actors):
            if actor_id not in desired:
                await self.stop_actor(actor_id)

    async def _start_missing_actors(self, desired_actor_ids: list[str]) -> None:
        for actor_id in desired_actor_ids:
            if actor_id not in self._actors:
                await self.start_actor(actor_id)
