from __future__ import annotations

from pathlib import Path

import pytest

from support.api import SharedTestContext, conversation_history, ws_conversation_send
from support.assertions import tool_result_text
from support.llm_rules import all_of, call_tool, has_tool_spec, messages_contain_tool_result, reply_text, user_message_contains
from support.prompt_conditioned_llm import PromptConditionedProvider
from yuubot.python.worker import KernelWorker


async def test_http_execute_python_worker_start_error_reaches_llm(
    test_context: SharedTestContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_start(
        cls: type[KernelWorker],
        workspace: Path,
        env: dict[str, str],
        max_rss_bytes: int,
        max_output_bytes: int,
        execution_timeout_s: float,
    ) -> KernelWorker:
        del cls, workspace, env, max_rss_bytes, max_output_bytes, execution_timeout_s
        try:
            raise ValueError("uv sync detail")
        except ValueError as exc:
            raise RuntimeError() from exc

    monkeypatch.setattr(KernelWorker, "start", classmethod(fail_start))
    actor_id = await test_context.setup_actor(
        PromptConditionedProvider(
            [
                (messages_contain_tool_result("execute_python"), reply_text("done")),
                (
                    all_of(has_tool_spec("execute_python"), user_message_contains("run python")),
                    call_tool("execute_python", {"code": "print('hello')"}),
                ),
            ]
        )
    )
    conversation_id = test_context.conversation_id("py-start-error")

    await ws_conversation_send(
        test_context.server,
        "m1",
        actor_id,
        conversation_id,
        "run python",
    )
    history = await conversation_history(test_context.server, conversation_id)
    text = tool_result_text(history)

    assert "execute_python failed: RuntimeError: RuntimeError()" in text
    assert "caused by ValueError: uv sync detail" in text
    assert history[-1]["payload"] == {"text": "done"}
