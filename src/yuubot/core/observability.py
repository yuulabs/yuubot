"""Trace observer — bridges yuuagents EventBus to yuutrace SDK.

A daemon-level singleton that subscribes to each actor's Stage eventbus,
translates framework events into yuutrace calls, and injects yuubot context
(conversation_id, character_name, model) so trace events are queryable by
these dimensions in the trace UI.

Context is registered per *agent_name* before the actor starts processing
messages, and unregistered when the actor stops.  The observer itself has
no dependency on actor lifecycle — it is a passive event translator.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, TypeGuard, cast

import msgspec
import yuutrace
from opentelemetry.util.types import AttributeValue
from yuuagents.eventbus import RuntimeEvent
from yuutrace.otel import (
    ATTR_TOOL_CALL_ID,
    ATTR_TOOL_NAME,
    ATTR_TOOL_USAGE_QUANTITY,
    ATTR_TOOL_USAGE_UNIT,
    EVENT_TOOL_USAGE,
    OtelAttributes,
)


TOOL_COST_UNITS = {"usd", "USD"}


class TraceAgentContext(msgspec.Struct):
    """Fixed-structure context attached to each agent's trace events."""

    conversation_id: str
    character_name: str
    model: str


@dataclass
class TraceObserver:
    """Subscribe to yuuagents runtime events and record them via yuutrace.

    Usage::

        observer = TraceObserver()
        observer.register("my-agent", conversation_id="...",
                          character_name="yuu", model="gpt-4")
        stage.eventbus.subscribe(observer)

        # ... agent runs, events fire, observer translates them ...

        observer.unregister("my-agent")
    """

    _contexts: dict[str, TraceAgentContext] = field(default_factory=dict, init=False)
    _agent_id_contexts: dict[str, TraceAgentContext] = field(default_factory=dict, init=False)

    # ------------------------------------------------------------------
    # Context registration
    # ------------------------------------------------------------------

    def register(
        self,
        agent_name: str,
        *,
        conversation_id: str,
        character_name: str,
        model: str,
    ) -> None:
        """Register yuubot context for *agent_name*.

        Called by the actor on start / reload, before any messages are
        processed.  The observer uses this context to enrich trace events.
        """
        self._contexts[agent_name] = TraceAgentContext(
            conversation_id=conversation_id,
            character_name=character_name,
            model=model,
        )

    def unregister(self, agent_name: str) -> None:
        """Remove context for *agent_name* (actor stopped or reloading)."""
        ctx = self._contexts.pop(agent_name, None)
        if ctx is None:
            return
        stale_ids = [
            agent_id
            for agent_id, agent_ctx in self._agent_id_contexts.items()
            if agent_ctx == ctx
        ]
        for agent_id in stale_ids:
            self._agent_id_contexts.pop(agent_id, None)

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    async def on_event(self, event: RuntimeEvent) -> None:
        ctx = self._context_for(event)
        if ctx is None:
            return
        if event.name == "llm.finished":
            self._on_llm_finished(event.data, ctx)
        elif event.name == "runtime.usage_reported":
            self._on_usage_reported(event.data, ctx)
        elif event.name in {
            "llm.started",
            "runtime.task_created",
            "runtime.task_completed",
            "runtime.task_error",
            "budget.exceeded",
        }:
            self._record_runtime_event(event, ctx)

    def _context_for(self, event: RuntimeEvent) -> TraceAgentContext | None:
        ctx = self._contexts.get(event.agent_name)
        if ctx is not None:
            if event.agent_id:
                self._agent_id_contexts[event.agent_id] = ctx
            return ctx
        return self._agent_id_contexts.get(event.agent_id)

    def _on_llm_finished(self, data: Mapping[str, object], ctx: TraceAgentContext) -> None:
        usage_raw = data.get("usage")
        if usage_raw is None:
            return
        # usage is yuullm.Usage which satisfies yuutrace.LlmUsage Protocol
        usage = cast(yuutrace.LlmUsage, usage_raw)
        cost_raw = data.get("cost")
        cost = cast("yuutrace.LlmCost | None", cost_raw)
        yuutrace.record_llm_usage(usage, cost=cost)

    def _on_usage_reported(self, data: Mapping[str, object], ctx: TraceAgentContext) -> None:
        service = cast(str, data.get("service", ""))
        unit = cast(str, data.get("unit", ""))
        amount = float(cast("float | int", data.get("amount", 0.0)))
        task_id = cast("str | None", data.get("task_id"))
        if service and unit:
            yuutrace.add_event(
                EVENT_TOOL_USAGE,
                _tool_usage_attributes(data, ctx, service, unit, amount, task_id),
            )
            if unit in TOOL_COST_UNITS:
                yuutrace.record_cost(
                    category="tool",
                    currency="USD",
                    amount=amount,
                    source="runtime.usage_reported",
                    tool_name=service,
                    tool_call_id=task_id,
                )

    def _record_runtime_event(
        self,
        event: RuntimeEvent,
        ctx: TraceAgentContext,
    ) -> None:
        yuutrace.add_event(
            event.name,
            _runtime_event_attributes(event.data, ctx),
        )


def _tool_usage_attributes(
    data: Mapping[str, object],
    ctx: TraceAgentContext,
    service: str,
    unit: str,
    amount: float,
    task_id: str | None,
) -> OtelAttributes:
    attrs = _yuubot_context_attributes(ctx)
    attrs.update(
        {
            ATTR_TOOL_NAME: service,
            ATTR_TOOL_USAGE_UNIT: unit,
            ATTR_TOOL_USAGE_QUANTITY: amount,
        }
    )
    _set_string(attrs, ATTR_TOOL_CALL_ID, task_id)
    _copy_string(data, attrs, "actor_id", "yuubot.actor_id")
    _copy_string(data, attrs, "integration_id", "yuubot.integration_id")
    _copy_string(data, attrs, "capability_id", "yuubot.capability_id")
    _copy_string(data, attrs, "task_id", "yuubot.task_id")
    return attrs


def _runtime_event_attributes(
    data: Mapping[str, object],
    ctx: TraceAgentContext,
) -> OtelAttributes:
    attrs = _yuubot_context_attributes(ctx)
    for key, value in data.items():
        if _is_attribute_value(value):
            attrs[f"yuu.event.{key}"] = value
    return attrs


def _yuubot_context_attributes(
    ctx: TraceAgentContext,
) -> OtelAttributes:
    return {
        "yuubot.conversation_id": ctx.conversation_id,
        "yuubot.character_name": ctx.character_name,
        "yuubot.model": ctx.model,
    }


def _copy_string(
    source: Mapping[str, object],
    target: OtelAttributes,
    source_key: str,
    target_key: str,
) -> None:
    value = source.get(source_key)
    if isinstance(value, str) and value:
        target[target_key] = value


def _set_string(
    target: OtelAttributes,
    key: str,
    value: str | None,
) -> None:
    if value:
        target[key] = value


def _is_attribute_value(value: Any) -> TypeGuard[AttributeValue]:
    return isinstance(value, str | int | float | bool)
