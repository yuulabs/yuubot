from __future__ import annotations

from typing import cast

import pytest

from support.api import (
    JsonObject,
    SharedTestContext,
    conversation_history,
    recv_ws_frames,
    ws_conversation_send,
)
from support.assertions import interaction_kinds, tool_result_text
from support.llm_rules import (
    all_of,
    call_tool,
    has_tool_spec,
    messages_contain_tool_result,
    reply_text,
    user_message_contains,
)
from support.prompt_conditioned_llm import PromptConditionedProvider


async def test_ask_user_pauses_persists_and_resumes(
    test_context: SharedTestContext,
) -> None:
    provider = PromptConditionedProvider(
        [
            (
                messages_contain_tool_result("ask_user"),
                reply_text("Thanks, continuing."),
            ),
            (
                all_of(
                    user_message_contains("help me choose"), has_tool_spec("ask_user")
                ),
                call_tool(
                    "ask_user",
                    {
                        "questions": [
                            {
                                "id": "color",
                                "header": "Theme",
                                "question": "Which color?",
                                "options": [{"label": "Blue", "description": "Calm"}],
                            },
                            {"id": "note", "question": "Anything else?", "options": []},
                        ]
                    },
                    call_id="ask-1",
                ),
            ),
        ]
    )
    actor_id = await test_context.setup_actor(provider)
    conversation_id = test_context.conversation_id("ask-user")

    frames = await ws_conversation_send(
        test_context.server, "m1", actor_id, conversation_id, "help me choose"
    )
    assert cast(JsonObject, frames[-1]["payload"])["continues"] is False
    history = await conversation_history(test_context.server, conversation_id)
    assert interaction_kinds(history) == ["input", "gen_tool_call"]

    answer_frames = await recv_ws_frames(
        test_context.server,
        [
            {
                "id": "a1",
                "type": "conversation.answer",
                "payload": {
                    "conversation_id": conversation_id,
                    "tool_call_id": "ask-1",
                    "answers": [
                        {"id": "color", "answer": "Blue"},
                        {"id": "note", "answer": "Keep it simple"},
                    ],
                },
            }
        ],
        lambda frame, _: (
            frame.get("type") == "conversation.commit"
            and cast(JsonObject, frame["payload"]).get("continues") is False
        ),
    )
    assert answer_frames[0]["type"] == "conversation.answer.accepted"
    history = await conversation_history(test_context.server, conversation_id)
    assert interaction_kinds(history) == [
        "input",
        "gen_tool_call",
        "tool_result",
        "gen_text",
    ]
    assert '"status":"answered"' in tool_result_text(history)
    assert history[-1]["payload"] == {"text": "Thanks, continuing."}


async def test_ask_user_rejects_ordinary_send_while_pending(
    test_context: SharedTestContext,
) -> None:
    provider = PromptConditionedProvider(
        [
            (
                all_of(user_message_contains("ask"), has_tool_spec("ask_user")),
                call_tool(
                    "ask_user",
                    {"questions": [{"id": "q", "question": "Answer?"}]},
                    call_id="ask-2",
                ),
            ),
        ]
    )
    actor_id = await test_context.setup_actor(provider)
    conversation_id = test_context.conversation_id("ask-user-busy")
    await ws_conversation_send(
        test_context.server, "m1", actor_id, conversation_id, "ask"
    )

    with pytest.raises(AssertionError, match="conversation_awaiting_input"):
        await ws_conversation_send(
            test_context.server, "m2", actor_id, conversation_id, "another message"
        )
