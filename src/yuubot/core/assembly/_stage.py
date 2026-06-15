"""Stage construction and actor startup.

Assembles a yuuagents Stage from ActorBinding and creates the
YuuAgentsActorRuntime that owns the actor lifecycle.
"""

from __future__ import annotations

import msgspec
from yuuagents import (
    EventBus,
    MailBox,
    Stage,
    StageConfig,
    YuuTraceObserver,
)
from yuuagents.llm_session import ProviderPoolSessionFactory

from yuubot.bootstrap.config import YuuAgentsConfig
from yuubot.core.bindings import ActorBinding
from yuubot.core.facade import ActorFacadeBinding
from yuubot.core.observability import YuubotTraceContextProvider
from yuubot.core.validation import (
    ConfigurationError,
    validate_stream_options,
)

from ._constants import _resolve_yuuagents_provider
from ._definition import build_agent_definition
from ._runtime import YuuAgentsActorRuntime
from ._tools import _stage_tool_backend_config


def start_yuuagents_actor(
    binding: ActorBinding,
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
            f"actor {binding.actor.name!r}: no LLM session factory configured "
            f"for provider {llm_provider!r}"
        )
    llm_session_factory = llm_session_factory.with_selector(binding.llm.model)
    stage = Stage.from_config(
        StageConfig(
            strict=yuuagents_config.strict,
            tool_backends=_stage_tool_backend_config(
                yuuagents_config,
                binding=binding,
                facade=facade,
            ),
        ),
        mailbox=mailbox,
        eventbus=eventbus,
        llm_session_factories={llm_provider: llm_session_factory},
        llm_options={llm_provider: _stage_llm_options(binding)},
    )
    definition = build_agent_definition(binding, facade=facade, mode="im")
    conversation_definition = build_agent_definition(
        binding,
        facade=facade,
        mode="conversation",
    )
    if trace_context is not None:
        stage.eventbus.subscribe(YuuTraceObserver(context_provider=trace_context))
    runtime = YuuAgentsActorRuntime(
        stage=stage,
        definitions={definition.name: definition},
        conversation_definition=conversation_definition,
        rollover_enabled=binding.actor.runtime_policy.rollover_enabled,
        idle_timeout_s=binding.actor.runtime_policy.idle_timeout_s,
        summarize_steps_span=binding.actor.runtime_policy.summarize_steps_span,
        agent_pricings={definition.name: binding.llm.backend.pricing},
    )
    return runtime


def _stage_llm_options(binding: ActorBinding) -> dict[str, object]:
    backend = binding.llm.backend
    return validate_stream_options(
        msgspec.to_builtins(backend.default_stream_options),
        context=f"llm_backend[{backend.name}].default_stream_options",
    )


# ── Pricing validation ───────────────────────────────────────────


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
