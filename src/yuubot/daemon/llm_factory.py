"""LLM factory functions — create YLLMClient and SessionCompressor from config.

Pure factories extracted from AgentRunner. No self/state needed.
"""

from __future__ import annotations

import os

import yuullm

from yuubot.characters import CHARACTER_REGISTRY
from yuubot.config import Config


def _build_provider(provider_name: str, providers: dict):
    """Build a yuullm Provider from config dict."""
    provider_cfg = providers.get(provider_name, {})
    api_type = provider_cfg.get("api_type", "openai-chat-completion")
    api_key_env = provider_cfg.get("api_key_env", "")
    api_key = os.environ.get(api_key_env) if api_key_env else None
    base_url = provider_cfg.get("base_url", "") or None

    if api_type == "anthropic-messages":
        return yuullm.providers.AnthropicMessagesProvider(
            api_key=api_key,
            base_url=base_url,
            provider_name=provider_name or "anthropic",
        )
    return yuullm.providers.OpenAIChatCompletionProvider(
        api_key=api_key,
        base_url=base_url,
        provider_name=provider_name or "openai",
    )


def make_llm(agent_name: str, config: Config) -> yuullm.YLLMClient:
    """Build a YLLMClient from Character fields, falling back to YAML."""
    char = CHARACTER_REGISTRY.get(agent_name)
    provider_name = char.provider if char and char.provider else ""
    model = char.model if char and char.model else ""

    if not provider_name or not model:
        agents = config.yuuagents.get("agents", {})
        agent_cfg = agents.get(agent_name, agents.get("main", {}))
        if not provider_name:
            provider_name = agent_cfg.get("provider", "")
        if not model:
            model = agent_cfg.get("model", "")

    providers = config.yuuagents.get("providers", {})
    provider_cfg = providers.get(provider_name, {})
    default_model = model or provider_cfg.get("default_model", "gpt-4o")

    provider = _build_provider(provider_name, providers)

    return yuullm.YLLMClient(
        provider=provider,
        default_model=default_model,
        price_calculator=yuullm.PriceCalculator(),
    )


def make_summary_llm(config: Config) -> yuullm.YLLMClient:
    """Build a YLLMClient for summarization/compression.

    Requires explicit summarizer_provider and summarizer_model in SessionConfig.
    """
    scfg = config.session
    provider_name = scfg.summarizer_provider
    model = scfg.summarizer_model

    if not provider_name or not model:
        raise ValueError(
            "session.summarizer_provider and session.summarizer_model must be "
            "explicitly configured for summarization/compression to work."
        )

    providers = config.yuuagents.get("providers", {})
    provider = _build_provider(provider_name, providers)

    return yuullm.YLLMClient(
        provider=provider,
        default_model=model,
        price_calculator=yuullm.PriceCalculator(),
    )


def make_compressor(agent_name: str, config: Config):
    """Build a SessionCompressor for an agent.

    Returns None if summarizer_provider/model not configured.
    """
    scfg = config.session
    if not scfg.summarizer_provider or not scfg.summarizer_model:
        return None

    from yuubot.daemon.compressor import SessionCompressor

    llm = make_summary_llm(config)

    async def _summarize_fn(history_slice: list, steps_span: int) -> str:
        from yuubot.daemon.summarizer import compress_summary
        return await compress_summary(history_slice, llm, steps_span=steps_span)

    char = CHARACTER_REGISTRY.get(agent_name)
    if char is None:
        return None

    return SessionCompressor(
        max_tokens=char.max_tokens,
        summarize_fn=_summarize_fn,
        summarize_steps_span=scfg.summarize_steps_span,
    )
