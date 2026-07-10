from __future__ import annotations

from support.api import SharedTestContext, conversation_history, ws_conversation_send
from support.assertions import tool_result_text
from support.llm_rules import all_of, call_tool, has_tool_spec, has_tool_specs, messages_contain_tool_result, reply_text, user_message_contains
from support.prompt_conditioned_llm import PromptConditionedProvider
from yuubot.domain import ContentItem
from yuubot.tools.read import ReadPayload, _execute_read


async def test_http_read_tool_reads_requested_slice(test_context: SharedTestContext) -> None:
    workspace = test_context.workspace
    workspace.mkdir(exist_ok=True)
    workspace.joinpath("big.txt").write_text("0\n1\n2\n3\n4\n5\n6\n7\n8\n9\n", encoding="utf-8")
    actor_id = await test_context.setup_actor(
        PromptConditionedProvider(
            [
                (messages_contain_tool_result("read"), reply_text("done")),
                (
                    all_of(
                        has_tool_specs("read", "edit", "write", "bash", "execute_python", "restart_kernel", "delegate"),
                        has_tool_spec("read"),
                        user_message_contains("read slice"),
                    ),
                    call_tool("read", {"path": "big.txt", "start_lo": 1, "end_lo": 3}),
                ),
            ]
        )
    )
    conversation_id = test_context.conversation_id("read-c1")
    await ws_conversation_send(
        test_context.server,
        "m1",
        actor_id,
        conversation_id,
        "read slice",
    )
    history = await conversation_history(test_context.server, conversation_id)
    assert tool_result_text(history) == "1\n2\n[truncated: lines 1-3 of 10]"


async def test_read_tool_returns_image_content_with_workspace_relative_path(test_context: SharedTestContext) -> None:
    workspace = test_context.workspace
    image_path = workspace / "uploads" / "image-png" / "cat.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"image-bytes")

    result = await _execute_read(
        workspace,
        ReadPayload("uploads/image-png/cat.png"),
        "gpt-test",
        True,
    )

    assert result == [
        ContentItem("image", path="uploads/image-png/cat.png", mime="image/png"),
    ]
