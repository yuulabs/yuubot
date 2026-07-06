from __future__ import annotations

import pytest

from yuubot.actor import ActorConfig
from yuubot.domain import ActorMessage, ModelCard
from yuubot.llm import scripted_reply
from yuubot.app import Yuubot


def _input_roles(items: list[dict[str, object]]) -> list[str]:
    roles: list[str] = []
    for item in items:
        if item.get("kind") != "input":
            continue
        payload = item.get("payload")
        if isinstance(payload, dict):
            roles.append(str(payload.get("role")))
    return roles


@pytest.mark.asyncio
async def test_actor_inbound_without_conversation_reuses_default_conversation(tmp_path) -> None:
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
        await actor.handle_mailbox_message(ActorMessage(text="first", source={"inbound_kind": "actor_inbound"}))
        first_conversation = actor._mailbox_conversation
        assert first_conversation is not None

        await actor.handle_mailbox_message(ActorMessage(text="second", source={"inbound_kind": "actor_inbound"}))

        assert actor._mailbox_conversation == first_conversation
        items = await app.runtime.history.load_interaction_wrapped(first_conversation)
        assert _input_roles(items) == ["user", "user"]
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_actor_inbound_without_conversation_creates_new_default_after_ttl(tmp_path) -> None:
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
        await actor.handle_mailbox_message(ActorMessage(text="first", source={"inbound_kind": "actor_inbound"}))
        first_conversation = actor._mailbox_conversation
        assert first_conversation is not None

        app.runtime.conversations.ttl_s = -1
        await actor.handle_mailbox_message(ActorMessage(text="second", source={"inbound_kind": "actor_inbound"}))

        assert actor._mailbox_conversation is not None
        assert actor._mailbox_conversation != first_conversation
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_actor_explicit_conversation_and_callback_roles(tmp_path) -> None:
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
    finally:
        await app.shutdown()
