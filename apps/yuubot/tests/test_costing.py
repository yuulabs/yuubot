"""Public cost calculation tests."""

from __future__ import annotations

import msgspec
import pytest
import yuullm

from yuubot.core.costing import calculate_cost
from yuubot.resources.records import PricingEntry, PricingTable


def test_calculate_cost_splits_cached_input_tokens() -> None:
    usage = yuullm.Usage(
        provider="openai",
        model="gpt-5.4-mini",
        input_tokens=1000,
        cache_read_tokens=400,
        output_tokens=200,
    )
    pricing = PricingTable(
        entries=(
            PricingEntry(
                model="gpt-5.4-mini",
                input_per_million=0.75,
                cached_input_per_million=0.075,
                output_per_million=4.50,
            ),
        )
    )

    cost = calculate_cost(usage, pricing, configured_model="gpt-5.4-mini")

    assert cost is not None
    assert cost.input_cost == pytest.approx(0.00045)
    assert cost.cache_read_cost == pytest.approx(0.00003)
    assert cost.output_cost == pytest.approx(0.0009)
    assert cost.total_cost == pytest.approx(0.00138)


def test_pricing_entry_serializes_cached_input_default() -> None:
    encoded = msgspec.json.decode(msgspec.json.encode(PricingEntry("model")))

    assert encoded["cached_input_per_million"] == 0.0
