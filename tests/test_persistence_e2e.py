from __future__ import annotations

from pathlib import Path
from typing import cast

from support.api import (
    JsonObject,
    SharedTestContext,
    boot_app,
    conversation_usage,
    conversation_history,
    conversation_summary,
    recv_ws_frames,
    running_server,
    setup_amy,
    ws_conversation_send,
)
from support.assertions import interaction_kinds, tool_result_text
from support.llm_rules import (
    all_of,
    call_tool,
    has_tool_spec,
    messages_contain_tool_result,
    reply_blocked,
    reply_text,
    runtime_developer_notice,
    user_message_contains,
)
from support.exec_py import ExecPyModuleContext
from support.prompt_conditioned_llm import PromptConditionedProvider


def _write_loop_llm() -> PromptConditionedProvider:
    return PromptConditionedProvider(
        [
            (messages_contain_tool_result("write"), reply_text("done", {"input_tokens": 1, "output_tokens": 1})),
            (
                all_of(has_tool_spec("write"), user_message_contains("write note")),
                call_tool("write", {"path": "note.txt", "content": "hello"}),
            ),
        ]
    )


def _python_reset_llm() -> PromptConditionedProvider:
    return PromptConditionedProvider(
        [
            (runtime_developer_notice("previous execute_python session has been reset"), reply_text("continued")),
            (messages_contain_tool_result("execute_python"), reply_text("python-ran")),
            (
                all_of(has_tool_spec("execute_python"), user_message_contains("run python")),
                call_tool("execute_python", {"code": "x = 1"}),
            ),
        ]
    )


async def test_http_conversation_facade_history_hides_prefix(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(
        PromptConditionedProvider([(user_message_contains("hello"), reply_text("hi"))])
    )
    conversation_id = test_context.conversation_id("prefix-c1")
    await ws_conversation_send(test_context.server, "m1", actor_id, conversation_id, "hello")
    facade = await conversation_history(test_context.server, conversation_id)
    summary = await conversation_summary(test_context.server, conversation_id)
    assert "tool_specs" not in interaction_kinds(facade)
    assert "system_prompt" not in interaction_kinds(facade)
    assert summary["message_count"] == 2
    assert summary["title"] == "hello"
    assert interaction_kinds(facade) == ["input", "gen_text"]


async def test_http_resume_keeps_conversation_usable(exec_py_context: ExecPyModuleContext) -> None:
    await exec_py_context.reset_state()
    await exec_py_context.activate(_python_reset_llm())
    conversation_id = exec_py_context.conversation_id("resume-c1")
    await ws_conversation_send(
        exec_py_context.server,
        "m1",
        exec_py_context.actor_id,
        conversation_id,
        "run python",
    )
    await ws_conversation_send(
        exec_py_context.server,
        "m2",
        exec_py_context.actor_id,
        conversation_id,
        "continue",
    )
    history = await conversation_history(exec_py_context.server, conversation_id)
    assert history[-1]["payload"] == {"text": "continued"}
    assert interaction_kinds(history).count("gen_tool_call") == 1


async def test_http_llm_rounds_append_usage_records(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(_write_loop_llm())
    conversation_id = test_context.conversation_id("usage-c1")
    await ws_conversation_send(test_context.server, "m1", actor_id, conversation_id, "write note")
    usage = await conversation_usage(test_context.server, conversation_id)
    summary = await conversation_summary(test_context.server, conversation_id)
    assert len(usage) == 2
    assert summary["status"] == "closed"


async def test_http_blocked_conversation_persists_status_and_error(tmp_path: Path) -> None:
    app = await boot_app(
        tmp_path / "data",
        PromptConditionedProvider([(user_message_contains("hi"), reply_blocked("length"))]),
    )
    async with running_server(app) as server:
        await setup_amy(server, tmp_path)
        frames = await recv_ws_frames(
            server,
            [
                {
                    "id": "m1",
                    "type": "conversation.send",
                    "payload": {
                        "actor_id": "amy",
                        "conversation_id": "blocked-c1",
                        "content": [{"kind": "text", "text": "hi"}],
                    },
                }
            ],
            lambda frame, _: frame.get("type") == "error",
        )
        error = frames[-1]
        assert cast(JsonObject, error["error"])["code"] == "conversation_blocked"
        summary = await conversation_summary(server, "blocked-c1")
    assert summary["status"] == "blocked"
    assert summary["last_error"] == {"reason": "length"}

    restored = await boot_app(tmp_path / "data")
    async with running_server(restored) as server:
        restored_summary = await conversation_summary(server, "blocked-c1")
    assert restored_summary["status"] == "blocked"
    assert restored_summary["last_error"] == {"reason": "length"}


async def test_http_tool_side_effect_and_history_alignment(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(_write_loop_llm())
    conversation_id = test_context.conversation_id("align-c1")
    await ws_conversation_send(test_context.server, "m1", actor_id, conversation_id, "write note")
    history = await conversation_history(test_context.server, conversation_id)
    assert (test_context.workspace / "note.txt").read_text(encoding="utf-8") == "hello"
    assert interaction_kinds(history) == ["input", "gen_tool_call", "tool_result", "gen_text"]
    assert tool_result_text(history) == "wrote note.txt"
