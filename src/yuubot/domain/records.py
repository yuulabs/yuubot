"""Durable configuration and routing records."""

import msgspec

from .messages import ModelCard
from .stream import Usage


class LifecycleError(msgspec.Struct, frozen=True, kw_only=True):
    type: str
    message: str


class ConversationRow(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    actor_id: str
    status: str
    created_at: str
    last_active_at: str
    last_error: dict[str, object] | None = None
    title: str = ""


class CostRow(msgspec.Struct, frozen=True, kw_only=True):
    conversation_id: str
    seq: int
    usage: Usage
    account: dict[str, object]
    estimated: bool
    created_at: str


class ActorStatus(msgspec.Struct, frozen=True, kw_only=True):
    enabled: bool
    status: str
    last_error: LifecycleError | None = None


class IntegrationStatus(msgspec.Struct, frozen=True, kw_only=True):
    enabled: bool
    last_error: LifecycleError | None = None


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


def decode_lifecycle_error(payload: bytes | None) -> LifecycleError | None:
    if payload is None:
        return None
    return msgspec.json.decode(payload, type=LifecycleError)


def lifecycle_error(exc: Exception) -> LifecycleError:
    return LifecycleError(type=type(exc).__name__, message=str(exc))


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


