"""Built-in LLM backend catalogues."""

from __future__ import annotations

import msgspec

from yuubot.core.validation import LLMProviderOptions
from yuubot.resources.records import PricingEntry


class BuiltinCatalogue(msgspec.Struct, frozen=True):
    default_model: str
    models: tuple[str, ...]
    pricing_entries: tuple[PricingEntry, ...]


BUILTIN_CATALOGUES: dict[str, BuiltinCatalogue] = {
    "openai": BuiltinCatalogue(
        default_model="gpt-5.4-mini",
        models=("gpt-5.5", "gpt-5.4-mini", "gpt-5.4-nano"),
        pricing_entries=(
            PricingEntry(
                "gpt-5.5",
                input_per_million=5.00,
                cached_input_per_million=0.50,
                output_per_million=30.00,
            ),
            PricingEntry(
                "gpt-5.4-mini",
                input_per_million=0.75,
                cached_input_per_million=0.075,
                output_per_million=4.50,
            ),
            PricingEntry(
                "gpt-5.4-nano",
                input_per_million=0.20,
                cached_input_per_million=0.02,
                output_per_million=1.25,
            ),
        ),
    ),
    "deepseek": BuiltinCatalogue(
        default_model="deepseek-v4-flash",
        models=("deepseek-v4-flash", "deepseek-v4-pro"),
        pricing_entries=(
            PricingEntry(
                "deepseek-v4-flash",
                input_per_million=0.14,
                cached_input_per_million=0.0028,
                output_per_million=0.28,
            ),
            PricingEntry(
                "deepseek-v4-pro",
                input_per_million=0.435,
                cached_input_per_million=0.003625,
                output_per_million=0.87,
            ),
        ),
    ),
}


def builtin_catalogue_for(
    provider_options: LLMProviderOptions,
    yuuagents_provider: str,
) -> BuiltinCatalogue | None:
    if provider_options.provider_name in BUILTIN_CATALOGUES:
        return BUILTIN_CATALOGUES[provider_options.provider_name]
    if provider_options.base_url == "https://api.openai.com/v1":
        return BUILTIN_CATALOGUES["openai"]
    if provider_options.base_url == "https://api.deepseek.com":
        return BUILTIN_CATALOGUES["deepseek"]
    if yuuagents_provider in BUILTIN_CATALOGUES:
        return BUILTIN_CATALOGUES[yuuagents_provider]
    return None
