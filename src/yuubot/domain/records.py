"""Durable configuration and routing records."""

import msgspec

from .stream import Usage
from .models import ModelSelector

DEFAULT_CONTEXT_COMPRESSION_TOKENS = 262144


class LifecycleError(msgspec.Struct, frozen=True):
    type: str
    message: str


class ConversationRow(msgspec.Struct, frozen=True):
    id: str
    actor_id: str
    status: str
    created_at: str
    last_active_at: str
    last_error: dict[str, object] | None = None
    title: str = ""


class UsageRow(msgspec.Struct, frozen=True):
    conversation_id: str
    seq: int
    usage: Usage
    account: dict[str, object]
    created_at: str


class ActorStatus(msgspec.Struct, frozen=True):
    enabled: bool
    status: str
    last_error: LifecycleError | None = None


class IntegrationStatus(msgspec.Struct, frozen=True):
    enabled: bool
    last_error: LifecycleError | None = None


class ActorRecord(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    name: str
    description: str = ""
    workspace: str = ""
    persona: str = ""
    model: ModelSelector | None = None
    context_compression_tokens: int = DEFAULT_CONTEXT_COMPRESSION_TOKENS


class ActorInput(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    name: str
    description: str = ""
    workspace: str = ""
    persona: str = ""
    model: ModelSelector
    context_compression_tokens: int = DEFAULT_CONTEXT_COMPRESSION_TOKENS
    tools: dict[str, object] = msgspec.field(default_factory=dict)


class ActorConfigError(ValueError):
    def __init__(self, code: str, message: str, detail: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def decode_actor_record(payload: bytes) -> ActorRecord:
    return msgspec.json.decode(payload, type=ActorRecord)


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
    return LifecycleError(type(exc).__name__, str(exc))


class RouteInput(msgspec.Struct, frozen=True):
    pattern: str
    actor_id: str
    integration_type: str = ""
    enabled: bool = True

    def to_record(self, route_id: str) -> RouteRecord:
        return RouteRecord(
            id=route_id,
            integration_type=self.integration_type,
            pattern=self.pattern,
            actor_id=self.actor_id,
            enabled=self.enabled,
        )


class RouteBody(msgspec.Struct, frozen=True):
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
