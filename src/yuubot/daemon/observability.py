"""Yuubot observability adapters for yuuagents runtime events."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import attrs
from loguru import logger
import yuuagents as ya


@attrs.define
class YuubotRuntimeObserver:
    """Small observer that keeps yuubot runtime metadata beside yuuagents events."""

    events: list[ya.RuntimeEvent] = attrs.field(factory=list)
    _metadata_by_agent: dict[str, dict[str, Any]] = attrs.field(factory=dict, init=False)
    closed: bool = False

    def bind_agent(self, agent_id: str, metadata: Mapping[str, Any]) -> None:
        self._metadata_by_agent[agent_id] = dict(metadata)

    def on_event(self, event: ya.RuntimeEvent) -> None:
        metadata = self._metadata_by_agent.get(event.agent_id)
        if metadata:
            enriched = ya.RuntimeEvent(
                name=event.name,
                agent_id=event.agent_id,
                agent_name=event.agent_name,
                timestamp=event.timestamp,
                data={**metadata, **dict(event.data)},
            )
        else:
            enriched = event
        self.events.append(enriched)
        logger.debug(
            "runtime event: name={} agent={} ctx={} task={}",
            enriched.name,
            enriched.agent_name,
            enriched.data.get("ctx_id", ""),
            enriched.data.get("task_id", ""),
        )

    def close(self) -> None:
        self.closed = True


@attrs.define
class YuubotBillingSink:
    """Billing sink skeleton; records runtime events without enforcing budgets."""

    records: list[ya.BillingRecord] = attrs.field(factory=list)
    flushed: bool = False

    def record(self, event: ya.RuntimeEvent) -> None:
        if event.name == "llm.finished":
            self.records.append(
                ya.BillingRecord(
                    agent_id=event.agent_id,
                    agent_name=event.agent_name,
                    kind="llm",
                    data=dict(event.data),
                )
            )
        elif event.name == "tool.finished":
            self.records.append(
                ya.BillingRecord(
                    agent_id=event.agent_id,
                    agent_name=event.agent_name,
                    kind="tool",
                    data=dict(event.data),
                )
            )

    def flush(self) -> None:
        self.flushed = True
