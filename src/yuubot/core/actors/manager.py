"""Actor lifecycle manager."""

from __future__ import annotations

import asyncio

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


@dataclass
class ActorManager:
    """Owns running Actor instances and nothing below the Actor boundary."""

    repository: ResourceRepository
    factories: ActorFactoryRegistry
    gateway: Gateway
    workspace_resolver: ActorWorkspaceResolver
    _actors: dict[str, Actor] = field(default_factory=dict, init=False)
    _actor_workspaces: dict[str, Path] = field(default_factory=dict, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def start_actor(self, actor_id: str) -> Actor:
        async with self._lock:
            return await self._start_actor_locked(actor_id)

    async def _start_actor_locked(self, actor_id: str) -> Actor:
        if actor_id in self._actors:
            return self._actors[actor_id]
        workspace_path = self.workspace_resolver.resolve(actor_id)
        binding = await load_actor_binding(
            self.repository,
            actor_id,
            workspace_path=workspace_path,
        )
        actor = await self._create_actor(binding)
        try:
            await actor.start()
        except Exception:
            self.gateway.close_mailbox(actor_id)
            raise
        self._actors[actor_id] = actor
        self._actor_workspaces[actor_id] = workspace_path
        return actor

    async def stop_actor(self, actor_id: str) -> None:
        async with self._lock:
            await self._stop_actor_locked(actor_id)

    async def _stop_actor_locked(self, actor_id: str) -> None:
        actor = self._actors.pop(actor_id, None)
        self._actor_workspaces.pop(actor_id, None)
        if actor is not None:
            await actor.stop()
        self.gateway.close_mailbox(actor_id)

    async def stop_all(self) -> None:
        async with self._lock:
            for actor_id in list(self._actors):
                await self._stop_actor_locked(actor_id)

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
