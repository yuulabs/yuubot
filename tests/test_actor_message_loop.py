from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from support.integration_app import reset_actor_app_state
from yuubot.actor import ActorConfig
from yuubot.domain import ActorMessage, ConversationContext, LLMInput, ModelCard, StreamEvent
from yuubot.llm import merge_catalog, scripted_reply
from yuubot.llm.types import AccountSnapshot, ValidationResult
from yuubot.app import Yuubot
from yuubot.runtime.cache import CachePool
from support.llm_rules import prompt_contains, reply_text, user_message_contains
from support.prompt_conditioned_llm import PromptConditionedProvider


def _input_roles(items: list[dict[str, object]]) -> list[str]:
    roles: list[str] = []
    for item in items:
        if item.get("kind") != "input":
            continue
        payload = item.get("payload")
        if isinstance(payload, dict):
            roles.append(str(payload.get("role")))
    return roles


def _input_texts(items: list[dict[str, object]]) -> list[str]:
    texts: list[str] = []
    for item in items:
        if item.get("kind") != "input":
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        content = payload.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("kind") == "text":
                texts.append(str(part.get("text")))
    return texts


class BlockingProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()
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
        return ValidationResult(ok=True)

    async def stream(
        self,
        input: LLMInput,
        *,
        model: ModelCard,
        context: ConversationContext,
        cache: CachePool,
        stop_event: asyncio.Event,
    ) -> AsyncIterator[StreamEvent]:
        del input, model, context, cache, stop_event
        self.started.set()
        await self.release.wait()
        yield StreamEvent(group_id="text-1", kind="text_delta", payload={"text": "ok"})
        yield StreamEvent(group_id="stop", kind="stream_stop", payload={"reason": "stop"})

    async def close(self) -> None:
        return None


@pytest.fixture
async def actor_loop_app(tmp_path) -> AsyncIterator[tuple[Yuubot, Path]]:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    try:
        yield app, workspace
    finally:
        await app.shutdown()


@pytest.fixture(autouse=True)
async def _reset_actor_loop_module_state(actor_loop_app: tuple[Yuubot, Path]) -> AsyncIterator[None]:
    yield
    await reset_actor_app_state(actor_loop_app[0])


@pytest.mark.asyncio
async def test_actor_inbound_without_conversation_reuses_default_conversation(actor_loop_app: tuple[Yuubot, Path]) -> None:
    app, workspace = actor_loop_app
    actor = app.create_actor(
        ActorConfig(
            id="amy",
            name="Amy",
            workspace=str(workspace),
            model=ModelCard(selector="fake"),
        ),
        scripted_reply("ok"),
    )
    await actor.handle_mailbox_message(ActorMessage(text="first", source={"inbound_kind": "actor_inbound"}))
    first_conversation = actor._mailbox_conversation
    assert first_conversation is not None

    await actor.handle_mailbox_message(ActorMessage(text="second", source={"inbound_kind": "actor_inbound"}))

    assert actor._mailbox_conversation == first_conversation
    items = await app.runtime.history.load_interaction_wrapped(first_conversation)
    assert _input_roles(items) == ["user", "user"]


@pytest.mark.asyncio
async def test_actor_inbound_without_conversation_creates_new_default_after_ttl(actor_loop_app: tuple[Yuubot, Path]) -> None:
    app, workspace = actor_loop_app
    actor = app.create_actor(
        ActorConfig(
            id="amy",
            name="Amy",
            workspace=str(workspace),
            model=ModelCard(selector="fake"),
        ),
        scripted_reply("ok"),
    )
    await actor.handle_mailbox_message(ActorMessage(text="first", source={"inbound_kind": "actor_inbound"}))
    first_conversation = actor._mailbox_conversation
    assert first_conversation is not None

    app.runtime.conversations.ttl_s = -1
    await actor.handle_mailbox_message(ActorMessage(text="second", source={"inbound_kind": "actor_inbound"}))

    assert actor._mailbox_conversation is not None
    assert actor._mailbox_conversation != first_conversation


@pytest.mark.asyncio
async def test_actor_explicit_conversation_and_callback_roles(actor_loop_app: tuple[Yuubot, Path]) -> None:
    app, workspace = actor_loop_app
    actor = app.create_actor(
        ActorConfig(
            id="amy",
            name="Amy",
            workspace=str(workspace),
            model=ModelCard(selector="fake"),
        ),
        scripted_reply("ok"),
    )
    await actor.handle_mailbox_message(
        ActorMessage(text="hello", conversation_id="explicit", source={"inbound_kind": "actor_inbound"})
    )
    await actor.handle_mailbox_message(
        ActorMessage(
            text="task done",
            conversation_id="explicit",
            source={"inbound_kind": "conversation_callback"},
        )
    )

    items = await app.runtime.history.load_interaction_wrapped("explicit")
    assert _input_roles(items) == ["user", "developer"]


