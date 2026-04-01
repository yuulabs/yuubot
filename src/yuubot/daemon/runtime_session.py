"""Runtime session wrapper around the simplified yuuagents Agent API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import attrs
import yuullm
from yuuagents.core.flow import Agent, Flow
from yuuagents.types import AgentStatus

if TYPE_CHECKING:
    from yuuagents.agent import AgentConfig
    from yuuagents.context import AgentContext

    from yuubot.capabilities import CapabilityContext


@attrs.define
class RuntimeSession:
    """Small compatibility layer for yuubot's conversation/runtime logic."""

    task_id: str
    runtime_id: str
    agent_name: str
    agent: Agent
    capability_context: CapabilityContext | None = None
    stop_reason: str = "natural"
    status: AgentStatus = AgentStatus.RUNNING

    @property
    def agent_id(self) -> str:
        return self.runtime_id

    @property
    def config(self) -> AgentConfig:
        return self.agent.config

    @property
    def context(self) -> AgentContext:
        return self.agent.ctx

    @property
    def flow(self) -> Flow[Any, Any]:
        return self.agent.flow

    @property
    def history(self) -> list[yuullm.Message]:
        return list(self.agent.messages)

    @property
    def total_tokens(self) -> int:
        return self.agent.total_tokens

    @property
    def last_usage(self) -> yuullm.Usage | None:
        return self.agent.last_usage

    @property
    def conversation_id(self) -> UUID:
        return self.agent.conversation_id_value

    def send(
        self,
        content: str | yuullm.Item | list[yuullm.Item] | yuullm.Message,
        *,
        defer_tools: bool = False,
    ) -> None:
        self.agent.send(content, defer_tools=defer_tools)

    def cancel(self) -> None:
        self.agent.flow.cancel()

    async def kill(self) -> None:
        await self.agent.kill()

    async def wait(self) -> None:
        await self.agent.flow.wait()

    def find_flow(self, flow_id: str) -> Flow[Any, Any] | None:
        return self.agent.flow.find(flow_id)

    def has_tool_call(
        self,
        name: str,
        *,
        argument_contains: str | None = None,
    ) -> bool:
        for role, items in self.agent.messages:
            if role != "assistant":
                continue
            for item in items:
                if item.get("type") != "tool_call":
                    continue
                if item.get("name") != name:
                    continue
                if argument_contains is None:
                    return True
                arguments = item.get("arguments", "")
                if isinstance(arguments, str) and argument_contains in arguments:
                    return True
        return False
