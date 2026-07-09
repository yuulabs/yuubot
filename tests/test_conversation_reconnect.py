from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import cast

import pytest
import websockets

from support.api import JsonObject, SharedTestContext, conversation_history, conversation_summary, wait_for_history_kind, ws_url
from yuubot.domain import ConversationContext, LLMInput, ModelCard, StreamEvent, TextDeltaPayload, Usage
from yuubot.llm import merge_catalog
from yuubot.llm.types import AccountSnapshot, ValidationResult
from yuubot.runtime.cache import CachePool
from yuubot.util.stream import stream_stop_event


class PausedStreamingProvider:
    def __init__(self, first: str = "partial ", second: str = "done") -> None:
        self.first = first
        self.second = second
        self.started = asyncio.Event()
        self.first_sent = asyncio.Event()
        self.release = asyncio.Event()

    async def list_presets(self) -> list[ModelCard]:
        return []

    async def list_remote_models(self) -> list[str]:
        return []

    def merge_catalog(self, presets: list[ModelCard], remote: list[str]) -> list[ModelCard]:
        return merge_catalog(presets, remote)

    async def get_balance(self) -> AccountSnapshot | None:
        return None

    async def validate(self) -> ValidationResult:
        return ValidationResult(True)

    async def stream(
        self,
        input: LLMInput,
        model: ModelCard,
        context: ConversationContext,
        cache: CachePool,
        stop_event: asyncio.Event,
    ) -> AsyncIterator[StreamEvent]:
        del input, model, context, cache
        self.started.set()
        yield StreamEvent("text-1", "text_delta", TextDeltaPayload(self.first))
        self.first_sent.set()
        await self.release.wait()
        if stop_event.is_set():
            yield stream_stop_event("interrupted", Usage(), {}, False)
            return
        yield StreamEvent("text-1", "text_delta", TextDeltaPayload(self.second))
        yield stream_stop_event("stop", Usage(), {}, False)

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_ws_disconnect_does_not_interrupt_running_conversation(test_context: SharedTestContext) -> None:
    provider = PausedStreamingProvider()
    actor_id = await test_context.setup_actor(provider)
    conversation_id = test_context.conversation_id("disconnect-c1")
    try:
        async with websockets.connect(ws_url(test_context.server), open_timeout=5) as ws:
            await ws.send(json.dumps(_send_command("send-1", actor_id, conversation_id)))
            await _recv_until(ws, lambda frame: frame.get("type") == "conversation.send.accepted")
            await _recv_until(ws, _is_text_delta(provider.first))
            await ws.close()

        await asyncio.sleep(0.05)
        summary = await conversation_summary(test_context.server, conversation_id)
        assert summary["active"] is True

        provider.release.set()
        history = await wait_for_history_kind(test_context.server, conversation_id, "gen_text")
        assert history[-1]["payload"] == {"text": f"{provider.first}{provider.second}"}
        summary = await _wait_for_inactive_summary(test_context, conversation_id)
        assert summary["active"] is False
        assert summary["status"] == "closed"
    finally:
        provider.release.set()


@pytest.mark.asyncio
async def test_history_subscribe_replays_active_in_memory_stream(test_context: SharedTestContext) -> None:
    provider = PausedStreamingProvider()
    actor_id = await test_context.setup_actor(provider)
    conversation_id = test_context.conversation_id("replay-c1")
    try:
        async with websockets.connect(ws_url(test_context.server), open_timeout=5) as ws:
            await ws.send(json.dumps(_send_command("send-1", actor_id, conversation_id)))
            await _recv_until(ws, lambda frame: frame.get("type") == "conversation.send.accepted")
            await _recv_until(ws, _is_text_delta(provider.first))
            await ws.close()

        history = await conversation_history(test_context.server, conversation_id)
        assert [item["kind"] for item in history] == ["input"]

        async with websockets.connect(ws_url(test_context.server), open_timeout=5) as ws:
            await ws.send(
                json.dumps(
                    {
                        "id": "history-1",
                        "type": "conversation.history.subscribe",
                        "payload": {"conversation_id": conversation_id},
                    }
                )
            )
            await _recv_until(ws, lambda frame: frame.get("type") == "conversation.history.subscribe.result")
            replay_start = await _recv_until(ws, lambda frame: frame.get("type") == "conversation.replay.start")
            replayed_delta = await _recv_until(ws, _is_text_delta(provider.first))
            assert cast(JsonObject, replay_start["payload"])["count"] == 1
            assert cast(JsonObject, replayed_delta["payload"])["live_seq"] == 1

            provider.release.set()
            live_delta = await _recv_until(ws, _is_text_delta(provider.second))
            assert cast(JsonObject, live_delta["payload"])["live_seq"] == 2
            await _recv_until(ws, _is_terminal_stop)

        history = await conversation_history(test_context.server, conversation_id)
        assert history[-1]["payload"] == {"text": f"{provider.first}{provider.second}"}
    finally:
        provider.release.set()


def _send_command(command_id: str, actor_id: str, conversation_id: str) -> JsonObject:
    return {
        "id": command_id,
        "type": "conversation.send",
        "payload": {
            "actor_id": actor_id,
            "conversation_id": conversation_id,
            "content": [{"kind": "text", "text": "hello"}],
        },
    }


async def _wait_for_inactive_summary(context: SharedTestContext, conversation_id: str) -> JsonObject:
    summary: JsonObject = {}
    for _ in range(100):
        summary = await conversation_summary(context.server, conversation_id)
        if summary.get("active") is False:
            return summary
        await asyncio.sleep(0.01)
    return summary


async def _recv_until(
    ws: websockets.ClientConnection,
    predicate: Callable[[JsonObject], bool],
    timeout: float = 5.0,
) -> JsonObject:
    deadline = asyncio.get_running_loop().time() + timeout
    frames: list[JsonObject] = []
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise AssertionError(f"timed out waiting for websocket frame; received={frames!r}")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        frame = cast(JsonObject, json.loads(raw))
        frames.append(frame)
        if predicate(frame):
            return frame


def _is_text_delta(expected: str) -> Callable[[JsonObject], bool]:
    def predicate(frame: JsonObject) -> bool:
        if frame.get("type") != "conversation.stream":
            return False
        payload = frame.get("payload")
        if not isinstance(payload, dict):
            return False
        payload = cast(JsonObject, payload)
        event_value = payload.get("event")
        if not isinstance(event_value, dict):
            return False
        event = cast(JsonObject, event_value)
        if event.get("kind") != "text_delta":
            return False
        event_payload = event.get("payload")
        if not isinstance(event_payload, dict):
            return False
        event_payload = cast(JsonObject, event_payload)
        return event_payload.get("text") == expected

    return predicate


def _is_terminal_stop(frame: JsonObject) -> bool:
    if frame.get("type") != "conversation.stream":
        return False
    payload = frame.get("payload")
    if not isinstance(payload, dict):
        return False
    payload = cast(JsonObject, payload)
    event_value = payload.get("event")
    if not isinstance(event_value, dict):
        return False
    event = cast(JsonObject, event_value)
    if event.get("kind") != "stream_stop":
        return False
    event_payload = event.get("payload")
    if not isinstance(event_payload, dict):
        return False
    event_payload = cast(JsonObject, event_payload)
    return event_payload.get("reason") == "stop"
