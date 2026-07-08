from __future__ import annotations

from pathlib import Path

import httpx

from support.api import base_url, http_json, running_server
from yuubot import Yuubot
from yuubot.domain import ModelCard
from yuubot.llm import ProviderInput


async def test_actor_put_rejects_model_without_pricing(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path)
    try:
        await app.put_provider(
            "deepseek",
            ProviderInput(
                "DeepSeek",
                "openai-compatible",
                {"endpoint": "https://api.deepseek.com", "api_key": "test-key", "options": {}},
            ),
        )
        await app.runtime.state.upsert_model_card(
            "deepseek",
            ModelCard("deepseek-v4-flash"),
        )
        async with running_server(app) as server:
            url = f"{base_url(server)}/api/actors/amy"
            async with httpx.AsyncClient() as client:
                response = await client.put(
                    url,
                    json={
                        "name": "Amy",
                        "workspace": str(tmp_path / "workspace"),
                        "provider": "deepseek",
                        "model": {"selector": "deepseek-v4-flash"},
                    },
                    timeout=30.0,
                )
            assert response.status_code == 422, response.text
            body = response.json()
            assert body["error"]["code"] == "model_pricing_required"
            assert body["error"]["detail"]["selector"] == "deepseek-v4-flash"
    finally:
        await app.shutdown()


async def test_actor_put_accepts_model_with_zero_pricing(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path)
    try:
        await app.put_provider(
            "deepseek",
            ProviderInput(
                "DeepSeek",
                "openai-compatible",
                {"endpoint": "https://api.deepseek.com", "api_key": "test-key", "options": {}},
            ),
        )
        await app.runtime.state.upsert_model_card(
            "deepseek",
            ModelCard(
                "deepseek-chat",
                max_context_tokens=128000,
                input_price_per_million=0,
                cached_input_price_per_million=0,
                output_price_per_million=0,
            ),
        )
        async with running_server(app) as server:
            actor = await http_json(
                "PUT",
                f"{base_url(server)}/api/actors/amy",
                {
                    "name": "Amy",
                    "workspace": str(tmp_path / "workspace"),
                    "provider": "deepseek",
                    "model": {"selector": "deepseek-chat"},
                    "context_compression_tokens": 64000,
                },
            )
            assert actor["id"] == "amy"
            assert actor["context_compression_tokens"] == 64000
            assert actor["model"]["max_context_tokens"] == 128000
    finally:
        await app.shutdown()
