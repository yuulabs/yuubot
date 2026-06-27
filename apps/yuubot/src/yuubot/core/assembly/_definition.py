"""Agent definition building from ActorBinding."""

from __future__ import annotations

from typing import Literal

import msgspec
import yuullm
from yuuagents import AgentDefinition, LlmConfig, PromptDefinition

from yuubot.core.bindings import AgentBinding
from yuubot.core.facade import ActorFacadeBinding

from ._compiler import ToolDeriveContext, compile_tool_bindings
from ._prompt import _system_prompt
from ._tools import get_assembly_tool_registry


def build_agent_definition(
    binding: AgentBinding,
    *,
    facade: ActorFacadeBinding | None = None,
    mode: Literal["im", "conversation"] = "im",
    workspace_path: str | None = None,
) -> AgentDefinition:
    context = _build_tool_derive_context(binding, facade, workspace_path)
    tools = _compile_tools(binding, context)
    return AgentDefinition(
        name=binding.agent_name,
        llm=LlmConfig(
            provider=yuullm.resolve_provider(
                binding.llm.backend.provider_identity
            ).api_type,
            model=binding.llm.model,
            max_tokens=binding.llm.generation_params.max_tokens,
            stream_options=msgspec.to_builtins(binding.llm.generation_params),
        ),
        budget=binding.budget.to_budget_config(),
        tools=tools,
        prompt=PromptDefinition(
            system=_system_prompt(binding, mode, facade),
        ),
    )


def _build_tool_derive_context(
    binding: AgentBinding,
    facade: ActorFacadeBinding | None,
    workspace_path: str | None,
) -> ToolDeriveContext:
    """Build the assembly context for ``ToolFactory.derive``.

    Identity fields mirror the bound facade (when present) so context-driven
    derivation reproduces the facade's identity exactly; otherwise they fall
    back to the binding's actor/owner fields.
    """
    if facade is not None:
        actor_id = facade.actor_id
        agent_name = facade.agent_name
        session_id = facade.session_id
        mailbox_id = facade.mailbox_id
        venv_python = facade.venv_python or ""
    else:
        actor_id = binding.actor.id
        agent_name = binding.agent_name
        session_id = binding.owner_id
        mailbox_id = binding.actor.id
        venv_python = ""

    resolved_workspace = workspace_path or binding.capability_set.workspace_path or ""

    return ToolDeriveContext(
        workspace_path=resolved_workspace,
        venv_python=venv_python,
        facade=facade,
        actor_id=actor_id,
        agent_name=agent_name,
        session_id=session_id,
        mailbox_id=mailbox_id,
    )


def _compile_tools(
    binding: AgentBinding,
    context: ToolDeriveContext,
) -> dict[str, dict[str, object]]:
    """Compile the CapabilitySet's explicit tool selections (no injection)."""
    registry = get_assembly_tool_registry()
    if registry is None:
        # Tests that bypass the daemon lifecycle register tools directly; with
        # no registry there is nothing to compile against an empty tool set.
        if not binding.capability_set.tools:
            return {}
        from yuubot.core.validation import ConfigurationError

        raise ConfigurationError(
            "assembly tool registry is not set — cannot compile tool selections"
        )
    bindings = compile_tool_bindings(
        list(binding.capability_set.tools),
        context,
        registry,
    )
    return {
        tb.tool_name: msgspec.to_builtins(tb.config) for tb in bindings
    }
