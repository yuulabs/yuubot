from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import msgspec.structs
import pytest
import yuullm
import yuuagents as ya

from tests.conftest import make_group_event, make_private_event
from tests.mocks import make_text_response, make_tool_call_response, mock_llm, mock_recorder_api
from yuubot.core.onebot import to_inbound_message
from yuubot.daemon.agent_runner import AgentRunner
from yuubot.daemon.restricted_python import RestrictedPythonSession, _execute_restricted


def test_restricted_worker_can_execute_simple_code() -> None:
    """_compile_restricted_async must not pass filename= to RestrictingNodeTransformer (removed in RestrictedPython 8.x)."""
    namespace: dict[str, object] = {}
    result = _execute_restricted("x = 2 + 3\nx", namespace)
    assert result.status == "ok", f"restricted exec failed: {result.traceback}"
    assert namespace.get("x") == 5


def _maid_bootstrap_code() -> str:
    return (
        "import yb\n"
        "state = yb.session_state()\n"
        "f'workspace_root={state.workspace_root}'\n"
    )


def test_maid_runtime_exposes_image_description_helpers() -> None:
    from yuuagents.python_runtime import resolve_agent_runtime
    from yuubot.characters import CHARACTER_REGISTRY

    spec = CHARACTER_REGISTRY["maid"].spec
    runtime = resolve_agent_runtime(
        ya.PythonRuntime(imports=spec.resolved_imports(), expand_functions=spec.expand_functions),
        None,
    ).python

    assert runtime is not None
    docs = runtime.render_import_docs()
    assert "import vision" in docs
    assert "yb.describe_image" in docs
    assert "vision.describe_image" in docs


def test_restricted_python_wraps_agent_fn_imports_as_sync_facades() -> None:
    import inspect

    from yuuagents.kernel import _init_session_state
    from yuubot.daemon.restricted_python import _refresh_namespace

    namespace: dict[str, object] = {}
    try:
        _refresh_namespace(
            namespace,
            {
                "sys_path": (),
                "state": {},
                "imports": (("yuubot.agent_fns.vision", "vision"),),
            },
        )
        assert not inspect.iscoroutinefunction(namespace["vision"].describe_image)
    finally:
        _init_session_state({})


@pytest.mark.asyncio
async def test_group_execute_python_uses_restricted_worker(yuubot_config) -> None:
    runner = AgentRunner(yuubot_config)
    inbound = to_inbound_message(make_group_event("算一下", ctx_id=501))

    responses = [
        make_tool_call_response("execute_python", json.dumps({"code": "x = 2\nx + 3"})),
        make_text_response("done"),
    ]
    with mock_llm(responses), mock_recorder_api():
        session = await runner.run_conversation(
            inbound,
            agent_name="yuu",
            bot_kind="group",
        )

    assert session is not None
    assert isinstance(session.agent.python_session, RestrictedPythonSession)
    tool_step = next(step for step in session.steps if isinstance(step, ya.ToolStep))
    assert "5" in tool_step.output_text
    await runner.stop()


@pytest.mark.asyncio
async def test_private_master_reuses_full_python_session(yuubot_config) -> None:
    runner = AgentRunner(yuubot_config)
    inbound = to_inbound_message(make_private_event("hello", ctx_id=502))

    with mock_llm(), mock_recorder_api():
        first = await runner.run_conversation(
            inbound,
            agent_name="yuu",
            bot_kind="master",
        )
        second = await runner.run_conversation(
            inbound,
            agent_name="yuu",
            bot_kind="master",
        )

    assert first is not None
    assert second is not None
    assert isinstance(first.agent.python_session, ya.PythonSession)
    assert first.agent.python_session is second.agent.python_session
    assert first.agent.owns_python_session is False
    assert second.agent.owns_python_session is False
    await runner.stop()


@pytest.mark.asyncio
async def test_running_text_model_signal_image_is_text_reference(yuubot_config) -> None:
    config = msgspec.structs.replace(
        yuubot_config,
        agent_llm_refs={
            **yuubot_config.agent_llm_refs,
            "maid": "deepseek/deepseek-chat",
        },
    )
    runner = AgentRunner(config)
    inbound = to_inbound_message(make_private_event("hello", ctx_id=503))
    started = asyncio.Event()
    release = asyncio.Event()

    async def _fake_stream(self, messages, *, model, tools=None, **kw):
        del self, messages, model, tools, kw
        started.set()
        await release.wait()

        async def _iter():
            for item in make_text_response("done"):
                yield item

        store = yuullm.Store(
            usage=yuullm.Usage(provider="test", model="test-model", total_tokens=1),
        )
        return _iter(), store

    image_event = make_private_event("", ctx_id=503)
    image_event["message"] = [
        {
            "type": "image",
            "data": {
                "url": "https://example.invalid/image.png",
                "file": "image.png",
                "local_path": "/tmp/yuubot-test-image.png",
            },
        }
    ]
    image_event["raw_message"] = "[CQ:image,file=image.png]"

    with (
        patch.object(yuullm.providers.OpenAIChatCompletionProvider, "stream", _fake_stream),
        mock_recorder_api(),
    ):
        task = asyncio.create_task(
            runner.run_conversation(
                inbound,
                agent_name="maid",
                bot_kind="master",
            )
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        try:
            signal = await runner.render_signal(to_inbound_message(image_event))
        finally:
            release.set()
        session = await task

    assert session is not None
    assert isinstance(signal.content, list)
    assert not any(item.get("type") == "image_url" for item in signal.content)
    assert any("/tmp/yuubot-test-image.png" in item.get("text", "") for item in signal.content)
    await runner.stop()


@pytest.mark.asyncio
async def test_maid_bootstrap_reads_workspace_via_yb(yuubot_config) -> None:
    """maid bootstrap code uses `import yb` + `yb.session_state()` to locate workspace.

    yb is injected as yuubot.agent_fns alias; session_state() returns the
    SessionState struct with workspace_root populated by the runtime.
    """
    runner = AgentRunner(yuubot_config)
    inbound = to_inbound_message(make_private_event("hello", ctx_id=510))

    responses = [
        make_tool_call_response("execute_python", json.dumps({"code": _maid_bootstrap_code()})),
        make_text_response("done"),
    ]

    with mock_llm(responses), mock_recorder_api():
        session = await runner.run_conversation(
            inbound,
            agent_name="maid",
            bot_kind="master",
        )

    assert session is not None
    tool_step = next(step for step in session.steps if isinstance(step, ya.ToolStep))
    assert "ModuleNotFoundError" not in tool_step.output_text
    assert "Python execution failed" not in tool_step.output_text
    await runner.stop()
