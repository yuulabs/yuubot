"""Cost calculation helpers for yuubot-owned LLM backend pricing.

Contains only pure functions for cost calculation — no session wrappers.
Cost calculation is called by the yuubot orchestrator after each agent.step().
"""

from __future__ import annotations

import yuullm

from yuubot.resources.records import ModelConfig, Pricing


def calculate_cost(
    usage: yuullm.Usage | None,
    model_configs: dict[str, ModelConfig],
    configured_model: str,
) -> yuullm.Cost | None:
    """Calculate cost from usage and configured model pricing.

    Pure function — no side effects. Returns None if no matching pricing entry.
    """
    if usage is None:
        return None
    pricing = _pricing_for_model(usage.model, configured_model, model_configs)
    if pricing is None:
        return None
    return _calculate_cost(usage, pricing)


def _candidate_models(usage_model: str, configured_model: str) -> tuple[str, ...]:
    """Ordered list of model names to try when looking up pricing."""
    if usage_model == configured_model:
        return (usage_model,)
    return (usage_model, configured_model)


def _pricing_for_model(
    usage_model: str,
    configured_model: str,
    model_configs: dict[str, ModelConfig],
) -> Pricing | None:
    """Find the best matching model pricing."""
    for model in _candidate_models(usage_model, configured_model):
        config = model_configs.get(model)
        if config is not None:
            return config.pricing
    return None


def _calculate_cost(usage: yuullm.Usage, pricing: Pricing) -> yuullm.Cost:
    """Pure arithmetic: compute cost from token counts and per-million rates."""
    input_tokens = max(usage.input_tokens, 0)
    cache_read_tokens = min(max(usage.cache_read_tokens, 0), input_tokens)
    regular_input_tokens = input_tokens - cache_read_tokens
    input_cost = regular_input_tokens * pricing.input_per_million / 1_000_000
    cache_read_cost = (
        cache_read_tokens * pricing.cached_input_per_million / 1_000_000
    )
    output_cost = usage.output_tokens * pricing.output_per_million / 1_000_000
    return yuullm.Cost(
        input_cost=input_cost,
        cache_read_cost=cache_read_cost,
        output_cost=output_cost,
        total_cost=input_cost + cache_read_cost + output_cost,
        source="yuubot-pricing",
    )
