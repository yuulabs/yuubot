from __future__ import annotations

from pathlib import Path

import pytest
from yuubot import Yuubot
from yuubot.domain import ModelCard
from yuubot.llm import ProviderInput, refresh_catalog
from yuubot.llm.openai import OpenAIProvider


async def test_refresh_catalog_removes_stale_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = await Yuubot.create(tmp_path)
    try:
        await app.put_provider(
            "deepseek",
            ProviderInput(
                name="DeepSeek",
                protocol="openai-compatible",
                config={"endpoint": "https://api.deepseek.com", "api_key": "test-key", "options": {}},
            ),
        )
        await app.runtime.state.upsert_model_card("deepseek", ModelCard(selector="gpt-4o"))
        await app.runtime.state.upsert_model_card("deepseek", ModelCard(selector="deepseek-chat", input_price_per_million=0.5))

        async def remote_models(self: OpenAIProvider) -> list[str]:
            del self
            return ["deepseek-chat", "deepseek-reasoner"]

        monkeypatch.setattr(OpenAIProvider, "list_remote_models", remote_models)

        cards = await refresh_catalog(
            "deepseek",
            store=app.runtime.state,
            registry=app.runtime.provider_registry,
        )
        selectors = {card.selector for card in cards}
        assert selectors == {"deepseek-chat", "deepseek-reasoner"}
        configured = next(card for card in cards if card.selector == "deepseek-chat")
        assert configured.input_price_per_million == 0.5
    finally:
        await app.shutdown()


async def test_refresh_catalog_retains_actor_bound_stale_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = await Yuubot.create(tmp_path)
    try:
        await app.put_provider(
            "deepseek",
            ProviderInput(
                name="DeepSeek",
                protocol="openai-compatible",
                config={"endpoint": "https://api.deepseek.com", "api_key": "test-key", "options": {}},
            ),
        )
        await app.runtime.state.upsert_model_card("deepseek", ModelCard(selector="legacy-model"))
        await app.runtime.state.upsert_model_card("deepseek", ModelCard(selector="deepseek-chat"))

        async def remote_models(self: OpenAIProvider) -> list[str]:
            del self
            return ["deepseek-chat"]

        monkeypatch.setattr(OpenAIProvider, "list_remote_models", remote_models)

        cards = await refresh_catalog(
            "deepseek",
            store=app.runtime.state,
            registry=app.runtime.provider_registry,
            retain_selectors=frozenset({"legacy-model"}),
        )
        assert {card.selector for card in cards} == {"deepseek-chat", "deepseek-reasoner", "legacy-model"}
    finally:
        await app.shutdown()
