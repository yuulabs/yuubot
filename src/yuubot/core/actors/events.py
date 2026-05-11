"""Actor lifecycle events."""

from __future__ import annotations

from dataclasses import dataclass

from yuubot.events import Event


@dataclass
class ActorLifecycleCommand(Event):
    actor_id: str


@dataclass
class StartActor(ActorLifecycleCommand):
    pass


@dataclass
class StopActor(ActorLifecycleCommand):
    pass
