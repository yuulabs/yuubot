"""yuubot-specific trace context for yuuagents observability."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid5

from opentelemetry.util.types import AttributeValue
from yuuagents import RuntimeEvent


@dataclass
class YuubotTraceContext:
    conversation_id: UUID | None = None
    character_name: str = ""
    model: str = ""


@dataclass
class YuubotTraceContextProvider:
    """Adds yuubot actor/character/integration attributes to yuuagents traces."""

    _contexts: dict[str, YuubotTraceContext] = field(default_factory=dict, init=False)
    _agent_contexts: dict[str, YuubotTraceContext] = field(
        default_factory=dict, init=False
    )
    _agent_names: dict[str, str] = field(default_factory=dict, init=False)

    def register(
        self,
        agent_name: str,
        *,
        character_name: str = "",
        model: str = "",
    ) -> None:
        ctx = self._contexts.setdefault(agent_name, YuubotTraceContext())
        if character_name:
            ctx.character_name = character_name
        if model:
            ctx.model = model
        for agent_id, mapped_name in self._agent_names.items():
            if mapped_name != agent_name:
                continue
            agent_ctx = self._agent_contexts.get(agent_id)
            if agent_ctx is None:
                continue
            if character_name:
                agent_ctx.character_name = character_name
            if model:
                agent_ctx.model = model

    def conversation_id(self, event: RuntimeEvent) -> UUID | str | None:
        ctx = self._context_for(event)
        if ctx.conversation_id is None:
            if not event.agent_id:
                return None
            ctx.conversation_id = uuid5(NAMESPACE_DNS, event.agent_id)
        return ctx.conversation_id

    def agent_name(self, event: RuntimeEvent) -> str:
        return event.agent_name

    def model(self, event: RuntimeEvent) -> str:
        ctx = self._context_for(event)
        if ctx.model:
            return ctx.model
        value = event.data.get("model")
        return value if isinstance(value, str) else ""

    def tags(self, event: RuntimeEvent) -> list[str] | None:
        return ["yuubot-v2"]

    def event_attributes(self, event: RuntimeEvent) -> dict[str, AttributeValue]:
        ctx = self._context_for(event)
        attrs: dict[str, AttributeValue] = {
            "yuubot.character_name": ctx.character_name,
            "yuubot.model": self.model(event),
        }
        if ctx.conversation_id is not None:
            attrs["yuubot.conversation_id"] = str(ctx.conversation_id)
        _copy_string(event.data, attrs, "actor_id", "yuubot.actor_id")
        _copy_string(event.data, attrs, "integration_id", "yuubot.integration_id")
        _copy_string(event.data, attrs, "capability_id", "yuubot.capability_id")
        _copy_string(event.data, attrs, "task_id", "yuubot.task_id")
        return {
            k: v for k, v in attrs.items() if isinstance(v, (str, int, float, bool))
        }

    def _context_for(self, event: RuntimeEvent) -> YuubotTraceContext:
        if event.agent_id and event.agent_id in self._agent_contexts:
            return self._agent_contexts[event.agent_id]
        if event.agent_name:
            if event.agent_id:
                self._agent_names[event.agent_id] = event.agent_name
                base = self._contexts.setdefault(event.agent_name, YuubotTraceContext())
                ctx = YuubotTraceContext(
                    character_name=base.character_name,
                    model=base.model,
                )
                self._agent_contexts[event.agent_id] = ctx
                return ctx
            return self._contexts.setdefault(event.agent_name, YuubotTraceContext())
        if event.agent_id:
            return self._agent_contexts.setdefault(event.agent_id, YuubotTraceContext())
        return YuubotTraceContext()


def _copy_string(
    source: Mapping[str, Any],
    target: dict[str, AttributeValue],
    source_key: str,
    target_key: str,
) -> None:
    value = source.get(source_key)
    if isinstance(value, str) and value:
        target[target_key] = value
