"""Yuubot observability adapter — EventBus subscriber with context enrichment.

Replaces the old YuubotRuntimeObserver + YuubotBillingSink (pre-0.2.0 API).
Billing is now handled by yuuagents Budget.charge() internally; this module
is purely for monitoring, logging, and OTEL forwarding.
"""

from __future__ import annotations

from typing import Any

import attrs
from loguru import logger
import yuuagents as ya
import yuutrace


@attrs.define
class YuubotTraceObserver:
    """Subscribes to EventBus, enriches events with yuubot metadata, forwards to OTEL.

    Usage::

        observer = YuubotTraceObserver()
        stage.eventbus.subscribe(observer)
        observer.bind_agent("agent-123", {"ctx_id": 42, "task_id": "abc"})
    """

    _metadata_by_agent: dict[str, dict[str, Any]] = attrs.field(factory=dict, init=False)
    closed: bool = False

    def bind_agent(self, agent_id: str, metadata: dict[str, Any]) -> None:
        """Register yuubot context metadata for an agent."""
        self._metadata_by_agent[agent_id] = metadata

    def on_event(self, event: ya.RuntimeEvent) -> None:
        """Receive a RuntimeEvent from the EventBus, enrich with context, log and forward."""
        metadata = self._metadata_by_agent.get(event.agent_id, {})
        enriched = {**metadata, **dict(event.data)}

        logger.debug(
            "runtime event: name={} agent={} ctx={} task={}",
            event.name,
            event.agent_name,
            enriched.get("ctx_id", ""),
            enriched.get("task_id", ""),
        )

        # Best-effort forward to yuutrace/OTEL — silently skip if no span is active
        try:
            yuutrace.add_event(
                event.name,
                {
                    "agent.id": event.agent_id,
                    "agent.name": event.agent_name,
                    "yuubot.ctx_id": enriched.get("ctx_id", ""),
                    "yuubot.conversation_id": enriched.get("conversation_id", ""),
                    "yuubot.task_id": enriched.get("task_id", ""),
                    "yuubot.character_name": enriched.get("character_name", ""),
                    "yuubot.chat_type": enriched.get("chat_type", ""),
                },
            )
        except yuutrace.NoActiveSpanError:
            pass

    def close(self) -> None:
        """Mark the observer as closed."""
        self.closed = True
