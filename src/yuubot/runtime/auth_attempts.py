"""Durable auth attempt state machine records."""

from __future__ import annotations

import uuid
from typing import Literal

import msgspec

from ..util.time import utc_now_iso

AuthAttemptMethod = Literal["oauth_pkce", "device_code", "api_key", "manual"]
AuthAttemptStatus = Literal["waiting_for_user", "polling", "exchanging", "succeeded", "failed", "expired"]


class AuthAttempt(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    connection_id: str
    method: AuthAttemptMethod
    status: AuthAttemptStatus
    action: dict[str, object] = msgspec.field(default_factory=dict)
    error: str | None = None
    expires_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


class AuthAttemptCreate(msgspec.Struct, frozen=True, kw_only=True):
    connection_id: str
    method: AuthAttemptMethod
    action: dict[str, object] = msgspec.field(default_factory=dict)
    expires_at: str | None = None


def new_auth_attempt(body: AuthAttemptCreate) -> AuthAttempt:
    if not body.connection_id:
        raise ValueError("connection_id is required")
    now = utc_now_iso()
    return AuthAttempt(
        id=uuid.uuid4().hex,
        connection_id=body.connection_id,
        method=body.method,
        status="waiting_for_user" if body.method in {"oauth_pkce", "device_code", "api_key", "manual"} else "failed",
        action=body.action,
        expires_at=body.expires_at,
        created_at=now,
        updated_at=now,
    )


def transition_auth_attempt(
    attempt: AuthAttempt,
    *,
    status: AuthAttemptStatus,
    error: str | None = None,
    action: dict[str, object] | None = None,
) -> AuthAttempt:
    return AuthAttempt(
        id=attempt.id,
        connection_id=attempt.connection_id,
        method=attempt.method,
        status=status,
        action=attempt.action if action is None else action,
        error=error,
        expires_at=attempt.expires_at,
        created_at=attempt.created_at,
        updated_at=utc_now_iso(),
    )
