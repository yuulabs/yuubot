"""Thin yuubot wrapper around a live yuuagents Agent."""

from __future__ import annotations

from typing import Literal
import uuid

import attrs
import yuullm
import yuuagents as ya


RuntimeSessionStatus = Literal["idle", "running", "closed", "error", "timeout"]


@attrs.define
class RuntimeSession:
    agent: ya.Agent
    conversation_id: str
    agent_name: str
    supports_vision: bool = False
    task_id: str = attrs.field(factory=lambda: uuid.uuid4().hex[:12])
    status: RuntimeSessionStatus = "idle"
    final_text: str = ""
    total_tokens: int = 0
    last_usage: yuullm.Usage | None = None
    snapshot: ya.AgentSnapshot | None = None
    stop_reason: str = "natural"
    steps: list[ya.AgentStep] = attrs.field(factory=list)

    @property
    def history(self) -> list[yuullm.Message]:
        return list(self.agent.history)

    @property
    def closed(self) -> bool:
        return self.agent.closed

    async def close(self) -> None:
        self.status = "closed"
        await self.agent.close()

    def update_usage(self, usage: yuullm.Usage | None) -> None:
        if usage is None:
            return
        self.last_usage = usage
        if usage.total_tokens is not None:
            self.total_tokens = int(usage.total_tokens)
        else:
            self.total_tokens += (
                int(usage.input_tokens or 0)
                + int(usage.cache_read_tokens or 0)
                + int(usage.cache_write_tokens or 0)
                + int(usage.output_tokens or 0)
            )
