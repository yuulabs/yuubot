from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from support.api import base_url, http_json, running_server

from yuubot.domain import (
    ContentItem,
    ConversationContext,
    InputMessage,
    LLMInput,
    StreamEvent,
    StreamStopPayload,
    TextDeltaPayload,
    Usage,
    text_content,
)
from yuubot.domain.models import AliasModelSelector, ExactModelSelector
from yuubot.llm.gateway import (
    AliasRecord,
    AliasTarget,
    EndpointRecord,
    EndpointStatus,
    GatewayClient,
    GatewayError,
    _usage_from_chunk,
)
from yuubot.runtime.cache import CachePool
from yuubot.app import Yuubot


class FakeEndpoint:
    def __init__(self, endpoint_id: str, steps: list[object]) -> None:
        self.config = EndpointRecord(endpoint_id, endpoint_id, f"http://{endpoint_id}.test/v1")
        self.status = EndpointStatus(endpoint_id, endpoint_id, self.config.base_url, True)
        self.steps = steps
        self.calls: list[str] = []

    async def stream(self, input, model, context, cache, stop_event, metadata=None):
        del input, context, cache, stop_event, metadata
        self.calls.append(model)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        for event in step:
            if isinstance(event, Exception):
                raise event
            yield event

    async def close(self) -> None:
        return None


def _input(*content: ContentItem) -> LLMInput:
    return LLMInput([], [InputMessage("user", "amy", list(content) or text_content("hello"))])


def _context(tmp_path: Path, selector=AliasModelSelector("fast")) -> ConversationContext:
    return ConversationContext(selector, "c1", "amy", tmp_path)


def _stop() -> StreamEvent:
    return StreamEvent("stop", "stream_stop", StreamStopPayload("stop", Usage(3, 1, 0, 2)))


async def test_alias_uses_declared_modalities_and_ordered_fallback(tmp_path: Path) -> None:
    first = FakeEndpoint("first", [GatewayError("gateway_unreachable", "down")])
    second = FakeEndpoint("second", [[_stop()]])
    gateway = GatewayClient(
        {"first": first, "second": second},
        {"fast": AliasRecord("fast", ["text", "image"], [AliasTarget("first", "m1"), AliasTarget("second", "m2")])},
    )

    events = [event async for event in gateway.stream(
        _input(ContentItem("text", "look"), ContentItem("image", path="image.png", mime="image/png")),
        AliasModelSelector("fast"),
        _context(tmp_path),
        CachePool(),
        asyncio.Event(),
    )]

    assert first.calls == ["m1"]
    assert second.calls == ["m2"]
    payload = events[-1].payload
    assert isinstance(payload, StreamStopPayload)
    assert payload.account == {
        "endpoint_id": "second",
        "model": "m2",
        "fallback_path": ["first/m1", "second/m2"],
    }


async def test_alias_rejects_input_outside_admin_declaration(tmp_path: Path) -> None:
    endpoint = FakeEndpoint("one", [[_stop()]])
    gateway = GatewayClient(
        {"one": endpoint},
        {"text": AliasRecord("text", ["text"], [AliasTarget("one", "m")])},
    )
    with pytest.raises(GatewayError, match="does not accept: image") as raised:
        _ = [event async for event in gateway.stream(
            _input(ContentItem("image", path="image.png", mime="image/png")),
            AliasModelSelector("text"),
            _context(tmp_path),
            CachePool(),
            asyncio.Event(),
        )]
    assert raised.value.code == "gateway_modality_unavailable"
    assert endpoint.calls == []


async def test_exact_selection_bypasses_alias_and_discovery(tmp_path: Path) -> None:
    endpoint = FakeEndpoint("one", [[_stop()]])
    gateway = GatewayClient({"one": endpoint}, {})
    _ = [event async for event in gateway.stream(
        _input(),
        ExactModelSelector("one", "hand-typed-model"),
        _context(tmp_path),
        CachePool(),
        asyncio.Event(),
    )]
    assert endpoint.calls == ["hand-typed-model"]


async def test_gateway_never_falls_back_after_visible_stream_event(tmp_path: Path) -> None:
    first = FakeEndpoint("first", [[
        StreamEvent("text", "text_delta", TextDeltaPayload("visible")),
        GatewayError("gateway_unreachable", "stream broke"),
    ]])
    second = FakeEndpoint("second", [[_stop()]])
    gateway = GatewayClient(
        {"first": first, "second": second},
        {"fast": AliasRecord("fast", ["text"], [AliasTarget("first", "m1"), AliasTarget("second", "m2")])},
    )
    with pytest.raises(GatewayError, match="stream broke"):
        _ = [event async for event in gateway.stream(
            _input(), AliasModelSelector("fast"), _context(tmp_path), CachePool(), asyncio.Event()
        )]
    assert second.calls == []


def test_usage_keeps_standard_details_without_money() -> None:
    class Details:
        def model_dump(self, mode: str):
            del mode
            return {"cached_tokens": 4, "cache_creation_tokens": 2, "audio_tokens": 1}

    class RawUsage:
        prompt_tokens = 12
        completion_tokens = 5
        prompt_tokens_details = Details()
        completion_tokens_details = {"reasoning_tokens": 3}

    assert _usage_from_chunk(RawUsage()) == Usage(
        12,
        4,
        2,
        5,
        {"cached_tokens": 4, "cache_creation_tokens": 2, "audio_tokens": 1},
        {"reasoning_tokens": 3},
    )


async def test_endpoint_api_encrypts_key_and_never_returns_it(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    secret = "recognizable-endpoint-secret"
    try:
        async with running_server(app) as server:
            saved = await http_json(
                "PUT",
                f"{base_url(server)}/api/gateway/endpoints/local",
                {
                    "name": "Local",
                    "base_url": "http://127.0.0.1:11434/v1",
                    "api_key": secret,
                    "clear_api_key": False,
                    "connect_timeout_s": 2,
                    "request_timeout_s": 30,
                    "refresh_models": False,
                },
            )
            assert "api_key" not in saved
            status = await http_json("GET", f"{base_url(server)}/api/gateway")
            assert secret not in str(status)
            endpoints = status["endpoints"]
            assert isinstance(endpoints, list)
            assert endpoints[0]["has_api_key"] is True
            assert await app.runtime.credentials.secret_payload("gateway-endpoint:local") == {"api_key": secret}
            assert secret.encode() not in app.runtime.db.path.read_bytes()
    finally:
        await app.shutdown()
