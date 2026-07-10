from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from support.integration_app import reset_actor_app_state
from yuubot.actor import ActorConfig
from yuubot.actor.prompt import REAL_TIME_CONTEXT_MARKER
from yuubot.app import Yuubot
from yuubot.domain import ActorMessage
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


@pytest.fixture(scope="module")
async def real_time_app(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[tuple[Yuubot, Path]]:
    data_dir = tmp_path_factory.mktemp("real-time-module") / "data"
    workspace = tmp_path_factory.mktemp("real-time-module-ws") / "workspace"
    app = await Yuubot.create(data_dir)
    try:
        yield app, workspace
    finally:
        await app.shutdown()


@pytest.fixture(autouse=True)
async def _reset_real_time_module_state(real_time_app: tuple[Yuubot, Path]) -> AsyncIterator[None]:
    yield
    await reset_actor_app_state(real_time_app[0])


@pytest.mark.asyncio
async def test_mailbox_user_message_includes_actor_mode_context(real_time_app: tuple[Yuubot, Path]) -> None:
    app, workspace = real_time_app
    actor = app.create_actor(
        ActorConfig(
            id="amy",
            name="Amy",
            workspace=str(workspace),
            model="fake",
        ),
        scripted_reply("ok"),
    )
    await actor.handle_mailbox_message(ActorMessage("webhook ping", source={"inbound_kind": "app_webhook"}))
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


@pytest.mark.asyncio
async def test_direct_user_message_includes_conversation_mode_context(real_time_app: tuple[Yuubot, Path]) -> None:
    app, workspace = real_time_app
    app.create_actor(
        ActorConfig(
            id="amy",
            name="Amy",
            workspace=str(workspace),
            model="fake",
        ),
        scripted_reply("ok"),
    )
    conversation, _ = await app.chat("amy", "hello", conversation_id="direct-chat")
    items = await app.runtime.history.load_wrapped(conversation.id)
    user_text = _user_text(items)

    assert user_text.startswith(REAL_TIME_CONTEXT_MARKER)
    assert "mode: conversation" in user_text
    assert "hello" in user_text


@pytest.mark.asyncio
async def test_developer_callback_does_not_attach_mode_context(real_time_app: tuple[Yuubot, Path]) -> None:
    app, workspace = real_time_app
    actor = app.create_actor(
        ActorConfig(
            id="amy",
            name="Amy",
            workspace=str(workspace),
            model="fake",
        ),
        scripted_reply("ok"),
    )
    await actor.handle_mailbox_message(
        ActorMessage(
            "task done",
            "explicit",
            {"inbound_kind": "conversation_callback"},
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
