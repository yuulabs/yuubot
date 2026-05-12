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
from typing import cast

import msgspec
import yuutrace
from yuuagents.eventbus import RuntimeEvent


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
        self._contexts.pop(agent_name, None)

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    async def on_event(self, event: RuntimeEvent) -> None:
        ctx = self._contexts.get(event.agent_name)
        if ctx is None:
            return
        if event.name == "llm.finished":
            self._on_llm_finished(event.data, ctx)
        elif event.name == "runtime.usage_reported":
            self._on_usage_reported(event.data, ctx)

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
            yuutrace.record_tool_usage(
                yuutrace.ToolUsageDelta(
                    name=service,
                    unit=unit,
                    quantity=amount,
                    call_id=task_id,
                )
            )
