from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from typing import cast

import yuullm

from yuuagents.agent.agent import Agent
from yuuagents.core.budget import Budget
from yuuagents.agent.definition import AgentDefinition, LlmConfig
from yuuagents.core.eventbus import EventBus
from yuuagents.llm.session import ProviderPoolSessionFactory, select_llm_session_factory
from yuuagents.core.mailbox import (
    BackgroundCompletedMessage,
    MailMessage,
    ScheduleTriggerMessage,
)
from yuuagents.core.stage import Stage
from yuuagents.core.task import Owner, OwnerType, Task
from yuuagents.tool.primitives import ToolCallTask, ToolContext
from yuuagents.types.values import EventPayload, LlmOptions

__all__ = [
    "ExampleActor",
    "close_actor_resources",
    "create_agent",
    "emit_actor_message_received",
    "emit_actor_message_unhandled",
    "emit_agent_started",
    "emit_budget_exceeded",
    "run_agent_loop",
]


def create_agent(
    stage: Stage,
    definition: AgentDefinition,
    *,
    agent_id: str | None = None,
) -> Agent:
    """Create an Agent from Stage resources and AgentDefinition."""
    return Agent.build(stage, definition, agent_id=agent_id)


async def emit_actor_message_received(
    eventbus: EventBus,
    message: MailMessage,
) -> None:
    await eventbus.emit(
        "actor.message_received",
        {
            "message_type": type(message).__name__,
        },
    )


async def emit_actor_message_unhandled(
    eventbus: EventBus,
    message: MailMessage | type[MailMessage] | str,
    extra: EventPayload | None = None,
) -> None:
    await eventbus.emit(
        "actor.message_unhandled",
        {
            "message_type": _mail_message_type(message),
            **(extra or {}),
        },
    )


async def emit_agent_started(
    eventbus: EventBus,
    agent: Agent,
    definition: AgentDefinition,
) -> None:
    messages, tool_specs = yuullm.split_history(agent.history)
    await eventbus.emit(
        "agent.started",
        {
            "agent_id": agent.id,
            "agent_name": agent.name,
            "model": definition.llm.model,
            "tool_specs": tool_specs or [],
            "history": list(messages),
        },
    )


async def emit_budget_exceeded(eventbus: EventBus, agent: Agent) -> None:
    await eventbus.emit(
        "budget.exceeded",
        {
            "agent_id": agent.id,
            "agent_name": agent.name,
        },
    )


def _render_task_result(task: Task) -> str:
    """Render a completed Task's result back to the agent as text."""
    if task.error:
        return f"Error: {task.error.message}"
    if task.result is None:
        return "(no result)"
    return str(task.result)


async def run_agent_loop(
    agent: Agent,
    stage: Stage,
    budget: Budget | None = None,
) -> None:
    """Default agent loop. Calls step() and executes tools via stage.runtime.

    Orchestrator (yuubot) can provide its own loop. This is the default
    implementation that works with the new Runtime tool system.
    """
    while not agent.done:
        message, _store = await agent.step()

        # Extract tool calls from the assistant message
        tool_calls = [
            yuullm.ToolCall(
                id=item["id"],
                name=item["name"],
                arguments=item["arguments"],
            )
            for item in message.content
            if item["type"] == "tool_call"
        ]

        if not tool_calls:
            continue

        # Submit all tool calls via new Runtime (runs concurrently)
        tasks: list[Task] = []
        for tc in tool_calls:
            context = ToolContext(
                agent_id=agent.id,
                tool_call_id=tc.id,
                eventbus=stage.eventbus,
                entity_log=agent.log,
            )
            task = await stage.runtime.submit_tool_call(
                Owner(type=OwnerType.AGENT, id=agent.id),
                tc,
                context,
            )
            tasks.append(task)

        # Wait for each task and write results back to agent history
        for task in tasks:
            completed = await stage.runtime.wait_task(task.id)
            result_text = _render_task_result(completed)
            tc_task = cast(ToolCallTask, completed)
            agent.append(
                yuullm.tool(tc_task.tool_call_params.tool_call_id, result_text)
            )


async def close_actor_resources(stage: Stage) -> None:
    """Close runtime resources held by the stage."""
    await stage.runtime.close()


