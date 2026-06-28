"""Conversation send pipeline."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import yuullm
from yuuagents import Agent

from yuubot.resources.records import ConversationRecord

from .bindings import ConversationBindingConflict, ConversationSendBinding
from .timing import _conversation_timing_span

if TYPE_CHECKING:
    from .manager import ConversationManager


def _agent_id(agent: Agent) -> str:
    return agent.id


async def send_message(
    manager: ConversationManager,
    *,
    conversation_id: str,
    text: str,
    binding: ConversationSendBinding | None = None,
    message_id: str | None = None,
) -> tuple[ConversationRecord, str]:
    """Persist a user Message and run the conversation turn.

    ``binding`` carries first-send binding fields (``actor_id`` etc.).
    On the first real send it is required and the conversation row is
    created from it; on subsequent sends the persisted binding is the
    authority and any conflicting ``binding.actor_id`` raises
    :class:`ConversationBindingConflict`.

    Returns the persisted ``ConversationRecord`` and the user message id.
    The turn itself runs on a background task — the method returns
    before the turn completes, mirroring the prior 202 semantics.
    """
    existing = manager._in_flight_tasks.get(conversation_id)
    if existing is not None and not existing.done():
        raise RuntimeError("conversation turn is still stopping")

    with _conversation_timing_span(
        "conversation.send",
        "conversation_exists",
        conversation_id=conversation_id,
    ) as timing:
        exists = await manager.store.conversation_exists(conversation_id)
        timing.attrs(exists=exists)
    if exists:
        with _conversation_timing_span(
            "conversation.send",
            "existing_conversation_loaded",
            conversation_id=conversation_id,
        ):
            conversation = await manager._require_conversation(conversation_id)
            _check_subsequent_send_binding(
                conversation=conversation,
                binding=binding,
            )
    else:
        with _conversation_timing_span(
            "conversation.send",
            "first_send_conversation_created",
            conversation_id=conversation_id,
        ):
            conversation = await _create_first_send_conversation(
                manager,
                conversation_id=conversation_id,
                binding=binding,
            )

    runtime_cached = conversation_id in manager._runtimes
    with _conversation_timing_span(
        "conversation.send",
        "runtime_ready",
        conversation_id=conversation_id,
        runtime_cached=runtime_cached,
    ):
        runtime = await manager._runtime_for(conversation)

    # Cache hit on the in-memory agent short-circuits the DB history
    # read on the hot path. Cache miss (restart / idle expiry) and
    # first-send both branch inside runtime.ensure_conversation_agent.
    with _conversation_timing_span(
        "conversation.send",
        "history_loaded",
        conversation_id=conversation_id,
    ) as timing:
        if exists and runtime.conversation_agents.get(conversation_id) is None:
            history = await manager.store.history(conversation_id)
        else:
            history = []
        timing.attrs(history_count=len(history))

    with _conversation_timing_span(
        "conversation.send",
        "conversation_agent_ready",
        conversation_id=conversation_id,
    ) as timing:
        agent = await runtime.ensure_conversation_agent(conversation_id, history)
        timing.attrs(agent_id=_agent_id(agent))
    manager._agent_to_conversation[_agent_id(agent)] = conversation_id

    # Persist the freshly-built prompt prefix on the first-send path
    # (prefix lives inside agent.history now). Persisted before the
    # user Message so ordering stays [tool_specs?, system, user, ...].
    if not exists:
        with _conversation_timing_span(
            "conversation.send",
            "first_send_prefix_persisted",
            conversation_id=conversation_id,
        ) as timing:
            prefix = list(agent.history)
            if prefix:
                await manager.store.append_history_items(conversation_id, prefix)
            timing.attrs(prefix_count=len(prefix))

    message_id = message_id or uuid.uuid4().hex
    user_message = yuullm.user(text)
    with _conversation_timing_span(
        "conversation.send",
        "user_message_persisted",
        conversation_id=conversation_id,
        message_id=message_id,
    ):
        await manager.store.append_history_item(conversation_id, user_message)

    with _conversation_timing_span(
        "conversation.send",
        "turn_task_started",
        conversation_id=conversation_id,
    ):
        manager._start_turn_task(
            conversation_id=conversation_id,
            runtime=runtime,
            message=user_message,
        )
    return conversation, message_id


def _check_subsequent_send_binding(
    conversation: ConversationRecord,
    binding: ConversationSendBinding | None,
) -> None:
    if binding is None:
        return
    supplied_actor = (binding.actor_id or "").strip()
    if supplied_actor and supplied_actor != conversation.actor_id:
        raise ConversationBindingConflict(conversation=conversation)


async def _create_first_send_conversation(
    manager: ConversationManager,
    *,
    conversation_id: str,
    binding: ConversationSendBinding | None,
) -> ConversationRecord:
    if binding is None or not binding.actor_id.strip():
        raise LookupError(
            f"first send for conversation {conversation_id!r} requires actor_id"
        )
    actor = await manager._active_actor(binding.actor_id.strip())
    await manager._require_capability_set(actor.capability_set_id)
    await manager._require_llm_backend(actor.llm_backend_id)

    return await manager.store.create_conversation_row(
        conversation_id=conversation_id,
        actor_id=actor.id,
        title="",
        reply_address="",
        metadata={},
    )
