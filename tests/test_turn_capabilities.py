from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from support.api import base_url, running_server
from yuubot.actor import ActorConfig
from yuubot.app import Yuubot
from yuubot.integrations.web import WebConfig, WebIntegration
from yuubot.llm.gateway import (
    AliasRecord,
    AliasTarget,
    GatewayClient,
    HostedSearchCitation,
    HostedSearchResult,
)
from yuubot.domain.stream import Usage
from yuubot.domain.models import AliasModelSelector
from yuubot.runtime.turn_limits import TurnIdentity


async def _active_turn(app: Yuubot, tmp_path: Path, gateway: GatewayClient) -> tuple[str, object]:
    actor = app.create_actor(
        ActorConfig(id="amy", name="Amy", workspace=str(tmp_path / "workspace"), model=AliasModelSelector("main")),
        gateway,
    )
    conversation = await actor.spawn_conversation("c1")
    conversation._running = True  # noqa: SLF001 - test establishes the live-turn boundary directly
    app.runtime.conversations._items[conversation.id] = conversation  # noqa: SLF001
    token = app.runtime.turn_limits.open(TurnIdentity("amy", "c1", "turn-1", "trace-1"))
    return token, conversation


@pytest.mark.asyncio
async def test_loopback_fixer_guard_keeps_facades_independent_and_records_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = await Yuubot.create(tmp_path / "data")
    gateway = GatewayClient(
        aliases={
            name: AliasRecord(name, ["text"], [AliasTarget("test", name)])
            for name in ("main", "ask-gemini", "ask-grok")
        },
    )
    app.gateway_client = gateway
    app.runtime.gateway_client = gateway
    token, _ = await _active_turn(app, tmp_path, gateway)
    calls: list[str] = []

    async def hosted_search(
        _client: GatewayClient,
        model: str,
        prompt: str,
        metadata: dict[str, str],
        enable_web_search: bool = False,
        pass_through_options: dict[str, object] | None = None,
    ) -> HostedSearchResult:
        del enable_web_search, pass_through_options
        calls.append(model)
        assert prompt == "one combined question"
        assert metadata["purpose"] == "fixer"
        return HostedSearchResult(
            f"answer from {model}",
            [] if model == "ask-gemini" else [HostedSearchCitation("https://example.com/source", "Source")],
            Usage(10, 1, 0, 4),
            {"model": model, "gateway_latency_ms": 12.5},
        )

    monkeypatch.setattr(GatewayClient, "hosted_search", hosted_search)
    headers = {"X-Yuubot-Turn-Token": token}
    try:
        async with running_server(app) as server:
            async with httpx.AsyncClient(base_url=base_url(server)) as client:
                gemini = await client.post("/api/fixer/gemini", headers=headers, json={"prompt": "one combined question"})
                gemini_again = await client.post("/api/fixer/gemini", headers=headers, json={"prompt": "one combined question"})
                grok = await client.post("/api/fixer/grok", headers=headers, json={"prompt": "one combined question"})
                invalid = await client.post(
                    "/api/fixer/grok",
                    headers={"X-Yuubot-Turn-Token": "expired"},
                    json={"prompt": "one combined question"},
                )
                usage_rows = await app.runtime.state.load_usage("c1")

        assert gemini.status_code == 200
        assert gemini.json()["citations"] == []
        assert gemini_again.status_code == 429
        assert gemini_again.json()["error"]["code"] == "fixer_limit_reached"
        assert grok.status_code == 200
        assert invalid.status_code == 401
        assert calls == ["ask-gemini", "ask-grok"]
        assert len(usage_rows) == 2
        assert {row.account["facade"] for row in usage_rows} == {"gemini", "grok"}
    finally:
        app.runtime.turn_limits.close(token)


@pytest.mark.asyncio
async def test_loopback_search_failure_releases_slot_and_fourth_success_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = await Yuubot.create(tmp_path / "data")
    gateway = GatewayClient(aliases={"main": AliasRecord("main", ["text"], [AliasTarget("test", "main")])})
    app.gateway_client = gateway
    app.runtime.gateway_client = gateway
    token, _ = await _active_turn(app, tmp_path, gateway)
    app.runtime.integrations["web"] = WebIntegration(
        "web",
        WebConfig("tavily-key", tavily_base_url="https://search.test"),
    )
    attempts = 0

    async def search_direct(*args: object, **kwargs: object) -> list[object]:
        nonlocal attempts
        del args, kwargs
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary search failure")
        from yext.web import SearchResult

        return [SearchResult("Result", f"https://example.com/{attempts}", "snippet")]

    monkeypatch.setattr("yext.web._search_direct", search_direct)
    headers = {"X-Yuubot-Turn-Token": token}
    try:
        async with running_server(app) as server:
            async with httpx.AsyncClient(base_url=base_url(server)) as client:
                failed = await client.post("/api/web/search", headers=headers, json={"query": "q"})
                successes = [
                    await client.post("/api/web/search", headers=headers, json={"query": f"q{index}"})
                    for index in range(3)
                ]
                fourth = await client.post("/api/web/search", headers=headers, json={"query": "q4"})

        assert failed.status_code == 502
        assert [response.status_code for response in successes] == [200, 200, 200]
        assert fourth.status_code == 429
        assert fourth.json()["error"]["code"] == "search_limit_reached"
        assert attempts == 4
    finally:
        app.runtime.turn_limits.close(token)


@pytest.mark.asyncio
async def test_fixer_capability_absence_is_explicit(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    gateway = GatewayClient(aliases={"main": AliasRecord("main", ["text"], [AliasTarget("test", "main")])})
    app.gateway_client = gateway
    app.runtime.gateway_client = gateway
    token, _ = await _active_turn(app, tmp_path, gateway)
    try:
        async with running_server(app) as server:
            async with httpx.AsyncClient(base_url=base_url(server)) as client:
                response = await client.post(
                    "/api/fixer/gemini",
                    headers={"X-Yuubot-Turn-Token": token},
                    json={"prompt": "question"},
                )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "hosted_search_unavailable"
    finally:
        app.runtime.turn_limits.close(token)