class ExampleActor:
    """Example actor implementation showing one way to wire Stage and Agent."""

    def __init__(
        self,
        stage: Stage,
        definitions: dict[str, AgentDefinition]
        | Iterable[AgentDefinition]
        | None = None,
    ) -> None:
        self.stage = stage
        self.definitions: dict[str, AgentDefinition] = {}
        self.agents: dict[str, Agent] = {}
        self.agents_by_name: dict[str, Agent] = {}
        self._agent_locks: dict[str, asyncio.Lock] = {}
        if definitions is not None:
            self.register_definitions(definitions)

    def register_definitions(
        self,
        definitions: dict[str, AgentDefinition] | Iterable[AgentDefinition],
    ) -> None:
        if isinstance(definitions, Mapping):
            self.definitions.update(cast(Mapping[str, AgentDefinition], definitions))
            return
        for definition in definitions:
            self.register_definition(definition)

    def register_definition(self, definition: AgentDefinition) -> None:
        if not definition.name:
            raise ValueError("AgentDefinition must have a name to be mailbox-routable")
        self.definitions[definition.name] = definition

    def create_agent(
        self,
        definition: AgentDefinition,
        *,
        agent_id: str | None = None,
    ) -> Agent:
        agent = create_agent(
            self.stage,
            definition,
            agent_id=agent_id,
        )
        self._track_agent(agent)
        return agent

    async def expire_agent(self, agent: Agent) -> None:
        await agent.close(status="expired")
        self._untrack_agent(agent)

    async def run(self) -> None:
        while True:
            await self.run_once()

    async def run_once(self) -> Agent | None:
        message = await self.stage.mailbox.recv()
        return await self.handle_message(message)

    async def handle_message(self, message: MailMessage) -> Agent | None:
        await emit_actor_message_received(self.stage.eventbus, message)
        match message:
            case ScheduleTriggerMessage(agent_name=agent_name):
                return await self._route_agent_message(agent_name, message.content)
            case BackgroundCompletedMessage():
                return await self._route_background_completed_message(message)
            case _:
                await emit_actor_message_unhandled(self.stage.eventbus, message)
                return None

    async def run_agent_loop(self, agent: Agent) -> None:
        """Run the default agent loop with this actor's Stage."""
        await run_agent_loop(agent, self.stage)

    async def close(self) -> None:
        for agent in list(self.agents.values()):
            await agent.close()
        await close_actor_resources(self.stage)

    def _track_agent(self, agent: Agent) -> None:
        self.agents[agent.id] = agent
        if agent.name:
            self.agents_by_name[agent.name] = agent

    def _untrack_agent(self, agent: Agent) -> None:
        self.agents.pop(agent.id, None)
        if agent.name and self.agents_by_name.get(agent.name) is agent:
            self.agents_by_name.pop(agent.name, None)
        self._agent_locks.pop(agent.id, None)

    async def _route_agent_message(
        self,
        agent_name: str,
        message: yuullm.Message | None,
    ) -> Agent | None:
        agent = self.agents_by_name.get(agent_name)
        if agent is None:
            definition = self.definitions.get(agent_name)
            if definition is None:
                await emit_actor_message_unhandled(
                    self.stage.eventbus,
                    ScheduleTriggerMessage,
                    extra={"agent_name": agent_name},
                )
                return None
            agent = self.create_agent(definition)
            self.agents_by_name[agent_name] = agent
            await emit_agent_started(self.stage.eventbus, agent, definition)

        if message is not None:
            agent.append(message)
        await self._run_agent_serialized(agent)
        return agent

    async def _route_background_completed_message(
        self,
        message: BackgroundCompletedMessage,
    ) -> Agent | None:
        if message.agent_id:
            agent = self.agents.get(message.agent_id)
            if agent is not None:
                if message.content is not None:
                    agent.append(message.content)
                await self._run_agent_serialized(agent)
                return agent
        if message.agent_name:
            return await self._route_agent_message(message.agent_name, message.content)
        if len(self.agents) == 1:
            agent = next(iter(self.agents.values()))
            if message.content is not None:
                agent.append(message.content)
            await self._run_agent_serialized(agent)
            return agent
        if len(self.definitions) == 1:
            agent_name = next(iter(self.definitions))
            return await self._route_agent_message(agent_name, message.content)
        await emit_actor_message_unhandled(
            self.stage.eventbus,
            message,
            extra={
                "agent_id": message.agent_id,
                "agent_name": message.agent_name,
                "task_id": message.task_id,
            },
        )
        return None

    async def _run_agent_serialized(self, agent: Agent) -> None:
        lock = self._agent_locks.setdefault(agent.id, asyncio.Lock())
        async with lock:
            async with self.stage.eventbus.scope(
                "agent.turn",
                {
                    "agent_id": agent.id,
                    "agent_name": agent.name,
                },
            ):
                await self.run_agent_loop(agent)


def _mail_message_type(message: MailMessage | type[MailMessage] | str) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, type):
        return message.__name__
    return type(message).__name__


def _resolve_agent_llm(
    stage: Stage, config: LlmConfig
) -> tuple[ProviderPoolSessionFactory, LlmOptions]:
    if not config.provider:
        raise ValueError("AgentDefinition.llm.provider is required")
    if config.provider not in stage.llm_session_factories:
        available = ", ".join(sorted(stage.llm_session_factories)) or "<none>"
        raise ValueError(
            f"Agent requires LLM provider {config.provider!r}, "
            f"but Stage only has: {available}"
        )
    factory = stage.llm_session_factories[config.provider]
    if config.model:
        factory = select_llm_session_factory(factory, config.model)
    return (
        factory,
        {**stage.llm_options.get(config.provider, {}), **config.stream_kwargs()},
    )
