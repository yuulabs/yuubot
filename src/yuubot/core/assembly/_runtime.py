"""YuuAgents actor runtime — orchestrates agent lifecycle, message routing,
and history rollover for a single yuuagents Stage.

This is the orchestrator class described in Pattern 1 (composition splitting).
It delegates rollover/prompt/tool concerns to the pure-function helpers in
the sibling modules.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import cast as typecast

import yuullm
from yuuagents import (
    Agent,
    AgentDefinition,
    BackgroundCompletedMessage,
    Budget,
    MailMessage,
    Owner,
    OwnerType,
    ScheduleTriggerMessage,
    Stage,
    ToolContext,
    close_actor_resources,
    create_agent,
    emit_actor_message_received,
    emit_actor_message_unhandled,
    emit_agent_started,
    emit_budget_exceeded,
)
from yuuagents.core.task import Task as YuuTask

from yuubot.core.costing import calculate_cost
from yuubot.resources.records import PricingTable

from ._rollover import (
    _agent_needs_rollover,
    _compacted_history,
    _last_assistant_text,
    _reset_token_usage,
    _summary_history,
)


def _schedule_db_path(
    config: dict[str, dict[str, object]] | None,
) -> str | None:
    if config is None:
        return None
    sched = config.get("schedule")
    if isinstance(sched, dict):
        path = sched.get("db_path")
        return str(path) if isinstance(path, str) else None
    return None


@dataclass
class YuuAgentsActorRuntime:
    """Orchestrates agent lifecycles within a single yuuagents Stage.

    Owns agent registries, per-agent locks, idle expiry tracking, and
    delegates rollover/completion logic to pure helpers.

    Budget and pricing are stored externally (not on Agent) and managed
    by this runtime.
    """

    stage: Stage
    definitions: dict[str, AgentDefinition]
    conversation_definition: AgentDefinition
    rollover_enabled: bool = False
    idle_timeout_s: float = 0.0
    summarize_steps_span: int = 20
    agents: dict[str, Agent] = field(default_factory=dict)
    agents_by_name: dict[str, Agent] = field(default_factory=dict)
    conversation_agents: dict[str, Agent] = field(default_factory=dict)
    agent_pricings: dict[str, PricingTable] = field(default_factory=dict)
    _agent_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _agent_last_used: dict[str, float] = field(default_factory=dict)
    _idle_expiry_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    _agent_budgets: dict[str, Budget] = field(default_factory=dict)
    _schedule_store: dict[str, dict[str, object]] = field(default_factory=dict)

    # ── Public API ───────────────────────────────────────────────

    async def handle_message(self, message: MailMessage) -> Agent | None:
        await emit_actor_message_received(self.stage.eventbus, message)
        match message:
            case ScheduleTriggerMessage(agent_name=agent_name):
                return await self._handle_agent_message(agent_name, message.content)
            case BackgroundCompletedMessage():
                return await self._handle_background_completed(message)
            case _:
                await emit_actor_message_unhandled(self.stage.eventbus, message)
                return None

    def store_pricing(self, agent_id: str, pricing: PricingTable) -> None:
        """Store pricing table for an agent (used by the orchestrator loop)."""
        self.agent_pricings[agent_id] = pricing

    async def close(self) -> None:
        for task in self._idle_expiry_tasks.values():
            task.cancel()
        self._idle_expiry_tasks.clear()
        for agent in list(self.agents.values()):
            await agent.close()
        await close_actor_resources(self.stage)

    async def ensure_conversation_agent(
        self,
        conversation_id: str,
        history: yuullm.History,
    ) -> Agent:
        agent = self.conversation_agents.get(conversation_id)
        if agent is not None:
            return agent
        definition = self._conversation_definition(conversation_id)
        agent = create_agent(self.stage, definition)
        self._init_agent_state(agent, definition)
        agent.history.extend(history)
        self.conversation_agents[conversation_id] = agent
        self._track_agent(agent)
        await emit_agent_started(self.stage.eventbus, agent, definition)
        return agent

    async def handle_conversation_message(
        self,
        conversation_id: str,
        message: yuullm.Message,
        history: yuullm.History,
    ) -> Agent:
        agent = await self.ensure_conversation_agent(conversation_id, history)
        agent.append(message)
        await self._run_agent_turn(agent)
        return agent

    async def run_delegate(
        self,
        *,
        task_id: str,
        prompt: str,
        parent_agent_name: str,
        delegate_name: str = "",
    ) -> str:
        definition = self._delegate_definition(
            task_id,
            parent_agent_name,
            delegate_name,
        )
        agent = create_agent(self.stage, definition)
        self._init_agent_state(agent, definition)
        self._track_agent(agent)
        await emit_agent_started(self.stage.eventbus, agent, definition)
        try:
            agent.append(yuullm.user(prompt))
            await self._run_agent_turn(agent)
            return _last_assistant_text(agent)
        finally:
            await agent.close(status="completed")
            await self._untrack_agent(agent)

    async def run_schedule_tool(
        self,
        *,
        agent_name: str,
        tool_name: str,
        payload: dict[str, object],
    ) -> object:
        if tool_name not in {"create_cron", "list_crons", "delete_cron"}:
            raise ValueError(f"unknown schedule tool: {tool_name!r}")
        agent = await self._agent_by_name(agent_name or self._default_agent_name())
        if agent is None:
            raise RuntimeError("schedule tool requires a running actor agent")
        if tool_name == "create_cron":
            return await self._schedule_create_cron(payload)
        if tool_name == "delete_cron":
            return await self._schedule_delete_cron(payload)
        return await self._schedule_list_crons()

    async def _schedule_create_cron(self, payload: dict[str, object]) -> str:
        job_id = str(payload.get("job_id", ""))
        cron = str(payload.get("cron", ""))
        actions = payload.get("actions", ())
        self._schedule_store[job_id] = {
            "cron": cron,
            "actions": list(actions) if isinstance(actions, tuple | list) else list(actions),
            "once": bool(payload.get("once", False)),
        }
        return f"Created cron job {job_id}: {cron}"

    async def _schedule_list_crons(self) -> str:
        if not self._schedule_store:
            return "No cron jobs configured."
        lines = [f"Cron Jobs ({len(self._schedule_store)}):"]
        for jid, entry in self._schedule_store.items():
            cron = entry.get("cron", "")
            actions = entry.get("actions", [])
            lines.append(f"  - {jid}: {cron}  actions: {actions}")
        return "\n".join(lines)

    async def _schedule_delete_cron(self, payload: dict[str, object]) -> str:
        job_id = str(payload.get("job_id", ""))
        if job_id in self._schedule_store:
            del self._schedule_store[job_id]
            return f"Deleted cron job {job_id}"
        return f"Cron job {job_id} not found"

    # ── Message dispatch ─────────────────────────────────────────

    async def _handle_agent_message(
        self,
        agent_name: str,
        message: yuullm.Message | None,
    ) -> Agent | None:
        agent = await self._agent_by_name(agent_name)
        if agent is None:
            return None
        if message is not None:
            agent.append(message)
        await self._run_agent_turn(agent)
        return agent

    async def _handle_background_completed(
        self,
        message: BackgroundCompletedMessage,
    ) -> Agent | None:
        if message.agent_id:
            agent = self.agents.get(message.agent_id)
            if agent is not None:
                return await self._continue_agent(agent, message.content)
        if message.agent_name:
            return await self._handle_agent_message(message.agent_name, message.content)
        if len(self.agents) == 1:
            agent = next(iter(self.agents.values()))
            return await self._continue_agent(agent, message.content)
        if len(self.definitions) == 1:
            agent_name = next(iter(self.definitions))
            return await self._handle_agent_message(agent_name, message.content)
        await emit_actor_message_unhandled(
            self.stage.eventbus,
            message,
            {
                "agent_id": message.agent_id,
                "agent_name": message.agent_name,
                "task_id": message.task_id,
            },
        )
        return None

    async def _continue_agent(
        self,
        agent: Agent,
        message: yuullm.Message | None,
    ) -> Agent:
        if message is not None:
            agent.append(message)
        await self._run_agent_turn(agent)
        return agent

    # ── Agent registry ───────────────────────────────────────────

    async def _agent_by_name(self, agent_name: str) -> Agent | None:
        agent = self.agents_by_name.get(agent_name)
        if agent is not None:
            return agent
        definition = self.definitions.get(agent_name)
        if definition is None:
            await emit_actor_message_unhandled(
                self.stage.eventbus,
                ScheduleTriggerMessage,
                {"agent_name": agent_name},
            )
            return None
        agent = create_agent(self.stage, definition)
        self._init_agent_state(agent, definition)
        self._track_agent(agent)
        await emit_agent_started(self.stage.eventbus, agent, definition)
        return agent

    def _default_agent_name(self) -> str:
        if len(self.definitions) != 1:
            raise RuntimeError("agent name is required")
        return next(iter(self.definitions))

    def _agent_has_executor(self, agent: Agent, tool_name: str) -> bool:
        return tool_name in {"create_cron", "list_crons", "delete_cron"}

    # ── Agent turn / rollover ────────────────────────────────────

    async def _run_agent_turn(self, agent: Agent) -> None:
        """Execute one agent turn: LLM step → cost → tools → repeat until done.

        Custom orchestrator loop — not using yuuagents.run_agent_loop().
        The loop charges budget, executes tools via the new Runtime, and
        handles rollover when token limits are exceeded.
        """
        lock = self._agent_locks.setdefault(agent.id, asyncio.Lock())
        async with lock:
            async with self.stage.eventbus.scope(
                "agent.turn",
                {
                    "agent_id": agent.id,
                    "agent_name": agent.name,
                },
            ):
                budget = self._agent_budgets.get(agent.id)
                pricing = self.agent_pricings.get(agent.id)

                while not agent.done:
                    if budget is not None and budget.is_exceeded():
                        await emit_budget_exceeded(self.stage.eventbus, agent)
                        break

                    # Step 1: Emit LLM start event (trace observability)
                    await self.stage.eventbus.emit(
                        "llm.started",
                        {
                            "agent_id": agent.id,
                            "agent_name": agent.name,
                        },
                    )

                    # Step 2: Call LLM
                    message, store = await agent.step()

                    # Step 3: Calculate cost and charge budget
                    cost_value: yuullm.Cost | None = None
                    if store.usage is not None:
                        # Provider-reported cost takes precedence
                        if store.provider_cost is not None:
                            cost_value = yuullm.Cost(
                                input_cost=0.0,
                                output_cost=0.0,
                                total_cost=store.provider_cost,
                                source="provider",
                            )
                        elif pricing is not None:
                            cost_value = calculate_cost(
                                store.usage, pricing, agent.llm.model
                            )
                        if cost_value is not None and budget is not None:
                            budget.charge("usd", cost_value.total_cost)

                    # Step 4: Charge tokens
                    if store.usage is not None and budget is not None:
                        tokens = (store.usage.input_tokens or 0) + (
                            store.usage.output_tokens or 0
                        )
                        if tokens:
                            budget.charge("tokens", tokens)

                    # Step 5: Emit LLM finish event (trace observability)
                    await self.stage.eventbus.emit(
                        "llm.finished",
                        {
                            "agent_id": agent.id,
                            "agent_name": agent.name,
                            "usage": store.usage,
                            "cost": cost_value,
                            "model": agent.llm.model,
                            "message": message,
                        },
                    )

                    # Step 4: Execute tools
                    tools = _extract_tool_calls(message)
                    if tools:
                        new_tasks: list[tuple[yuullm.ToolCall, YuuTask]] = []
                        for tc in tools:
                            context = ToolContext(
                                agent_id=agent.id,
                                tool_call_id=tc.id,
                                eventbus=self.stage.eventbus,
                                entity_log=agent.log,
                            )
                            try:
                                yt = await self.stage.new_runtime.submit_tool_call(
                                    Owner(type=OwnerType.AGENT, id=agent.id),
                                    tc,
                                    context,
                                )
                                new_tasks.append((tc, yt))
                            except KeyError:
                                agent.append(
                                    yuullm.tool(tc.id, f"Tool {tc.name} is not available")
                                )

                        for tc, yt in new_tasks:
                            ct = await self.stage.new_runtime.wait_task(yt.id)
                            rt = _render_task_result(ct)
                            agent.append(yuullm.tool(tc.id, rt))

                    # Step 6: Charge step
                    if budget is not None:
                        budget.charge("steps", 1)

                await self._rollover_if_needed(agent, budget)
                self._touch_agent(agent)

    async def _rollover_if_needed(self, agent: Agent, budget: Budget | None) -> None:
        if not self.rollover_enabled or not _agent_needs_rollover(agent, budget):
            return
        summary = await self._summarize_agent_history(agent, budget)
        agent.replace_history(_compacted_history(agent.history, summary))
        new_budget = _reset_token_usage(agent, budget)
        if new_budget is not None:
            self._agent_budgets[agent.id] = new_budget

    async def _summarize_agent_history(
        self, agent: Agent, budget: Budget | None
    ) -> str:
        summary_history = _summary_history(agent.history, self.summarize_steps_span)
        summary_session = agent.llm.factory.create_session(summary_history)
        stream, store = await summary_session.stream(**agent.llm.options)
        parts: list[str] = []
        async for item in stream:
            match item:
                case yuullm.Response(item=response):
                    if response["type"] == "text":
                        parts.append(response["text"])
                case yuullm.ThinkingBlock():
                    pass
                case yuullm.ToolCall():
                    pass
                case _:
                    pass
        if store.usage and budget is not None:
            tokens = (store.usage.input_tokens or 0) + (store.usage.output_tokens or 0)
            if tokens:
                budget.charge("tokens", tokens)
        return "".join(parts).strip() or "No prior context."

    # ── Agent lifecycle management ───────────────────────────────

    def _init_agent_state(
        self,
        agent: Agent,
        definition: AgentDefinition,
    ) -> None:
        """Create budget from definition, link pricing, build tool specs."""
        budget = definition.budget.to_budget()
        self._agent_budgets[agent.id] = budget
        pricing = self.agent_pricings.pop(definition.name, None)
        if pricing is not None:
            self.agent_pricings[agent.id] = pricing

        tool_specs = _build_tool_specs_for_agent(self.stage)
        if tool_specs or definition.prompt.system:
            history: yuullm.History = []
            if tool_specs:
                history.append(yuullm.tools(tool_specs))
            if definition.prompt.system:
                history.append(yuullm.system(definition.prompt.system))
            existing = [
                m for m in agent.history
                if isinstance(m, yuullm.Message) and m.role != "system"
            ]
            if existing:
                agent.replace_history(history)
                for msg in existing:
                    agent.append(msg)
            else:
                agent.replace_history(history)

    def _track_agent(self, agent: Agent) -> None:
        self.agents[agent.id] = agent
        if agent.name:
            self.agents_by_name[agent.name] = agent
        self._touch_agent(agent)

    async def _untrack_agent(self, agent: Agent) -> None:
        self.agents.pop(agent.id, None)
        self._agent_budgets.pop(agent.id, None)
        self.agent_pricings.pop(agent.id, None)
        self._agent_locks.pop(agent.id, None)
        self._agent_last_used.pop(agent.id, None)
        self._idle_expiry_tasks.pop(agent.id, None)
        await self.stage.new_runtime.cancel_agent_tasks(agent.id)
        for name, item in list(self.agents_by_name.items()):
            if item is agent:
                self.agents_by_name.pop(name, None)
        for conversation_id, item in list(self.conversation_agents.items()):
            if item is agent:
                self.conversation_agents.pop(conversation_id, None)

    def _touch_agent(self, agent: Agent) -> None:
        self._agent_last_used[agent.id] = asyncio.get_running_loop().time()
        if self.idle_timeout_s <= 0:
            return
        task = self._idle_expiry_tasks.pop(agent.id, None)
        if task is not None:
            task.cancel()
        self._idle_expiry_tasks[agent.id] = asyncio.create_task(
            self._expire_agent_when_idle(agent.id)
        )

    async def _expire_agent_when_idle(self, agent_id: str) -> None:
        try:
            while True:
                last_used = self._agent_last_used.get(agent_id)
                if last_used is None:
                    return
                elapsed = asyncio.get_running_loop().time() - last_used
                remaining = self.idle_timeout_s - elapsed
                if remaining > 0:
                    await asyncio.sleep(remaining)
                    continue
                lock = self._agent_locks.get(agent_id)
                if lock is not None and lock.locked():
                    await asyncio.sleep(self.idle_timeout_s)
                    continue
                agent = self.agents.get(agent_id)
                if agent is None:
                    return
                await agent.close(status="expired")
                await self._untrack_agent(agent)
                return
        except asyncio.CancelledError:
            raise

    # ── Definition factories ─────────────────────────────────────

    def _conversation_definition(self, conversation_id: str) -> AgentDefinition:
        base = self.conversation_definition
        return AgentDefinition(
            name=f"{base.name}:conversation:{conversation_id}",
            llm=base.llm,
            budget=base.budget,
            tools=base.tools,
            prompt=base.prompt,
        )

    def _delegate_definition(
        self,
        task_id: str,
        parent_agent_name: str,
        delegate_name: str,
    ) -> AgentDefinition:
        if not self.definitions:
            raise RuntimeError("no actor agent definitions are registered")
        base = self.definitions.get(parent_agent_name) or next(
            iter(self.definitions.values())
        )
        suffix = delegate_name.strip() or task_id
        return AgentDefinition(
            name=f"{base.name}:delegate:{suffix}",
            llm=base.llm,
            budget=base.budget,
            tools=base.tools,
            prompt=base.prompt,
        )


# ── Module-level helpers ────────────────────────────────────────


def _extract_tool_calls(message: yuullm.Message) -> list[yuullm.ToolCall]:
    """Extract ToolCall structs from an assistant message's content.

    After agent.step() returns, the assistant message's content list
    contains tool_call items as dicts with 'type', 'id', 'name', and
    'arguments' keys. These are converted to yuullm.ToolCall structs
    for submission to the new Runtime.
    """
    result: list[yuullm.ToolCall] = []
    for item in message.content:
        if isinstance(item, dict) and item.get("type") == "tool_call":
            tc = typecast("dict[str, object]", item)
            result.append(
                yuullm.ToolCall(
                    id=str(tc["id"]),
                    name=str(tc["name"]),
                    arguments=str(tc["arguments"]),
                )
            )
    return result


def _render_task_result(task: YuuTask) -> str:
    """Render a completed tool Task's result as a text string."""
    if task.result is not None:
        return str(task.result)
    if task.error is not None:
        msg = f"[{task.error.type}] {task.error.message}"
        if task.error.traceback:
            msg += "\n" + "\n".join(task.error.traceback)
        return msg
    return "no result"


def _build_tool_specs_for_agent(stage: Stage) -> list[dict[str, object]]:
    """Build OpenAI-format tool specs from registered Runtime tools."""
    registry = stage.new_runtime.registry
    specs: list[dict[str, object]] = []
    for name, definition in registry._definitions.items():
        schema = definition.input_model.model_json_schema()
        specs.append({
            "type": "function",
            "function": {
                "name": definition.name,
                "description": definition.description,
                "parameters": schema,
            },
        })
    return specs
