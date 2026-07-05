"""Model catalog helpers shared by admin flows and provider implementations."""

from ..domain.messages import ModelCard
from ..runtime.store import ApplicationStateStore
from .protocol import Provider
from .records import ProviderRecord
from .registry import ProviderRegistry
from .types import ModelCardInput


def default_model_card(selector: str) -> ModelCard:
    return ModelCard(selector=selector)


def model_card_from_input(body: ModelCardInput) -> ModelCard:
    return ModelCard(
        selector=body.selector,
        vision=body.vision,
        toolcall=body.toolcall,
        json=body.json,
        input_price_per_million=body.input_price_per_million,
        cached_input_price_per_million=body.cached_input_price_per_million,
        output_price_per_million=body.output_price_per_million,
    )


def is_configured(card: ModelCard) -> bool:
    defaults = ModelCard(selector=card.selector)
    return (
        card.vision != defaults.vision
        or card.toolcall != defaults.toolcall
        or card.json != defaults.json
        or card.input_price_per_million != defaults.input_price_per_million
        or card.cached_input_price_per_million != defaults.cached_input_price_per_million
        or card.output_price_per_million != defaults.output_price_per_million
    )


def merge_catalog(presets: list[ModelCard], remote: list[str]) -> list[ModelCard]:
    by_selector = {card.selector: card for card in presets}
    for selector in remote:
        by_selector.setdefault(selector, default_model_card(selector))
    return [by_selector[key] for key in sorted(by_selector)]


async def refresh_catalog(
    provider_id: str,
    *,
    store: ApplicationStateStore,
    registry: ProviderRegistry,
    retain_selectors: frozenset[str] = frozenset(),
) -> list[ModelCard]:
    record = await store.load_provider(provider_id)
    provider = registry.build(record)
    try:
        merged = provider.merge_catalog(await provider.list_presets(), await provider.list_remote_models())
        available = {card.selector for card in merged}
        for card in merged:
            existing = await store.load_model_card(provider_id, card.selector)
            if existing is not None and is_configured(existing):
                continue
            await store.upsert_model_card(provider_id, card)
        for existing in await store.list_model_cards(provider_id):
            if existing.selector in available or existing.selector in retain_selectors:
                continue
            await store.delete_model_card(provider_id, existing.selector)
        return await store.list_model_cards(provider_id)
    finally:
        await provider.close()


async def build_actor_provider(
    provider_id: str,
    *,
    store: ApplicationStateStore,
    registry: ProviderRegistry,
) -> Provider:
    record = await store.load_provider(provider_id)
    return registry.build(record)


def provider_configured(record: ProviderRecord) -> bool:
    api_key = record.config.get("api_key")
    return isinstance(api_key, str) and bool(api_key)
