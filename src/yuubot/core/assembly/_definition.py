"""Agent definition building from ActorBinding."""

from __future__ import annotations

from typing import Literal

import msgspec
from yuuagents import AgentDefinition, LlmConfig, PromptDefinition

from yuubot.core.bindings import AgentBinding
from yuubot.core.facade import ActorFacadeBinding

from ._constants import _resolve_yuuagents_provider
from ._prompt import _system_prompt
from ._tools import _agent_tool_configs


def build_agent_definition(
    binding: AgentBinding,
    *,
    facade: ActorFacadeBinding | None = None,
    mode: Literal["im", "conversation"] = "im",
    workspace_path: str | None = None,
) -> AgentDefinition:
    return AgentDefinition(
        name=binding.agent_name,
        llm=LlmConfig(
            provider=_resolve_yuuagents_provider(binding.llm.backend.yuuagents_provider),
            model=binding.llm.model,
            max_tokens=binding.llm_options.max_tokens,
            stream_options=msgspec.to_builtins(binding.llm_options.stream_options),
        ),
        budget=binding.budget.to_budget_config(),
        tools=_agent_tool_configs(
            binding.capability_set.agent_tools,
            facade,
            workspace_path=workspace_path,
        ),
        prompt=PromptDefinition(
            system=_system_prompt(binding, mode),
        ),
    )
