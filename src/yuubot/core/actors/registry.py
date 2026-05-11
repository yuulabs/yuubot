"""Actor factory registry."""

from __future__ import annotations

from dataclasses import dataclass, field

from yuubot.core.actors.contracts import ActorFactory


@dataclass
class ActorFactoryRegistry:
    _factories: dict[str, ActorFactory] = field(default_factory=dict)

    def register(self, factory: ActorFactory) -> None:
        self._factories[factory.actor_type] = factory

    def get(self, actor_type: str) -> ActorFactory:
        try:
            return self._factories[actor_type]
        except KeyError as exc:
            raise LookupError(f"actor factory {actor_type!r} is not registered") from exc
