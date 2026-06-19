"""Invocation context passed to integrations."""

from __future__ import annotations

from typing import Any, Protocol, cast

import msgspec
from collections.abc import Mapping


class UsageRecorder(Protocol):
    def charge(
        self,
        service: str,
        amount: float,
        unit: str,
        *,
        category: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> None: ...


class InvocationContext(msgspec.Struct):
    """Framework-populated context for a single capability invocation."""

    actor_id: str
    source_id: str = ""
    source_path: str = ""
    integration_id: str = ""
    capability_id: str = ""
    usage: object | None = None
    raw: dict[str, object] = msgspec.field(default_factory=dict)

    @property
    def usage_recorder(self) -> UsageRecorder | None:
        return cast(UsageRecorder | None, self.usage)

    def charge_usage(
        self,
        service: str,
        amount: float,
        unit: str,
        *,
        category: str | None = "integration",
        metadata: Mapping[str, Any] | None = None,
        **attributes: Any,
    ) -> None:
        if self.usage_recorder is None:
            return
        usage_attributes = {
            "actor_id": self.actor_id,
            "integration_id": self.integration_id,
            "capability_id": self.capability_id,
            **attributes,
        }
        self.usage_recorder.charge(
            service,
            amount,
            unit,
            category=category,
            metadata=metadata,
            attributes=usage_attributes,
        )


def bind_invocation_context(
    context: InvocationContext | None,
    *,
    actor_id: str,
    integration_id: str,
    capability_id: str,
    usage: object | None = None,
) -> InvocationContext:
    if context is None:
        return InvocationContext(
            actor_id=actor_id,
            integration_id=integration_id,
            capability_id=capability_id,
            usage=usage,
        )
    return InvocationContext(
        actor_id=context.actor_id or actor_id,
        source_id=context.source_id,
        source_path=context.source_path,
        integration_id=context.integration_id or integration_id,
        capability_id=context.capability_id or capability_id,
        usage=context.usage or usage,
        raw=context.raw,
    )
