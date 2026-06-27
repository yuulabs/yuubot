"""Conversation read-model resolution tests."""

from __future__ import annotations

import yuullm

from tests.helpers import insert_echo_actor_resources
from yuubot.core.bindings import (
    agent_binding_from_resolved_conversation,
    resolve_conversation,
)
from yuubot.core.conversations import ConversationStore
from yuubot.core.validation import GenerationParams
from yuubot.resources.store.models import ActorORM, LLMBackendORM


async def test_resolve_conversation_hydrates_actor_refs_and_frozen_history(
    resources,
) -> None:
    inserted = await insert_echo_actor_resources(
        resources.repository,
        actor_id="conversation-reader",
        system_prompt="persona v1",
    )
    store = ConversationStore(
        resources.store,
        secret_codec=resources.repository.secret_codec,
    )
    conversation = await store.create_conversation_row(
        conversation_id="conv-reader",
        actor_id=inserted.actor.id,
        title="Conversation Reader",
    )
    await store.append_history_items(
        conversation.conversation_id,
        [
            yuullm.system("frozen persona v1"),
            yuullm.user("hello"),
        ],
    )

    await resources.repository.update(
        ActorORM,
        inserted.actor.id,
        persona_prompt="persona v2",
        model="gpt-4",
    )

    resolved = await resolve_conversation(
        resources.repository,
        conversation.conversation_id,
    )

    assert resolved.conversation.conversation_id == conversation.conversation_id
    assert resolved.actor.id == inserted.actor.id
    assert resolved.capability_set.id == inserted.actor.capability_set_id
    assert resolved.llm_backend.id == inserted.actor.llm_backend_id
    assert resolved.persona_prompt == "persona v2"
    assert resolved.model == "gpt-4"
    assert [item.item_kind for item in resolved.history] == ["message", "message"]
    assert "frozen persona v1" in resolved.history[0].item_json


async def test_conversation_agent_binding_merges_generation_params_once(
    resources,
) -> None:
    inserted = await insert_echo_actor_resources(
        resources.repository,
        actor_id="generation-reader",
    )
    await resources.repository.update(
        ActorORM,
        inserted.actor.id,
        generation_override=GenerationParams(
            temperature=0.2,
            stop=["DONE"],
        ),
    )
    inserted.llm_backend.default_generation_params = GenerationParams(
        max_tokens=500,
        temperature=0.7,
        top_p=0.9,
    )
    await resources.repository.update(
        LLMBackendORM,
        inserted.llm_backend.id,
        default_generation_params=inserted.llm_backend.default_generation_params,
    )
    store = ConversationStore(
        resources.store,
        secret_codec=resources.repository.secret_codec,
    )
    conversation = await store.create_conversation_row(
        conversation_id="generation-conv",
        actor_id=inserted.actor.id,
    )

    resolved = await resolve_conversation(
        resources.repository,
        conversation.conversation_id,
    )
    binding = agent_binding_from_resolved_conversation(resolved)

    assert binding.llm.generation_params == GenerationParams(
        max_tokens=500,
        temperature=0.2,
        top_p=0.9,
        stop=["DONE"],
    )
