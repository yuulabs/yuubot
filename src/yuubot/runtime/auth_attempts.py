"""Durable auth attempt state machine records."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Iterator, MutableMapping
from datetime import UTC, datetime, timedelta
from typing import Literal

from attrs import define, field
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


def _parse_timestamp(value: str) -> datetime:
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def auth_attempt_expires_at(*, ttl_s: float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=ttl_s)).isoformat()


def auth_attempt_is_expired(attempt: AuthAttempt, *, now: datetime | None = None) -> bool:
    if attempt.expires_at is None:
        return False
    current = now or datetime.now(UTC)
    return current >= _parse_timestamp(attempt.expires_at)


def new_auth_attempt(body: AuthAttemptCreate) -> AuthAttempt:
    if not body.connection_id:
        raise ValueError("connection_id is required")
    if body.expires_at is not None:
        _parse_timestamp(body.expires_at)
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


@define
class AuthAttemptRegistry(MutableMapping[str, AuthAttempt]):
    _attempts: dict[str, AuthAttempt] = field(factory=dict)
    _condition: asyncio.Condition = field(factory=asyncio.Condition)

    def __getitem__(self, key: str) -> AuthAttempt:
        return self._attempts[key]

    def __setitem__(self, key: str, value: AuthAttempt) -> None:
        self._attempts[key] = value

    def __delitem__(self, key: str) -> None:
        del self._attempts[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._attempts)

    def __len__(self) -> int:
        return len(self._attempts)

    async def put(self, attempt: AuthAttempt) -> None:
        async with self._condition:
            self._attempts[attempt.id] = attempt
            self._condition.notify_all()

    async def discard(self, attempt_id: str) -> bool:
        async with self._condition:
            existed = self._attempts.pop(attempt_id, None) is not None
            self._condition.notify_all()
            return existed

    async def wait_for(
        self,
        attempt_id: str,
        *,
        predicate: Callable[[AuthAttempt], bool],
        timeout: float,
    ) -> AuthAttempt | None:
        def ready() -> bool:
            current = self._attempts.get(attempt_id)
            return current is None or predicate(current)

        async with self._condition:
            if not ready():
                try:
                    await asyncio.wait_for(self._condition.wait_for(ready), timeout=timeout)
                except TimeoutError:
                    pass
            return self._attempts.get(attempt_id)

    def expired_ids(self, *, now: datetime | None = None) -> list[str]:
        current = now or datetime.now(UTC)
        return [
            attempt.id
            for attempt in list(self._attempts.values())
            if auth_attempt_is_expired(attempt, now=current)
        ]
