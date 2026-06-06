"""Start yuuagents actors from yuubot core bindings."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import msgspec
import yuullm

from yuuagents import (
    AgentDefinition,
    BackgroundCompletedMessage,
    Budget,
    EventBus,
    LlmConfig,
    MailBox,
    MailMessage,
    PromptDefinition,
    PythonImport,
    PythonKernelConfig,
    ScheduleTriggerMessage,
    Stage,
    StageConfig,
    ToolDefinition,
    ToolSpecConfig,
    YuuTraceObserver,
    close_actor_resources,
    create_agent,
    emit_actor_message_received,
    emit_actor_message_unhandled,
    emit_agent_started,
    run_agent_loop,
)
from yuuagents.agent import Agent, LlmClient
from yuuagents.llm_providers import LlmProviderConfig
from yuuagents.tool_backends.ipykernel import PythonToolConfig

from yuubot.bootstrap.config import YuuAgentsConfig
from yuubot.core.bindings import ActorBinding
from yuubot.core.costing import PricingAwareLlmClient
from yuubot.core.facade import ActorFacadeBinding, facade_module_name
from yuubot.core.observability import YuubotTraceContextProvider
from yuubot.core.validation import (
    ConfigurationError,
    validate_provider_options,
    validate_stream_options,
)
from yuubot.resources.records import ToolConfig

PYTHON_PROVIDER_KEY = "ipykernel"
ROLLOVER_THRESHOLD = 0.85
ROLLOVER_SUMMARY_PROMPT = (
    "Summarize the prior conversation context for continuing the same task. "
    "Preserve user goals, important facts, decisions, open work, tool results, "
    "and any constraints. Return only the summary."
)
IM_MODE_SYSTEM_GUIDANCE = (
    "Yuubot IM mode: incoming mailbox messages are inputs, not function calls. "
    "For user-visible replies, call await yb.im.respond(\"...\"). "
    "For quick acknowledgements, call await yb.im.react(\"working\"). "
    "Plain assistant text is internal and is not delivered to the IM user."
)
FACADE_IMPORTS = (
    PythonImport(module="yb"),
    PythonImport(module="yb.actor"),
    PythonImport(module="yb.admin"),
    PythonImport(module="yb.delegate"),
    PythonImport(module="yb.im"),
    PythonImport(module="yb.schedule"),
    PythonImport(module="yb.tasks"),
    PythonImport(module="yext"),
)
FACADE_EXPAND_FUNCTIONS = (
    "yb.*",
    "yb.admin.*",
    "yb.actor.*",
    "yb.delegate.*",
    "yb.im.*",
    "yb.schedule.*",
    "yb.tasks.*",
    "yext.*",
)


_YUUAGENTS_KNOWN_FACTORIES: frozenset[str] = frozenset({"openai", "anthropic", "openrouter"})

def _resolve_yuuagents_provider(yuuagents_provider: str) -> str:
    """Map a provider name to the yuuagents LLM factory name.

    Known yuuagents factory names pass through directly.  Any other value
    (vendor names like ``"deepseek"``, ``"groq"``, etc.) resolves to
    ``"openai"`` since those vendors use the OpenAI-compatible wire protocol.
    """
    if yuuagents_provider in _YUUAGENTS_KNOWN_FACTORIES:
        return yuuagents_provider
    return "openai"


def start_yuuagents_actor(
    binding: ActorBinding,
    *,
    yuuagents_config: YuuAgentsConfig,
    facade: ActorFacadeBinding | None = None,
    mailbox: MailBox | None = None,
    eventbus: EventBus | None = None,
    llm_client: LlmClient | None = None,
    trace_context: YuubotTraceContextProvider | None = None,
) -> "YuuAgentsActorRuntime":
    _check_pricing_for_budget(binding)
    llm_provider = _resolve_yuuagents_provider(binding.llm.backend.yuuagents_provider)
    stage = Stage.from_config(
        StageConfig(
            strict=yuuagents_config.strict,
            tool_backends=_stage_tool_backend_config(
                yuuagents_config,
                binding=binding,
                facade=facade,
            ),
            llms={}
            if llm_client is not None
            else {llm_provider: _stage_llm_config(binding)},
        ),
        mailbox=mailbox,
        eventbus=eventbus,
    )
    if llm_client is not None:
        stage.llm_clients[llm_provider] = llm_client
        stage.llm_options[llm_provider] = _stage_llm_options(binding)
    stage.llm_clients[llm_provider] = PricingAwareLlmClient(
        inner=stage.llm_clients[llm_provider],
        pricing=binding.llm.backend.pricing,
        configured_model=binding.llm.model,
    )
    definition = build_agent_definition(binding, facade=facade, mode="im")
    conversation_definition = build_agent_definition(
        binding,
        facade=facade,
        mode="conversation",
    )
    if trace_context is not None:
        stage.eventbus.subscribe(YuuTraceObserver(context_provider=trace_context))
    return YuuAgentsActorRuntime(
        stage=stage,
        definitions={definition.name: definition},
        conversation_definition=conversation_definition,
        rollover_enabled=binding.actor.runtime_policy.rollover_enabled,
        idle_timeout_s=binding.actor.runtime_policy.idle_timeout_s,
        summarize_steps_span=binding.actor.runtime_policy.summarize_steps_span,
    )


@dataclass
class YuuAgentsActorRuntime:
    stage: Stage
    definitions: dict[str, AgentDefinition]
    conversation_definition: AgentDefinition
    rollover_enabled: bool = False
    idle_timeout_s: float = 0.0
    summarize_steps_span: int = 20
    agents: dict[str, Agent] = field(default_factory=dict)
    agents_by_name: dict[str, Agent] = field(default_factory=dict)
    conversation_agents: dict[str, Agent] = field(default_factory=dict)
    _agent_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    _agent_last_used: dict[str, float] = field(default_factory=dict)
    _idle_expiry_tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict)

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
        agent.append_message(message)
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
        self._track_agent(agent)
        await emit_agent_started(self.stage.eventbus, agent, definition)
        try:
            agent.append_message(yuullm.user(prompt))
            await self._run_agent_turn(agent)
            return _last_assistant_text(agent)
        finally:
            await agent.close(status="completed")
            self._untrack_agent(agent)

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
        if not self._agent_has_executor(agent, tool_name):
            raise RuntimeError("schedule tool is not enabled for this actor")
        task = self.stage.runtime.submit(
            agent.agent_id,
            yuullm.ToolCall(
                id=f"schedule-{tool_name}",
                name=tool_name,
                arguments=json.dumps(payload, ensure_ascii=True),
            ),
            agent.budget,
            timeout=15.0,
        )
        return await task.wait_with_error_handling()

    async def _handle_agent_message(
        self,
        agent_name: str,
        message: yuullm.Message | None,
    ) -> Agent | None:
        agent = await self._agent_by_name(agent_name)
        if agent is None:
            return None
        if message is not None:
            agent.append_message(message)
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
            agent_id=message.agent_id,
            agent_name=message.agent_name,
            task_id=message.task_id,
        )
        return None

    async def _continue_agent(
        self,
        agent: Agent,
        message: yuullm.Message | None,
    ) -> Agent:
        if message is not None:
            agent.append_message(message)
        await self._run_agent_turn(agent)
        return agent

    async def _agent_by_name(self, agent_name: str) -> Agent | None:
        agent = self.agents_by_name.get(agent_name)
        if agent is not None:
            return agent
        definition = self.definitions.get(agent_name)
        if definition is None:
            await emit_actor_message_unhandled(
                self.stage.eventbus,
                ScheduleTriggerMessage,
                agent_name=agent_name,
            )
            return None
        agent = create_agent(self.stage, definition)
        self._track_agent(agent)
        await emit_agent_started(self.stage.eventbus, agent, definition)
        return agent

    def _default_agent_name(self) -> str:
        if len(self.definitions) != 1:
            raise RuntimeError("agent name is required")
        return next(iter(self.definitions))

    def _agent_has_executor(self, agent: Agent, tool_name: str) -> bool:
        return any(
            tool_name in executor
            for executor in self.stage.runtime.agent2executors.get(agent.agent_id, [])
        )

    async def _run_agent_turn(self, agent: Agent) -> None:
        lock = self._agent_locks.setdefault(agent.agent_id, asyncio.Lock())
        async with lock:
            async with self.stage.eventbus.scope(
                "agent.turn",
                {
                    "agent_id": agent.agent_id,
                    "agent_name": agent.agent_name,
                },
            ):
                await run_agent_loop(agent, self.stage.eventbus)
                await self._rollover_if_needed(agent)
                self._touch_agent(agent)

    def _track_agent(self, agent: Agent) -> None:
        self.agents[agent.agent_id] = agent
        if agent.agent_name:
            self.agents_by_name[agent.agent_name] = agent
        self._touch_agent(agent)

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

    async def _rollover_if_needed(self, agent: Agent) -> None:
        if not self.rollover_enabled or not _agent_needs_rollover(agent):
            return
        summary = await self._summarize_agent_history(agent)
        agent.replace_history(_compacted_history(agent.history, summary))
        _reset_token_usage(agent)

    async def _summarize_agent_history(self, agent: Agent) -> str:
        summary_history = _summary_history(agent.history, self.summarize_steps_span)
        stream, store = await agent.llm.stream(summary_history, **agent.llm_options)
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
        if store.usage:
            tokens = store.usage.input_tokens + store.usage.output_tokens
            if tokens:
                agent.budget.charge("tokens", tokens)
        return "".join(parts).strip() or "No prior context."

    def _touch_agent(self, agent: Agent) -> None:
        self._agent_last_used[agent.agent_id] = asyncio.get_running_loop().time()
        if self.idle_timeout_s <= 0:
            return
        task = self._idle_expiry_tasks.pop(agent.agent_id, None)
        if task is not None:
            task.cancel()
        self._idle_expiry_tasks[agent.agent_id] = asyncio.create_task(
            self._expire_agent_when_idle(agent.agent_id)
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
                self._untrack_agent(agent)
                return
        except asyncio.CancelledError:
            raise

    def _untrack_agent(self, agent: Agent) -> None:
        self.agents.pop(agent.agent_id, None)
        self._agent_locks.pop(agent.agent_id, None)
        self._agent_last_used.pop(agent.agent_id, None)
        self._idle_expiry_tasks.pop(agent.agent_id, None)
        for name, item in list(self.agents_by_name.items()):
            if item is agent:
                self.agents_by_name.pop(name, None)
        for conversation_id, item in list(self.conversation_agents.items()):
            if item is agent:
                self.conversation_agents.pop(conversation_id, None)


def build_agent_definition(
    binding: ActorBinding,
    *,
    facade: ActorFacadeBinding | None = None,
    mode: Literal["im", "conversation"] = "im",
) -> AgentDefinition:
    actor = binding.actor
    return AgentDefinition(
        name=actor.name,
        llm=LlmConfig(
            provider=_resolve_yuuagents_provider(binding.llm.backend.yuuagents_provider),
            model=binding.llm.model,
            max_tokens=actor.llm_options.max_tokens,
            stream_options=msgspec.to_builtins(actor.llm_options.stream_options),
        ),
        budget=actor.budget.to_budget_config(),
        tools=_agent_tool_configs(actor.agent_tools, facade),
        prompt=PromptDefinition(
            system=_system_prompt(binding.character.system_prompt, mode),
        ),
    )


def _system_prompt(
    character_prompt: str,
    mode: Literal["im", "conversation"],
) -> str:
    if mode == "conversation":
        return character_prompt
    if not character_prompt:
        return IM_MODE_SYSTEM_GUIDANCE
    return f"{character_prompt}\n\n{IM_MODE_SYSTEM_GUIDANCE}"


def _agent_needs_rollover(agent: Agent) -> bool:
    token_limit = agent.budget.limits.get("tokens", 0.0)
    if token_limit <= 0:
        return False
    return agent.budget.usage.get("tokens", 0.0) >= token_limit * ROLLOVER_THRESHOLD


def _summary_history(history: yuullm.History, summarize_steps_span: int) -> yuullm.History:
    messages, _tool_specs = yuullm.split_history(history)
    system_messages = [message for message in messages if message.role == "system"]
    tail_messages = [
        message for message in messages if message.role != "system"
    ][-_positive_span(summarize_steps_span):]
    return [
        *system_messages,
        *tail_messages,
        yuullm.user(ROLLOVER_SUMMARY_PROMPT),
    ]


def _compacted_history(history: yuullm.History, summary: str) -> yuullm.History:
    messages, tool_specs = yuullm.split_history(history)
    result: yuullm.History = []
    if tool_specs is not None:
        result.append(yuullm.tools(tool_specs))
    result.extend(message for message in messages if message.role == "system")
    result.append(
        yuullm.user(
            "The previous context was compacted. Continue from this summary:\n\n"
            f"{summary}"
        )
    )
    return result


def _positive_span(value: int) -> int:
    return value if value > 0 else 20


def _reset_token_usage(agent: Agent) -> None:
    """Reset token usage by replacing the budget with a fresh instance.

    Budget.usage returns a copy, so mutation through the public API is not
    possible. Instead, create a new Budget with the same limits — the new
    instance starts with empty _usage.
    """
    agent.budget = Budget(limits=agent.budget.limits)


def _last_assistant_text(agent: Agent) -> str:
    messages, _tool_specs = yuullm.split_history(agent.history)
    for message in reversed(messages):
        if message.role != "assistant":
            continue
        text = yuullm.render_message_text(message).strip()
        if text:
            return text
    return ""


def _stage_llm_config(binding: ActorBinding) -> dict[str, object]:
    return msgspec.to_builtins(
        LlmProviderConfig(
            default_model=binding.llm.model,
            provider_options=_stage_provider_options(binding),
            stream_options=_stage_llm_options(binding),
        )
    )


def _stage_provider_options(binding: ActorBinding) -> dict[str, object]:
    backend = binding.llm.backend
    return validate_provider_options(
        msgspec.to_builtins(backend.provider_options),
        context=f"llm_backend[{backend.name}].provider_options",
    )


def _stage_llm_options(binding: ActorBinding) -> dict[str, object]:
    backend = binding.llm.backend
    return validate_stream_options(
        msgspec.to_builtins(backend.default_stream_options),
        context=f"llm_backend[{backend.name}].default_stream_options",
    )


def _stage_tool_backend_config(
    yuuagents_config: YuuAgentsConfig,
    *,
    binding: ActorBinding,
    facade: ActorFacadeBinding | None,
) -> dict[str, Any]:
    tool_backends = {
        key: dict(value) for key, value in yuuagents_config.tool_backends.items()
    }
    if facade is not None:
        tool_backends[PYTHON_PROVIDER_KEY] = _python_tool_backend_config(
            tool_backends.get(PYTHON_PROVIDER_KEY),
            binding=binding,
            facade=facade,
        )
    return tool_backends


def _tool_definition_configs(
    configs: Iterable[ToolConfig],
) -> dict[str, ToolDefinition]:
    return {
        item.provider_key: ToolDefinition(config=dict(item.config), spec=item.spec)
        for item in configs
    }


def _agent_tool_configs(
    configs: Iterable[ToolConfig],
    facade: ActorFacadeBinding | None,
) -> dict[str, ToolDefinition]:
    result = _tool_definition_configs(configs)
    if facade is not None:
        result[PYTHON_PROVIDER_KEY] = _python_agent_tool_config(
            result.get(PYTHON_PROVIDER_KEY),
            facade,
        )
    return result


def _python_tool_backend_config(
    existing: object,
    *,
    binding: ActorBinding,
    facade: ActorFacadeBinding,
) -> dict[str, Any]:
    base = msgspec.convert(
        existing if isinstance(existing, Mapping) else {},
        type=PythonKernelConfig,
        strict=False,
    )
    return msgspec.to_builtins(
        PythonKernelConfig(
            python=base.python,
            cwd=str(binding.require_workspace_path()),
            inherit_envs=base.inherit_envs,
            env_allowlist=base.env_allowlist,
            extra_envs=base.extra_envs,
            sys_path=tuple(facade.sys_path),
            startup_code=_merged_startup_code(base.startup_code, facade.startup_code),
        )
    )


def _python_agent_tool_config(
    existing: ToolDefinition | None,
    facade: ActorFacadeBinding,
) -> ToolDefinition:
    tool = existing or ToolDefinition(spec=ToolSpecConfig(level="summary"))
    return ToolDefinition(
        config=msgspec.to_builtins(_python_tool_config(tool.config, facade)),
        spec=tool.spec,
    )


def _python_tool_config(
    raw: Mapping[str, Any],
    facade: ActorFacadeBinding,
) -> PythonToolConfig:
    base = msgspec.convert(raw, type=PythonToolConfig, strict=False)
    return PythonToolConfig(
        config=base.config,
        imports=_merged_imports(base.imports, _facade_imports(facade)),
        state=_python_session_state(base.state, facade),
        expand_functions=_merged_str_sequence(
            base.expand_functions,
            _facade_expand_functions(facade),
        ),
    )


def _facade_imports(facade: ActorFacadeBinding) -> tuple[PythonImport, ...]:
    modules = {
        facade_module_name(capability)
        for capability in facade.capabilities
    }
    return (
        *FACADE_IMPORTS,
        *(PythonImport(module=module) for module in sorted(modules) if module != "yext"),
    )


def _facade_expand_functions(facade: ActorFacadeBinding) -> tuple[str, ...]:
    modules = {
        facade_module_name(capability)
        for capability in facade.capabilities
    }
    return (
        *FACADE_EXPAND_FUNCTIONS,
        *(f"{module}.*" for module in sorted(modules) if module != "yext"),
    )


def _python_session_state(
    state: dict[str, Any],
    facade: ActorFacadeBinding,
) -> dict[str, Any]:
    result = dict(state)
    result.setdefault("actor_id", facade.actor_id)
    result.setdefault("agent_name", facade.agent_name)
    result.setdefault("session_id", facade.session_id)
    result.setdefault("mailbox_id", facade.mailbox_id)
    return result


def _merged_imports(
    existing: tuple[PythonImport, ...],
    required_imports: tuple[PythonImport, ...],
) -> tuple[PythonImport, ...]:
    imports = list(existing)
    existing_modules = {item.module for item in imports}
    for required_import in required_imports:
        if required_import.module not in existing_modules:
            imports.append(required_import)
    return tuple(imports)


def _merged_str_sequence(
    existing: tuple[str, ...] | None,
    required: tuple[str, ...],
) -> tuple[str, ...]:
    values = list(existing or ())
    for item in required:
        if item not in values:
            values.append(item)
    return tuple(values)


def _merged_startup_code(existing: str, required: str) -> str:
    if not existing:
        return required
    if required in existing:
        return existing
    return f"{existing}\n{required}"


def _check_pricing_for_budget(binding: ActorBinding) -> None:
    if not _requires_pricing(binding):
        return
    model = binding.llm.model
    for entry in binding.llm.backend.pricing.entries:
        if entry.model == model:
            return
    raise ConfigurationError(
        f"actor {binding.actor.name!r}: USD budget requires pricing for "
        f"model {model!r} in backend {binding.llm.backend.name!r}"
    )


def _requires_pricing(binding: ActorBinding) -> bool:
    backend_budget = binding.llm.backend.budget
    return (
        binding.actor.budget.max_usd > 0
        or _positive_budget(backend_budget.daily_usd)
        or _positive_budget(backend_budget.monthly_usd)
    )


def _positive_budget(value: float | None) -> bool:
    return value is not None and value > 0
