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
from yuuagents.tool.primitives import ToolResult

from yuubot.core.costing import calculate_cost
from yuubot.resources.records import ModelConfig

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
    agent_model_configs: dict[str, dict[str, ModelConfig]] = field(
        default_factory=dict
    )
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

    def store_model_configs(
        self,
        agent_id: str,
        model_configs: dict[str, ModelConfig],
    ) -> None:
        """Store model configs for an agent (used by the orchestrator loop)."""
        self.agent_model_configs[agent_id] = model_configs

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
        if history:
            # Cache miss after restart / idle expiry: replace the freshly
            # built in-memory agent history with the persisted prefix.
            # create_agent() seeds [system_message] from
            # definition.prompt.system — that snapshot is the LIVE one
            # (mutated AGENTS.md / actor persona) and must not leak into the
            # resumed conversation. Restoration here only replays rows
            # already written to conversation_history_items at first send.
            self._init_agent_budget(agent, definition)
            agent.replace_history(list(history))
        else:
            # First send: build the prompt prefix (tools + system) from
            # the live binding snapshot. The prefix is persisted as
            # ordered history items by ConversationManager.send_message.
            self._init_agent_state(agent, definition)
        self.conversation_agents[conversation_id] = agent
        self._track_agent(agent)
        await emit_agent_started(self.stage.eventbus, agent, definition)
        return agent

    async def handle_conversation_message(
        self,
        conversation_id: str,
        message: yuullm.Message,
        cancel_event: asyncio.Event | None = None,
    ) -> Agent:
        agent = await self.ensure_conversation_agent(conversation_id, [])
        agent.append(message)
        await self._run_agent_turn(
            agent,
            cancel_event=cancel_event,
        )
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

    async def _run_agent_turn(
        self,
        agent: Agent,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        """Execute one agent turn: LLM step → cost → tools → repeat until done.

        ``cancel_event`` is the single-point safety net (checked once at the
        top of each loop iteration) so a ``task.cancel()`` scheduled between
        awaits still trips before the next LLM stream starts. The real cancel
        delivery is ``task.cancel()`` → ``CancelledError``.

        ``agent.turn_completed`` is emitted unconditionally by this method's
        terminal path — the loop's own exit (natural done OR a cancel break-
        out) — so ``cancel_turn`` does not synthesise it. Rollover only runs
        on natural completion (``while...else``), never on a cancel break-out.
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
                model_configs = self.agent_model_configs.get(agent.id)

                while not agent.done:
                    # Single-point safety net: ``task.cancel()`` schedules
                    # ``CancelledError`` delivery at the next await. If the
                    # loop is between awaits (e.g. just finished a
                    # synchronous step), the cancel may not land until the
                    # next LLM stream starts. This explicit check closes
                    # that window — raise into Stage A's handler below,
                    # which decides whether there's a new partial to
                    # finalise.
                    if cancel_event is not None and cancel_event.is_set():
                        raise asyncio.CancelledError

                    if budget is not None and budget.is_exceeded():
                        await emit_budget_exceeded(self.stage.eventbus, agent)
                        break

                    # ── Stage A: LLM step ───────────────────────────────
                    # The LLM stream is one natural interruptible unit of
                    # work; its own except handler finalises the partial
                    # assistant (emit ``llm.finished`` + flush reporter)
                    # when interrupted. The party that got interrupted is
                    # the party that signals — LLM stage owns ``llm.finished``.
                    last_emitted_assistant = _last_assistant_message(agent.history)
                    try:
                        # Step 1: Emit LLM start event (trace observability)
                        await self.stage.eventbus.emit(
                            "llm.started",
                            {
                                "agent_id": agent.id,
                                "agent_name": agent.name,
                            },
                        )

                        # Step 2: Call LLM  ← await #1: LLM stream
                        message, store = await agent.step()

                        # Step 3: Calculate cost and charge budget
                        cost_value: yuullm.Cost | None = None
                        if store.usage is not None:
                            if store.provider_cost is not None:
                                cost_value = yuullm.Cost(
                                    input_cost=0.0,
                                    output_cost=0.0,
                                    total_cost=store.provider_cost,
                                    source="provider",
                                )
                            elif model_configs is not None:
                                cost_value = calculate_cost(
                                    store.usage, model_configs, agent.llm.model
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
                        # ↑ normal-path emit; agent.history now has the FINAL
                        # assistant. Stage B's handler must NOT re-emit
                        # ``llm.finished`` — the LLM stage already signed off.
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
                    except asyncio.CancelledError:
                        # LLM stream was interrupted in flight (or the
                        # single-point ``cancel_event`` raised because no
                        # await was in flight). session.py already appended
                        # the partial assistant to agent.history; emit
                        # ``llm.finished`` so _handle_llm_finished persists
                        # it (mirrors opencode's "interrupt = just another
                        # terminal path"). The ``is not`` guard handles the
                        # between-awaits raise where step() hadn't produced
                        # anything new — nothing to finalise, just break.
                        partial = _last_assistant_message(agent.history)
                        if partial is not None and partial is not last_emitted_assistant:
                            await self.stage.eventbus.emit(
                                "llm.finished",
                                {
                                    "agent_id": agent.id,
                                    "agent_name": agent.name,
                                    "usage": None,
                                    "cost": None,
                                    "model": agent.llm.model,
                                    "message": partial,
                                },
                            )
                        await agent.flush_entitylog()
                        break

                    # ── Stage B: tool execution ────────────────────────
                    # The tool batch is the second natural interruptible
                    # unit of work; its own except handler calls
                    # ``_cancel_inflight_tool_calls`` (which synthesises
                    # ``[cancelled]`` tool_results + emits
                    # ``tool.result_appended``). The LLM stage already
                    # emitted ``llm.finished`` naturally above — do NOT
                    # re-emit it here (Phase 4 side note #1 fix).
                    tools = _extract_tool_calls(message)
                    if tools:
                        try:
                            new_tasks: list[tuple[yuullm.ToolCall, YuuTask]] = []
                            for tc in tools:
                                context = ToolContext(
                                    agent_id=agent.id,
                                    tool_call_id=tc.id,
                                    eventbus=self.stage.eventbus,
                                    entity_log=agent.log,
                                )
                                try:
                                    yt = await self.stage.runtime.submit_tool_call(
                                        Owner(type=OwnerType.AGENT, id=agent.id),
                                        tc,
                                        context,
                                    )
                                    new_tasks.append((tc, yt))
                                except KeyError:
                                    error_msg = f"Tool {tc.name} is not available"
                                    agent.append(yuullm.tool(tc.id, error_msg))
                                    await self.stage.eventbus.emit(
                                        "tool.result_appended",
                                        {
                                            "agent_id": agent.id,
                                            "agent_name": agent.name,
                                            "tool_call_id": tc.id,
                                            "tool_name": tc.name,
                                            "result": error_msg,
                                            "task_id": "",
                                            "status": "failed",
                                        },
                                    )

                            for tc, yt in new_tasks:
                                # ← await #2: tool execution
                                ct = await self.stage.runtime.wait_task(yt.id)
                                rt = _render_task_result(ct)
                                agent.append(yuullm.tool(tc.id, rt))
                                await self.stage.eventbus.emit(
                                    "tool.result_appended",
                                    {
                                        "agent_id": agent.id,
                                        "agent_name": agent.name,
                                        "tool_call_id": tc.id,
                                        "tool_name": tc.name,
                                        "result": rt,
                                        "task_id": yt.id,
                                        "status": str(ct.status),
                                    },
                                )
                        except asyncio.CancelledError:
                            # Tool execution was interrupted. Synthesize
                            # ``[cancelled]`` tool_results for any tool_calls
                            # without results (and emit ``tool.result_appended``
                            # so the frontend + DB see them). The LLM message
                            # was already naturally emitted in Stage A — do
                            # NOT re-emit ``llm.finished`` here.
                            await self._cancel_inflight_tool_calls(agent)
                            break

                    # Step 7: Charge step
                    if budget is not None:
                        budget.charge("steps", 1)
                else:
                    # while...else: loop exited via ``not agent.done`` (the
                    # natural completion path). Rollover only runs here, NOT
                    # on a break-out from either CancelledError handler (a
                    # pure stop ends the turn; no rollover needed for a dead
                    # turn).
                    await self._rollover_if_needed(agent, budget)

                self._touch_agent(agent)

                # ``agent.turn_completed`` is the sole turn-end signal. The
                # natural-done path emits it directly; the cancel break-out
                # also reaches here and emits it (the turn is genuinely
                # over). ``cancel_turn`` awaits the cancelled task, so this
                # emit lands before the HTTP "stop receipt" returns.
                await self.stage.eventbus.emit(
                    "agent.turn_completed",
                    {
                        "agent_id": agent.id,
                        "agent_name": agent.name,
                    },
                )

    async def _cancel_inflight_tool_calls(self, agent: Agent) -> None:
        """Cancel running tool tasks and synthesize ``[cancelled]`` results.

        Called from Stage B's (tool-execution) CancelledError handler. After
        this, the in-memory agent history is legal: every tool_call in the
        last assistant message has a matching tool_result. Since
        ``send_message`` prioritises the in-memory agent cache, subsequent
        turns see the correct history. Only "cancel then immediate power
        loss" loses this — accepted.

        For each synthesised ``[cancelled]`` result, emits
        ``tool.result_appended`` so the Stage B (tool-execution) segment
        signs off its own terminal events (the party that got interrupted
        is the party that signals) — the assistant message was already
        signed off naturally by Stage A, so this does NOT re-emit
        ``llm.finished``.
        """
        # Cancel any still-running tool tasks (bash subprocess, python
        # kernel, file I/O, …). yuuagents.Runtime.cancel_agent_tasks
        # calls task.cancel() on each run_task and finalises its Task
        # record with status=CANCELLED.
        await self.stage.runtime.cancel_agent_tasks(agent.id)

        # Synthesize tool results for any tool calls without results.
        history = agent.history
        last_assistant_idx: int | None = None
        for i in range(len(history) - 1, -1, -1):
            item = history[i]
            if isinstance(item, yuullm.Message) and item.role == "assistant":
                last_assistant_idx = i
                break
        if last_assistant_idx is None:
            return
        assistant_item = history[last_assistant_idx]
        if not isinstance(assistant_item, yuullm.Message):
            return
        assistant_msg = assistant_item
        tool_calls = _extract_tool_calls(assistant_msg)
        if not tool_calls:
            return
        existing_results: set[str] = set()
        for j in range(last_assistant_idx + 1, len(history)):
            item = history[j]
            if isinstance(item, yuullm.Message) and item.role == "tool":
                for content_item in item.content:
                    if (
                        isinstance(content_item, dict)
                        and content_item.get("type") == "tool_result"
                    ):
                        tc_id = content_item.get("tool_call_id")
                        if isinstance(tc_id, str):
                            existing_results.add(tc_id)
        for tc in tool_calls:
            if tc.id not in existing_results:
                cancelled_result = "[cancelled by user]"
                agent.append(yuullm.tool(tc.id, cancelled_result))
                await self.stage.eventbus.emit(
                    "tool.result_appended",
                    {
                        "agent_id": agent.id,
                        "agent_name": agent.name,
                        "tool_call_id": tc.id,
                        "tool_name": tc.name,
                        "result": cancelled_result,
                        "task_id": "",
                        "status": "cancelled",
                    },
                )

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
        """Full non-conversation actor state init: budget + prompt prefix.

        Used by the delegate / schedule-trigger code paths, which always
        start with an empty agent and need the freshly-snapshotted prefix.
        The conversation path uses :meth:`_init_agent_budget` and
        :meth:`_init_agent_prompt_prefix` separately so the restart branch
        can replay persisted history while still setting up budget/pricing.
        """
        self._init_agent_budget(agent, definition)
        self._init_agent_prompt_prefix(agent, definition)

    def _init_agent_budget(
        self,
        agent: Agent,
        definition: AgentDefinition,
    ) -> None:
        """Materialize the budget for ``agent`` and link its model configs.

        ``agent_model_configs`` is staged at construction time keyed by
        ``self.conversation_definition.name`` (the IM-mode definition name).
        ``definition`` here is a *derived* definition (per-conversation or
        per-delegate) whose ``.name`` carries an identifying suffix
        (``:conversation:{id}`` / ``:delegate:{name}``) — using that key
        would miss the staged entry. Pop from the base definition name
        instead so the model config map is rehomed to ``agent.id`` for the
        run-time budget lookup.
        """
        budget = definition.budget.to_budget()
        self._agent_budgets[agent.id] = budget
        model_configs = self.agent_model_configs.pop(
            self.conversation_definition.name,
            None,
        )
        if model_configs is not None:
            self.agent_model_configs[agent.id] = model_configs

    def _init_agent_prompt_prefix(
        self,
        agent: Agent,
        definition: AgentDefinition,
    ) -> None:
        """Build the model-visible prompt prefix (tool specs + system message).

        Replaces ``agent.history`` with the freshly-snapshotted prefix,
        preserving any pre-existing non-system Message items (none on the
        first-send path — the manager appends the user Message afterwards).
        """
        tool_specs = _build_tool_specs_for_agent(self.stage)
        prefix: yuullm.History = []
        if tool_specs:
            prefix.append(yuullm.tools(tool_specs))
        if definition.prompt.system:
            prefix.append(yuullm.system(definition.prompt.system))
        if not prefix:
            return
        existing = [
            m for m in agent.history
            if isinstance(m, yuullm.Message) and m.role != "system"
        ]
        if existing:
            agent.replace_history(prefix)
            for msg in existing:
                agent.append(msg)
        else:
            agent.replace_history(prefix)

    def _track_agent(self, agent: Agent) -> None:
        self.agents[agent.id] = agent
        if agent.name:
            self.agents_by_name[agent.name] = agent
        self._touch_agent(agent)

    async def _untrack_agent(self, agent: Agent) -> None:
        self.agents.pop(agent.id, None)
        self._agent_budgets.pop(agent.id, None)
        self.agent_model_configs.pop(agent.id, None)
        self._agent_locks.pop(agent.id, None)
        self._agent_last_used.pop(agent.id, None)
        self._idle_expiry_tasks.pop(agent.id, None)
        await self.stage.runtime.cancel_agent_tasks(agent.id)
        for name, item in list(self.agents_by_name.items()):
            if item is agent:
                self.agents_by_name.pop(name, None)
        for conversation_id, item in list(self.conversation_agents.items()):
            if item is agent:
                self.conversation_agents.pop(conversation_id, None)

    # ── Budget accessors ──────────────────────────────────────────
    # Read-only lookups for the host app. The charging / is_exceeded
    # logic itself stays inside ``_run_agent_turn`` (frozen); these only
    # expose the already-maintained ``_agent_budgets`` entries so the
    # ConversationManager can publish realtime cost SSE events.

    def budget_for_agent(self, agent_id: str) -> Budget | None:
        """Return the in-memory ``Budget`` for ``agent_id``, or ``None``.

        Used by the host (yuubot ConversationManager) to read the running
        cumulative USD spend when projecting a ``cost_update`` SSE event
        after each ``llm.finished``. Read-only — never charges.
        """
        return self._agent_budgets.get(agent_id)

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


def _last_assistant_message(history: yuullm.History) -> yuullm.Message | None:
    """Return the most recent assistant Message in ``history``, or None.

    Used by the CancelledError handler to locate the partial assistant
    yuullm already appended to agent.history so it can be emitted via
    ``llm.finished`` and persisted by ``ConversationManager``.
    """
    for item in reversed(history):
        if isinstance(item, yuullm.Message) and item.role == "assistant":
            return item
    return None


def _render_task_result(task: YuuTask) -> ToolResult:
    """Render a completed tool Task's result."""
    if task.result is not None:
        if isinstance(task.result, str):
            return task.result
        if isinstance(task.result, list):
            return task.result
        return str(task.result)
    if task.error is not None:
        msg = f"[{task.error.type}] {task.error.message}"
        if task.error.traceback:
            msg += "\n" + "\n".join(task.error.traceback)
        return msg
    return "no result"


def _build_tool_specs_for_agent(stage: Stage) -> list[dict[str, object]]:
    """Build OpenAI-format tool specs from registered Runtime tools."""
    registry = stage.runtime.registry
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
