"""LLM factory functions — create YLLMClient and SessionCompressor from config.

Pure factories extracted from AgentRunner. No self/state needed.
"""

from __future__ import annotations

import yuullm

from yuubot.config import Config
from yuubot.model_resolution import ModelResolver, build_llm_client


async def make_llm(agent_name: str, config: Config) -> yuullm.YLLMClient:
    """Build a YLLMClient for an agent using selector-based resolution."""
    resolved = await ModelResolver(config).resolve_agent(agent_name)
    return build_llm_client(resolved.resolved_provider, resolved.resolved_model, config)


async def make_summary_llm(config: Config) -> yuullm.YLLMClient:
    """Build a YLLMClient for summarization/compression via llm_roles.summarizer."""
    resolved = await ModelResolver(config).resolve_role("summarizer")
    return build_llm_client(resolved.resolved_provider, resolved.resolved_model, config)


async def make_compressor(agent_name: str, config: Config):
    """Build a SessionCompressor for an agent."""
    from yuubot.daemon.compressor import SessionCompressor

    llm = await make_summary_llm(config)

    async def _summarize_fn(history_slice: list, steps_span: int) -> str:
        from yuubot.daemon.summarizer import compress_summary
        return await compress_summary(history_slice, llm, steps_span=steps_span)

    from yuubot.characters import CHARACTER_REGISTRY

    char = CHARACTER_REGISTRY.get(agent_name)
    if char is None:
        return None

    return SessionCompressor(
        max_tokens=char.max_tokens,
        summarize_fn=_summarize_fn,
        summarize_steps_span=config.session.summarize_steps_span,
    )
