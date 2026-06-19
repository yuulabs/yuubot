"""Cost calculation helpers for yuubot-owned LLM backend pricing.

Contains only pure functions for cost calculation — no session wrappers.
Cost calculation is called by the yuubot orchestrator after each agent.step().
"""

from __future__ import annotations

import yuullm

from yuubot.resources.records import PricingEntry, PricingTable


def calculate_cost(usage: yuullm.Usage | None, pricing: PricingTable, configured_model: str) -> yuullm.Cost | None:
    """Calculate cost from usage and pricing table.

    Pure function — no side effects. Returns None if no matching pricing entry.
    """
    if usage is None:
        return None
    entry = _pricing_entry(usage.model, configured_model, pricing)
    if entry is None:
        return None
    return _calculate_cost(usage, entry)


def _candidate_models(usage_model: str, configured_model: str) -> tuple[str, ...]:
    """Ordered list of model names to try when looking up pricing."""
    if usage_model == configured_model:
        return (usage_model,)
    return (usage_model, configured_model)


def _pricing_entry(
    usage_model: str,
    configured_model: str,
    pricing: PricingTable,
) -> PricingEntry | None:
    """Find the best matching pricing entry."""
    for model in _candidate_models(usage_model, configured_model):
        for entry in pricing.entries:
            if entry.model == model:
                return entry
    return None


def _calculate_cost(usage: yuullm.Usage, entry: PricingEntry) -> yuullm.Cost:
    """Pure arithmetic: compute cost from token counts and per-million rates."""
    input_cost = usage.input_tokens * entry.input_per_million / 1_000_000
    output_cost = usage.output_tokens * entry.output_per_million / 1_000_000
    return yuullm.Cost(
        input_cost=input_cost,
        output_cost=output_cost,
        total_cost=input_cost + output_cost,
        source="yuubot-pricing",
    )
