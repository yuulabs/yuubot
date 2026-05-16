"""Cost calculation helpers for yuubot-owned LLM backend pricing."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import yuullm
from yuuagents.agent import LlmClient

from yuubot.resources.records import PricingEntry, PricingTable


@dataclass
class PricingAwareLlmClient:
    """Populate ``store.cost`` from yuubot backend pricing when needed."""

    inner: LlmClient
    pricing: PricingTable
    configured_model: str

    async def stream(
        self,
        messages: list[yuullm.Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> yuullm.StreamResult:
        stream, store = await self.inner.stream(messages, tools=tools, **kwargs)
        return self._with_cost(stream, store), store

    async def _with_cost(
        self,
        stream: AsyncIterator[yuullm.StreamItem],
        store: yuullm.Store,
    ) -> AsyncIterator[yuullm.StreamItem]:
        async for item in stream:
            yield item
        self._populate_cost(store)

    def _populate_cost(self, store: yuullm.Store) -> None:
        if store.cost is not None or store.usage is None:
            return
        if store.provider_cost is not None:
            store.cost = yuullm.Cost(
                input_cost=0.0,
                output_cost=0.0,
                total_cost=store.provider_cost,
                source="provider",
            )
            return
        entry = self._pricing_entry(store.usage.model)
        if entry is None:
            return
        store.cost = _calculate_cost(store.usage, entry)

    def _pricing_entry(self, usage_model: str) -> PricingEntry | None:
        for model in _candidate_models(usage_model, self.configured_model):
            for entry in self.pricing.entries:
                if entry.model == model:
                    return entry
        return None


def _candidate_models(usage_model: str, configured_model: str) -> tuple[str, ...]:
    if usage_model == configured_model:
        return (usage_model,)
    return (usage_model, configured_model)


def _calculate_cost(usage: yuullm.Usage, entry: PricingEntry) -> yuullm.Cost:
    input_cost = usage.input_tokens * entry.input_per_million / 1_000_000
    output_cost = usage.output_tokens * entry.output_per_million / 1_000_000
    return yuullm.Cost(
        input_cost=input_cost,
        output_cost=output_cost,
        total_cost=input_cost + output_cost,
        source="yuubot-pricing",
    )
