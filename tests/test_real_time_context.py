from __future__ import annotations

import pytest

from yuubot.actor import ActorConfig
from yuubot.actor.prompt import REAL_TIME_CONTEXT_MARKER
from yuubot.app import Yuubot
from yuubot.domain import ActorMessage, ModelCard
from yuubot.llm import scripted_reply


def _user_text(items: list[dict[str, object]]) -> str:
    for item in items:
        if item.get("kind") != "input":
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict) or payload.get("role") != "user":
            continue
        content = payload.get("content")
        if not isinstance(content, list) or not content:
            return ""
        first = content[0]
        if isinstance(first, dict) and isinstance(first.get("text"), str):
            return first["text"]
    return ""


def _system_prompt(items: list[dict[str, object]]) -> str:
    for item in items:
        if item.get("kind") != "system_prompt":
            continue
        payload = item.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("text"), str):
            return payload["text"]
    return ""


@pytest.mark.asyncio
async def test_mailbox_user_message_includes_actor_mode_context(tmp_path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    actor = app.create_actor(
        ActorConfig(
            id="amy",
            name="Amy",
            workspace=str(tmp_path / "workspace"),
            model=ModelCard(selector="fake"),
        ),
        scripted_reply("ok"),
    )
    try:
        await actor.handle_mailbox_message(ActorMessage(text="webhook ping", source={"inbound_kind": "app_webhook"}))
        conversation_id = actor._mailbox_conversation
        assert conversation_id is not None
        items = await app.runtime.history.load_wrapped(conversation_id)
        user_text = _user_text(items)
        system_prompt = _system_prompt(items)

        assert user_text.startswith(REAL_TIME_CONTEXT_MARKER)
        assert "mode: actor" in user_text
        assert "now:" in user_text
        assert "webhook ping" in user_text
        assert "## Session modes" in system_prompt
        assert "\nnow:" not in system_prompt.split("# Real-Time Data\n", 1)[1]
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_direct_user_message_includes_conversation_mode_context(tmp_path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    actor = app.create_actor(
        ActorConfig(
            id="amy",
            name="Amy",
            workspace=str(tmp_path / "workspace"),
            model=ModelCard(selector="fake"),
        ),
        scripted_reply("ok"),
    )
    try:
        conversation, _ = await app.chat("amy", "hello", conversation_id="direct-chat")
        items = await app.runtime.history.load_wrapped(conversation.id)
        user_text = _user_text(items)

        assert user_text.startswith(REAL_TIME_CONTEXT_MARKER)
        assert "mode: conversation" in user_text
        assert "hello" in user_text
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_developer_callback_does_not_attach_mode_context(tmp_path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    actor = app.create_actor(
        ActorConfig(
            id="amy",
            name="Amy",
            workspace=str(tmp_path / "workspace"),
            model=ModelCard(selector="fake"),
        ),
        scripted_reply("ok"),
    )
    try:
        await actor.handle_mailbox_message(
            ActorMessage(
                text="task done",
                conversation_id="explicit",
                source={"inbound_kind": "conversation_callback"},
            )
        )
        items = await app.runtime.history.load_wrapped("explicit")
        user_messages = [
            item
            for item in items
            if item.get("kind") == "input"
            and isinstance(item.get("payload"), dict)
            and item["payload"].get("role") == "user"
        ]
        assert user_messages == []
    finally:
        await app.shutdown()
