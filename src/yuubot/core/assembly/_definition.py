"""Agent definition building from ActorBinding."""

from __future__ import annotations

from typing import Literal

import msgspec
from yuuagents import AgentDefinition, LlmConfig, PromptDefinition

from yuubot.core.bindings import ActorBinding
from yuubot.core.facade import ActorFacadeBinding

from ._constants import _resolve_yuuagents_provider
from ._prompt import _system_prompt
from ._tools import _agent_tool_configs


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
