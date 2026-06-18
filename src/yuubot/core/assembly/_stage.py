"""Stage construction and actor startup.

Assembles a yuuagents Stage from AgentBinding and creates the
YuuAgentsActorRuntime that owns the actor lifecycle.
"""

from __future__ import annotations

import msgspec
from yuuagents import (
    EventBus,
    MailBox,
    ProviderPoolSessionFactory,
    Stage,
    YuuTraceObserver,
)
from yuuagents.tool.primitives import resolve_tool_type

from yuubot.bootstrap.config import YuuAgentsConfig
from yuubot.core.bindings import AgentBinding
from yuubot.core.facade import ActorFacadeBinding
from yuubot.core.observability import YuubotTraceContextProvider
from yuubot.core.tools import ToolRegistry
from yuubot.core.validation import (
    ConfigurationError,
    validate_stream_options,
)

from ._constants import _resolve_yuuagents_provider
from ._definition import build_agent_definition
from ._runtime import YuuAgentsActorRuntime


def start_yuuagents_actor(
    binding: AgentBinding,
    *,
    yuuagents_config: YuuAgentsConfig,
    facade: ActorFacadeBinding | None = None,
    mailbox: MailBox | None = None,
    eventbus: EventBus | None = None,
    llm_session_factory: ProviderPoolSessionFactory | None = None,
    trace_context: YuubotTraceContextProvider | None = None,
) -> YuuAgentsActorRuntime:
    llm_provider = _resolve_yuuagents_provider(binding.llm.backend.yuuagents_provider)
    if llm_session_factory is None:
        raise ConfigurationError(
            f"agent {binding.agent_name!r}: no LLM session factory configured "
            f"for provider {llm_provider!r}"
        )
    llm_session_factory = llm_session_factory.with_selector(binding.llm.model)
    stage = Stage.from_config(
        mailbox=mailbox,
        eventbus=eventbus,
        llm_session_factories={llm_provider: llm_session_factory},
        llm_options={llm_provider: _stage_llm_options(binding)},
    )

    workspace_path = _get_workspace_path(binding, facade)

    definition = build_agent_definition(
        binding, facade=facade, mode="im", workspace_path=workspace_path,
    )
    conversation_definition = build_agent_definition(
        binding,
        facade=facade,
        mode="conversation",
        workspace_path=workspace_path,
    )

    _register_tools(stage, definition)

    if trace_context is not None:
        stage.eventbus.subscribe(YuuTraceObserver(context_provider=trace_context))
    runtime = YuuAgentsActorRuntime(
        stage=stage,
        definitions={definition.name: definition},
        conversation_definition=conversation_definition,
        rollover_enabled=binding.capability_set.runtime_policy.rollover_enabled,
        idle_timeout_s=binding.capability_set.runtime_policy.idle_timeout_s,
        summarize_steps_span=binding.capability_set.runtime_policy.summarize_steps_span,
        agent_pricings={definition.name: binding.llm.backend.pricing},
    )
    return runtime


def _stage_llm_options(binding: AgentBinding) -> dict[str, object]:
    backend = binding.llm.backend
    return validate_stream_options(
        msgspec.to_builtins(backend.default_stream_options),
        context=f"llm_backend[{backend.name}].default_stream_options",
    )


def _get_workspace_path(
    binding: AgentBinding,
    facade: ActorFacadeBinding | None,
) -> str | None:
    if facade is None:
        return None
    return str(binding.require_workspace_path())


def _register_tools(stage: Stage, definition: any) -> None:
    """Register tool instances from an agent definition into the runtime registry."""
    from yuubot.core.assembly._tools import _tool_registry

    for tool_name, raw_config in definition.tools.items():
        if _tool_registry is not None:
            tool_cls = _tool_registry.tool_class(tool_name)
        else:
            tool_cls = resolve_tool_type(tool_name)
        typed_config = msgspec.convert(raw_config, tool_cls.config_type)
        tool = tool_cls.from_startup(stage.runtime, typed_config)
        stage.runtime.registry.register(tool.definition, tool)


# ── Pricing validation ───────────────────────────────────────────


def _check_pricing_for_budget(binding: AgentBinding) -> None:
    if not _requires_pricing(binding):
        return
    model = binding.llm.model
    for entry in binding.llm.backend.pricing.entries:
        if entry.model == model:
            return
    raise ConfigurationError(
        f"agent {binding.agent_name!r}: USD budget requires pricing for "
        f"model {model!r} in backend {binding.llm.backend.name!r}"
    )


def _requires_pricing(binding: AgentBinding) -> bool:
    backend_budget = binding.llm.backend.budget
    return (
        binding.budget.max_usd > 0
        or _positive_budget(backend_budget.daily_usd)
        or _positive_budget(backend_budget.monthly_usd)
    )


def _positive_budget(value: float | None) -> bool:
    return value is not None and value > 0
