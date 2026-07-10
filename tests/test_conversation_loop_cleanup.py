from __future__ import annotations

import asyncio
import queue
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from yuubot.actor import ActorConfig
from yuubot.app import Yuubot
from yuubot.chat import harness as harness_module
from yuubot.domain import ConversationContext, InputMessage, LLMInput, StreamEvent, TextDeltaPayload, Usage, text_content
from yuubot.llm import scripted_reply
from yuubot.runtime.cache import CachePool
from yuubot.util.stream import stream_stop_event


class BlockingProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(
        self,
        input: LLMInput,
        model: str,
        context: ConversationContext,
        cache: CachePool,
        stop_event: asyncio.Event,
        metadata: dict[str, str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del input, model, context, cache, metadata
        self.started.set()
        await self.release.wait()
        reason = "interrupted" if stop_event.is_set() else "stop"
        yield StreamEvent("text-1", "text_delta", TextDeltaPayload("done"))
        yield stream_stop_event(reason, Usage(), {})

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_terminal_turn_marks_closed_when_harness_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def close_raises(self: harness_module.Harness) -> None:
        del self
        raise queue.Empty()

    monkeypatch.setattr(harness_module.Harness, "close", close_raises)

    app = await Yuubot.create(tmp_path / "data")
    conversation_id = "conv-cleanup-test"
    try:
        app.create_actor(
            ActorConfig(
                id="amy",
                name="Amy",
                workspace=str(tmp_path / "workspace"),
                model="fake",
            ),
            scripted_reply("done"),
        )
        await app.run_user_message(
            "amy",
            InputMessage("user", "amy", text_content("hi")),
            conversation_id,
        )
        rows = await app.runtime.state.list_conversations()
        record = next(item for item in rows if item.id == conversation_id)
        assert record.status == "closed"
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_running_conversation_is_not_swept_until_it_becomes_idle(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    provider = BlockingProvider()
    conversation_id = "conv-running-ttl"
    try:
        app.create_actor(
            ActorConfig(
                id="amy",
                name="Amy",
                workspace=str(tmp_path / "workspace"),
                model="fake",
            ),
            provider,
        )
        task = asyncio.create_task(
            app.run_user_message(
                "amy",
                InputMessage("user", "amy", text_content("hi")),
                conversation_id,
            )
        )
        await provider.started.wait()

        app.runtime.conversations.ttl_s = -1
        await app.runtime.conversations.sweep()
        assert app.runtime.conversations.has(conversation_id)

        provider.release.set()
        await task
        await app.runtime.conversations.sweep()
        assert not app.runtime.conversations.has(conversation_id)
    finally:
        provider.release.set()
        await app.shutdown()


@pytest.mark.asyncio
async def test_history_append_does_not_refresh_conversation_idle_ttl(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    conversation_id = "conv-history-no-touch"
    try:
        actor = app.create_actor(
            ActorConfig(
                id="amy",
                name="Amy",
                workspace=str(tmp_path / "workspace"),
                model="fake",
            ),
            scripted_reply("done"),
        )
        conversation = await app.runtime.conversations.get_or_create(actor, conversation_id)
        app.runtime.conversations.ttl_s = 10
        app.runtime.conversations._idle_since[conversation_id] = 0.0

        await conversation.append_items([InputMessage("developer", "yuubot", text_content("note"))])
        await app.runtime.conversations.sweep()

        assert not app.runtime.conversations.has(conversation_id)
    finally:
        await app.shutdown()
