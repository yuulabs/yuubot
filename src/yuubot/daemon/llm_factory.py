"""LLM factory functions — create YLLMClient from config."""

from __future__ import annotations

import yuullm

from yuubot.config import Config
from yuubot.model_resolution import ModelResolver, ResolvedModel, build_llm_client


async def make_llm(agent_name: str, config: Config) -> yuullm.YLLMClient:
    """Build a YLLMClient for an agent using selector-based resolution."""
    client, _resolved = await make_resolved_llm(agent_name, config)
    return client


async def make_resolved_llm(agent_name: str, config: Config) -> tuple[yuullm.YLLMClient, ResolvedModel]:
    """Build a YLLMClient and return the resolved model metadata."""
    resolved = await ModelResolver(config).resolve_agent(agent_name)
    return build_llm_client(resolved.resolved_provider, resolved.resolved_model, config), resolved


async def make_summary_llm(config: Config) -> yuullm.YLLMClient:
    """Build a YLLMClient for summarization/compression via llm_roles.summarizer."""
    resolved = await ModelResolver(config).resolve_role("summarizer")
    return build_llm_client(resolved.resolved_provider, resolved.resolved_model, config)
