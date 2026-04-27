"""Shared service-layer models for the RFC2 yuubot runtime."""

from __future__ import annotations

import time
from typing import Any, Literal

import msgspec


class YuubotServiceError(Exception):
    """Base class for domain-service failures exposed through local APIs."""

    code = "service_error"

    def __init__(self, message: str = "") -> None:
        self.message = message or self.__class__.__name__
        super().__init__(self.message)

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class ServiceNotImplementedError(YuubotServiceError, NotImplementedError):
    """Raised when a daemon-local endpoint still advertises an unavailable action."""

    code = "not_implemented"


class AccessDenied(YuubotServiceError):
    """Raised when an actor is outside the allowed Master/Group scope."""

    code = "access_denied"


class InvalidScope(YuubotServiceError):
    """Raised when a request references a context/workspace outside its scope."""

    code = "invalid_scope"


class PageInfo(msgspec.Struct, frozen=True):
    limit: int = 50
    offset: int = 0
    total: int | None = None
    next_offset: int | None = None


class Reference(msgspec.Struct, frozen=True):
    title: str = ""
    url: str = ""
    source: str = ""
    quote: str = ""


class MediaRef(msgspec.Struct, frozen=True):
    media_id: str = ""
    url: str = ""
    local_path: str = ""
    mime_type: str = ""
    description: str = ""


class AuditEvent(msgspec.Struct, frozen=True):
    name: str
    actor_user_id: int = 0
    ctx_id: int = 0
    agent_id: str = ""
    conversation_id: str = ""
    function_name: str = ""
    status: Literal["started", "finished", "denied", "error"] = "started"
    summary: str = ""
    created_at: float = msgspec.field(default_factory=time.time)
    data: dict[str, Any] = msgspec.field(default_factory=dict)


class EmptyResult(msgspec.Struct, frozen=True):
    """Stable empty payload for unavailable service endpoints."""

    status: str = "not_implemented"
    detail: str = "This service action is unavailable in the current runtime."
