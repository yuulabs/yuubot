from __future__ import annotations

import asyncio
from typing import cast

from support.api import (
    JsonObject,
    SharedTestContext,
    bootstrap,
    conversation_history,
    conversation_summary,
    disable_actor,
    enable_actor,
    post_inbound,
    wait_for_history_kind,
    ws_conversation_send,
)
from support.assertions import interaction_kinds, payload as history_payload, text_content, tool_result_text
from support.llm_rules import (
    all_of,
    call_tool,
    has_tool_spec,
    messages_contain_tool_result,
    reply_text,
    user_message_contains,
)
from support.llm_fakes import scripted_reply
from support.exec_py import ExecPyModuleContext
from support.prompt_conditioned_llm import PromptConditionedProvider


async def test_http_conversation_runs_tool_loop(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(
        PromptConditionedProvider(
            [
                (messages_contain_tool_result("write"), reply_text("done", {"input_tokens": 1, "output_tokens": 1})),
                (
                    all_of(has_tool_spec("write"), user_message_contains("write note")),
                    call_tool("write", {"path": "note.txt", "content": "hello"}),
                ),
            ]
        )
    )
    conversation_id = test_context.conversation_id("tool-c1")
    await ws_conversation_send(test_context.server, "m1", actor_id, conversation_id, "write note")
    history = await conversation_history(test_context.server, conversation_id)
    summary = await conversation_summary(test_context.server, conversation_id)
    assert (test_context.workspace / "note.txt").read_text() == "hello"
    assert history[-1]["payload"] == {"text": "done"}
    assert summary["actor_id"] == actor_id
    assert summary["status"] == "closed"
    assert interaction_kinds(history) == ["input", "gen_tool_call", "tool_result", "gen_text"]


async def test_http_enabled_actor_receives_inbound_messages(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("hello"), enable=False)
    route = test_context.route_id("qq-group")
    conversation_id = test_context.conversation_id("route-c1")
    await test_context.create_route(route_id=route, pattern=route, actor_id=actor_id)
    await enable_actor(test_context.server, actor_id)
    inbound = await post_inbound(test_context.server, route, "hi", conversation_id)
    assert inbound["delivered"] is True
    history = await wait_for_history_kind(test_context.server, conversation_id, "gen_text")
    await disable_actor(test_context.server, actor_id)
    actor = next(item for item in cast(list[JsonObject], (await bootstrap(test_context.server))["actors"]) if item["id"] == actor_id)
    assert history[-1]["payload"] == {"text": "hello"}
    assert actor["status"] == "disabled"


async def test_http_actor_blocked_state_visible_in_bootstrap(test_context: SharedTestContext) -> None:
    from yuubot.domain import StreamEvent, StreamStopPayload, TextDeltaPayload
    from yuubot.llm import ScriptedStream

    actor_id = await test_context.setup_actor(
        ScriptedStream(
            [
                [
                    StreamEvent("text-1", "text_delta", TextDeltaPayload("partial")),
                    StreamEvent("stop", "stream_stop", StreamStopPayload("length")),
                ]
            ]
        ),
        enable=False,
    )
    route = test_context.route_id("mailbox")
    conversation_id = test_context.conversation_id("blocked-c1")
    await test_context.create_route(route_id=route, pattern=route, actor_id=actor_id)
    await enable_actor(test_context.server, actor_id)
    await post_inbound(test_context.server, route, "hi", conversation_id)
    actor: JsonObject = {}
    for _ in range(100):
        actor = next(item for item in cast(list[JsonObject], (await bootstrap(test_context.server))["actors"]) if item["id"] == actor_id)
        if actor["status"] == "blocked":
            break
        await asyncio.sleep(0.01)
    summary = await conversation_summary(test_context.server, conversation_id)
    assert actor["status"] == "blocked"
    assert summary["status"] == "blocked"
    assert summary["last_error"] == {"reason": "length"}


async def test_http_two_actors_use_distinct_workspaces(test_context: SharedTestContext) -> None:
    actor_a = await test_context.setup_actor(
        PromptConditionedProvider(
            [
                (messages_contain_tool_result("write"), reply_text("done-a")),
                (
                    all_of(has_tool_spec("write"), user_message_contains("write note a")),
                    call_tool("write", {"path": "note-a.txt", "content": "alpha"}),
                ),
            ]
        ),
        actor_id=test_context.name("actor-a"),
        provider_id=test_context.name("provider-a"),
        workspace=test_context.workspace,
    )
    actor_b = await test_context.setup_actor(
        PromptConditionedProvider(
            [
                (messages_contain_tool_result("write"), reply_text("done-b")),
                (
                    all_of(has_tool_spec("write"), user_message_contains("write note b")),
                    call_tool("write", {"path": "note-b.txt", "content": "beta"}),
                ),
            ]
        ),
        actor_id=test_context.name("actor-b"),
        provider_id=test_context.name("provider-b"),
        workspace=test_context.workspace_alt,
    )
    conversation_a = test_context.conversation_id("multi-a")
    conversation_b = test_context.conversation_id("multi-b")
    await ws_conversation_send(
        test_context.server,
        "m1",
        actor_a,
        conversation_a,
        "write note a",
    )
    await ws_conversation_send(
        test_context.server,
        "m2",
        actor_b,
        conversation_b,
        "write note b",
    )
    assert (test_context.workspace / "note-a.txt").read_text(encoding="utf-8") == "alpha"
    assert (test_context.workspace_alt / "note-b.txt").read_text(encoding="utf-8") == "beta"
    assert not (test_context.workspace / "note-b.txt").exists()
    assert not (test_context.workspace_alt / "note-a.txt").exists()
    snapshot = await bootstrap(test_context.server)
    actor_ids = {item["id"] for item in cast(list[JsonObject], snapshot["actors"])}
    assert actor_a in actor_ids
    assert actor_b in actor_ids


async def test_http_execute_python_receives_enabled_integration_context(exec_py_context: ExecPyModuleContext) -> None:
    code = (
        "import asyncio\n"
        "import yext.github\n"
        "await asyncio.sleep(0)\n"
        "repo = yext.github.repo()\n"
        "print(repo.owner + '/' + repo.name)\n"
        "print(repo.token)\n"
        "import os\n"
        "print(os.environ['YEXT_WEB_MAX_READ_CHARS'])\n"
    )
    await exec_py_context.reset_state()
    await exec_py_context.put_integration(
        "github",
        name="gh",
        config={"access_token": "token", "default_owner": "yuulabs", "default_repo": "yuubot"},
    )
    await exec_py_context.enable_integration("github")
    await exec_py_context.put_integration("web", name="web", config={"tavily_api_key": "web-token", "max_read_chars": 42})
    await exec_py_context.enable_integration("web")
    await exec_py_context.activate(
        PromptConditionedProvider(
            [
                (
                    all_of(messages_contain_tool_result("execute_python"), user_message_contains("inspect github context")),
                    reply_text("done"),
                ),
                (
                    all_of(has_tool_spec("execute_python"), user_message_contains("inspect github context")),
                    call_tool("execute_python", {"code": code}),
                ),
                (user_message_contains("continue"), reply_text("next")),
            ]
        )
    )
    conversation_id = exec_py_context.conversation_id("py-c1")
    await ws_conversation_send(
        exec_py_context.server,
        "m1",
        exec_py_context.actor_id,
        conversation_id,
        "inspect github context",
    )
    await ws_conversation_send(
        exec_py_context.server,
        "m2",
        exec_py_context.actor_id,
        conversation_id,
        "continue",
    )
    history = await conversation_history(exec_py_context.server, conversation_id)
    developer_messages = [item for item in history if item["kind"] == "input" and history_payload(item).get("role") == "developer"]
    assert tool_result_text(history) == "yuulabs/yuubot\ntoken\n42\n"
    assert len(developer_messages) == 1
    assert "execute_python session has been reset" in text_content(developer_messages[0])


async def test_http_execute_python_returns_ipython_traceback(exec_py_context: ExecPyModuleContext) -> None:
    await exec_py_context.reset_state()
    await exec_py_context.activate(
        PromptConditionedProvider(
            [
                (messages_contain_tool_result("execute_python"), reply_text("done")),
                (
                    all_of(has_tool_spec("execute_python"), user_message_contains("show error")),
                    call_tool("execute_python", {"code": "1/0"}),
                ),
            ]
        )
    )
    conversation_id = exec_py_context.conversation_id("py-error")
    await ws_conversation_send(
        exec_py_context.server,
        "m1",
        exec_py_context.actor_id,
        conversation_id,
        "show error",
    )
    history = await conversation_history(exec_py_context.server, conversation_id)
    text = tool_result_text([item for item in history if item["kind"] == "tool_result"])
    assert "ZeroDivisionError" in text
    assert "execute_python failed" not in text
