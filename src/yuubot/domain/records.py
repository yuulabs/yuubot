"""Durable configuration and routing records."""

import msgspec

from .messages import ModelCard


class ActorRecord(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    name: str
    description: str = ""
    workspace: str = ""
    persona: str = ""
    model: ModelCard
    provider: str


def decode_actor_record(payload: bytes) -> ActorRecord:
    raw = msgspec.json.decode(payload)
    if isinstance(raw, dict) and "llm" in raw and "provider" not in raw:
        raw = dict(raw)
        raw["provider"] = raw.pop("llm")
    if isinstance(raw, dict) and "tools" in raw:
        raw = dict(raw)
        raw.pop("tools")
    return msgspec.convert(raw, ActorRecord)


class RouteRecord(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    integration_type: str = ""
    pattern: str
    actor_id: str
    enabled: bool = True


class RouteBody(msgspec.Struct, frozen=True, kw_only=True):
    pattern: str
    actor_id: str
    id: str = ""
    integration_type: str = ""
    enabled: bool = True

    def to_record(self) -> RouteRecord:
        return RouteRecord(
            id=self.id or self.pattern,
            integration_type=self.integration_type,
            pattern=self.pattern,
            actor_id=self.actor_id,
            enabled=self.enabled,
        )