@pytest.mark.asyncio
async def test_actor_mailbox_compacts_once_and_continues(actor_loop_app: tuple[Yuubot, Path]) -> None:
    app, workspace = actor_loop_app
    actor = app.create_actor(
        ActorConfig(
            id="amy",
            name="Amy",
            workspace=str(workspace),
            model=ModelCard(selector="fake"),
            context_compression_tokens=5,
        ),
        PromptConditionedProvider(
            rules=[
                (prompt_contains("Summarize the current work"), reply_text("summary text", usage={"input_tokens": 1})),
                (
                    user_message_contains("automatic context compression continuation"),
                    reply_text("continued", usage={"input_tokens": 1}),
                ),
                (user_message_contains("first"), reply_text("old done", usage={"input_tokens": 5})),
            ]
        ),
    )

    await actor.handle_mailbox_message(ActorMessage(text="first", source={"inbound_kind": "actor_inbound"}))

    new_conversation = actor._mailbox_conversation
    assert new_conversation is not None
    compacted_event = next(event for event in app.runtime.eventbus.events if event.kind == "actor.context_compacted")
    old_conversation = str(compacted_event.payload["old_conversation_id"])
    assert new_conversation == compacted_event.payload["new_conversation_id"]

    old_items = await app.runtime.history.load_interaction_wrapped(old_conversation)
    new_items = await app.runtime.history.load_interaction_wrapped(new_conversation)
    assert _input_roles(old_items) == ["user", "developer"]
    assert _input_roles(new_items) == ["developer", "user"]
    assert _input_texts(new_items)[0] == "summary text"
    assert "first" in _input_texts(new_items)[1]


@pytest.mark.asyncio
async def test_actor_mailbox_second_compaction_trigger_discards_runtime_conversation(
    actor_loop_app: tuple[Yuubot, Path],
) -> None:
    app, workspace = actor_loop_app
    actor = app.create_actor(
        ActorConfig(
            id="amy",
            name="Amy",
            workspace=str(workspace),
            model=ModelCard(selector="fake"),
            context_compression_tokens=5,
        ),
        PromptConditionedProvider(
            rules=[
                (prompt_contains("Summarize the current work"), reply_text("summary text", usage={"input_tokens": 1})),
                (
                    user_message_contains("automatic context compression continuation"),
                    reply_text("continued", usage={"input_tokens": 5}),
                ),
                (user_message_contains("first"), reply_text("old done", usage={"input_tokens": 5})),
            ]
        ),
    )

    await actor.handle_mailbox_message(ActorMessage(text="first", source={"inbound_kind": "actor_inbound"}))

    stopped_event = next(event for event in app.runtime.eventbus.events if event.kind == "actor.context_compaction_stopped")
    stopped_conversation = str(stopped_event.payload["conversation_id"])
    assert actor._mailbox_conversation is None
    assert actor.status == "idle"
    assert not app.runtime.conversations.has(stopped_conversation)
    assert await app.runtime.history.conversation_meta(stopped_conversation) is not None
    assert await app.runtime.state.load_costs(stopped_conversation)


@pytest.mark.asyncio
async def test_actor_explicit_conversation_does_not_compact(actor_loop_app: tuple[Yuubot, Path]) -> None:
    app, workspace = actor_loop_app
    actor = app.create_actor(
        ActorConfig(
            id="amy",
            name="Amy",
            workspace=str(workspace),
            model=ModelCard(selector="fake"),
            context_compression_tokens=5,
        ),
        PromptConditionedProvider(rules=[(user_message_contains("first"), reply_text("ok", usage={"input_tokens": 5}))]),
    )

    await actor.handle_mailbox_message(
        ActorMessage(text="first", conversation_id="explicit", source={"inbound_kind": "actor_inbound"})
    )

    assert actor._mailbox_conversation is None
    assert all(event.kind != "actor.context_compacted" for event in app.runtime.eventbus.events)


@pytest.mark.asyncio
async def test_task_delivery_busy_does_not_append_developer_notice(tmp_path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    provider = BlockingProvider()
    actor = app.create_actor(
        ActorConfig(
            id="amy",
            name="Amy",
            workspace=str(tmp_path / "workspace"),
            model=ModelCard(selector="fake"),
        ),
        provider,
    )
    try:
        task = asyncio.create_task(app.chat("amy", "first", conversation_id="explicit"))
        await provider.started.wait()

        await actor.handle_mailbox_message(
            ActorMessage(
                text="task done",
                conversation_id="explicit",
                source={"inbound_kind": "task_delivery"},
            )
        )

        items = await app.runtime.history.load_interaction_wrapped("explicit")
        assert _input_roles(items) == ["user"]

        provider.release.set()
        await task
        items = await app.runtime.history.load_interaction_wrapped("explicit")
        assert _input_roles(items) == ["user"]
        assert items[-1]["kind"] == "gen_text"
    finally:
        provider.release.set()
        await app.shutdown()
