from __future__ import annotations

from support.api import (
    SharedTestContext,
    conversation_history,
    enable_integration,
    wait_for_history_kind,
    ws_conversation_send,
)
from support.assertions import interaction_kinds, runtime_developer_notice_count, tool_result_text
from support.llm_rules import (
    all_of,
    call_tool,
    has_tool_spec,
    integration_sdk_documented,
    messages_contain_tool_result,
    reply_text,
    runtime_developer_notice,
    user_message_contains,
    user_message_has_text_and_path,
)
from support.prompt_conditioned_llm import PromptConditionedProvider

INTEGRATION_INSPECT_CODE = (
    "import asyncio\n"
    "import yext.github\n"
    "await asyncio.sleep(0)\n"
    "repo = yext.github.repo()\n"
    "print(repo.owner + '/' + repo.name)\n"
    "print(repo.token)\n"
    "import os\n"
    "print(os.environ['YEXT_WEB_MAX_READ_CHARS'])\n"
)

def _tasks_inspect_code(task_name: str) -> str:
    return (
        "import yb.tasks\n"
        f"task = await yb.tasks.submit({task_name!r}, 'echo task-probe', 'prompt test')\n"
        "print(task.name)\n"
        "print(await task.status())\n"
        f"tasks = await yb.tasks.list_tasks(name_glob={task_name!r})\n"
        "print(len(tasks))\n"
    )


def _integration_llm() -> PromptConditionedProvider:
    return PromptConditionedProvider(
        rules=[
            (messages_contain_tool_result("execute_python"), reply_text("integration-ok")),
            (
                all_of(integration_sdk_documented("yext.github"), has_tool_spec("execute_python")),
                call_tool("execute_python", {"code": INTEGRATION_INSPECT_CODE}),
            ),
        ]
    )


async def test_http_integration_docs_in_prompt_enable_execute_python_call(test_context: SharedTestContext) -> None:
    await test_context.put_integration(
        "github",
        name="gh",
        config={"access_token": "token", "default_owner": "yuulabs", "default_repo": "yuubot"},
    )
    await enable_integration(test_context.server, "github")
    await test_context.put_integration("tavily_web", name="web", config={"api_key": "web-token", "max_read_chars": 42})
    await enable_integration(test_context.server, "tavily_web")
    actor_id = await test_context.setup_actor(_integration_llm())
    conversation_id = test_context.conversation_id("integration-c1")
    await ws_conversation_send(
        test_context.server,
        command_id="m1",
        actor_id=actor_id,
        conversation_id=conversation_id,
        content="inspect integration context",
    )
    history = await conversation_history(test_context.server, conversation_id)
    assert history[-1]["payload"] == {"text": "integration-ok"}
    assert tool_result_text(history) == "yuulabs/yuubot\ntoken\n42\n"


def _tasks_llm(task_name: str) -> PromptConditionedProvider:
    return PromptConditionedProvider(
        rules=[
            (messages_contain_tool_result("execute_python"), reply_text("tasks-ok")),
            (
                all_of(integration_sdk_documented("yb.tasks"), has_tool_spec("execute_python")),
                call_tool("execute_python", {"code": _tasks_inspect_code(task_name)}),
            ),
        ]
    )


async def test_http_tasks_docs_in_prompt_enable_submit_call(test_context: SharedTestContext) -> None:
    task_name = test_context.name("probe")
    actor_id = await test_context.setup_actor(_tasks_llm(task_name))
    conversation_id = test_context.conversation_id("tasks-c1")
    await ws_conversation_send(
        test_context.server,
        command_id="m1",
        actor_id=actor_id,
        conversation_id=conversation_id,
        content="start a background shell task",
    )
    history = await wait_for_history_kind(test_context.server, conversation_id, "gen_text")
    assert history[-1]["payload"] == {"text": "tasks-ok"}
    assert tool_result_text(history) == f"{task_name}\nrunning\n1\n"


async def test_http_integration_docs_missing_prevents_execute_python_call(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(_integration_llm())
    conversation_id = test_context.conversation_id("integration-missing")
    await ws_conversation_send(
        test_context.server,
        command_id="m1",
        actor_id=actor_id,
        conversation_id=conversation_id,
        content="inspect integration context",
    )
    history = await conversation_history(test_context.server, conversation_id)
    assert history[-1]["payload"] != {"text": "integration-ok"}
    assert not any(item["kind"] == "tool_result" for item in history)


async def test_http_tool_loop_continues_when_llm_sees_prior_tool_result(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(
        PromptConditionedProvider(
            rules=[
                (messages_contain_tool_result("write"), reply_text("done")),
                (
                    all_of(has_tool_spec("write"), user_message_contains("write note")),
                    call_tool("write", {"path": "note.txt", "content": "hello"}),
                ),
            ]
        )
    )
    conversation_id = test_context.conversation_id("write-c1")
    await ws_conversation_send(test_context.server, command_id="m1", actor_id=actor_id, conversation_id=conversation_id, content="write note")
    history = await conversation_history(test_context.server, conversation_id)
    assert history[-1]["payload"] == {"text": "done"}
    assert (test_context.workspace / "note.txt").read_text(encoding="utf-8") == "hello"
    assert interaction_kinds(history) == ["input", "gen_tool_call", "tool_result", "gen_text"]


async def test_http_python_reset_notice_enables_second_turn_after_execute_python(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(
        PromptConditionedProvider(
            rules=[
                (runtime_developer_notice("previous execute_python session has been reset"), reply_text("continued")),
                (messages_contain_tool_result("execute_python"), reply_text("python-ran")),
                (
                    all_of(has_tool_spec("execute_python"), user_message_contains("run python")),
                    call_tool("execute_python", {"code": "x = 1"}),
                ),
            ]
        )
    )
    conversation_id = test_context.conversation_id("python-reset-c1")
    await ws_conversation_send(test_context.server, command_id="m1", actor_id=actor_id, conversation_id=conversation_id, content="run python")
    await ws_conversation_send(test_context.server, command_id="m2", actor_id=actor_id, conversation_id=conversation_id, content="continue")
    history = await conversation_history(test_context.server, conversation_id)
    assert history[-1]["payload"] == {"text": "continued"}
    assert runtime_developer_notice_count(history, "previous execute_python session has been reset") == 1


async def test_http_multimodal_input_visible_to_llm_via_conditional_reply(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(
        PromptConditionedProvider(
            rules=[
                (
                    user_message_has_text_and_path("see this", "uploads/text-plain/report.txt"),
                    reply_text("saw-multimodal"),
                ),
            ]
        ),
    )
    conversation_id = test_context.conversation_id("multimodal-c1")
    await ws_conversation_send(
        test_context.server,
        command_id="m1",
        actor_id=actor_id,
        conversation_id=conversation_id,
        content=[
            {"kind": "text", "text": "see this", "mime": "text/plain"},
            {"kind": "file", "path": "uploads/text-plain/report.txt", "mime": "text/plain"},
        ],
    )
    history = await conversation_history(test_context.server, conversation_id)
    assert history[-1]["payload"] == {"text": "saw-multimodal"}
