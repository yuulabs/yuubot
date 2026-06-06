from __future__ import annotations

import asyncio
from typing import cast

import msgspec
import pytest
import yuullm
from yuuagents.mailbox import BackgroundCompletedMessage, MailBox

from helpers import make_actor_record, make_character_record, make_llm_backend_record
from yuubot.core.assembly import build_agent_definition
from yuubot.core.actors.impls.simple_loop import SimpleLoopActor
from yuubot.core.bindings import ActorBinding
from yuubot.core.facade import (
    ActorFacadeBinding,
    FacadeBackgroundTaskEnded,
    FacadeBackgroundTaskStarted,
    FacadeDelegateTask,
    FacadeImResponse,
    IntegrationInvokeBridge,
    _render_client_module,
)
from yuubot.core.integrations.impls.echo import ECHO_CAPABILITY_SPEC
from yuubot.core.integrations import IntegrationCore
from yuubot.core.llm import BoundLLM


def test_generated_yext_client_does_not_export_system_tasks() -> None:
    source = _render_client_module()

    compile(source, "<generated yext._client>", "exec")
    assert "def submit_bg(" not in source
    assert "TASKS[task_id]" not in source


def test_handwritten_yb_tasks_exports_submit_bg() -> None:
    from yb.tasks import submit_bg

    assert callable(submit_bg)


def test_handwritten_yb_im_exports_response_helpers() -> None:
    from yb.im import react, respond

    assert callable(respond)
    assert callable(react)


def test_handwritten_yb_delegate_exports_submit() -> None:
    from yb.delegate import submit

    assert callable(submit)


def test_handwritten_yb_schedule_exports_cron_helpers() -> None:
    from yb.schedule import create, create_cron, delete_cron, list_crons

    assert callable(create)
    assert callable(create_cron)
    assert callable(delete_cron)
    assert callable(list_crons)


def test_python_tool_facade_imports_follow_visible_capabilities(tmp_path) -> None:
    character = make_character_record("actor-1")
    backend = make_llm_backend_record("actor-1")
    actor = make_actor_record(
        "actor-1",
        character=character,
        llm_backend=backend,
    )
    binding = ActorBinding(
        actor=actor,
        character=character,
        llm=BoundLLM(backend=backend, model="gpt-4", stream_options={}),
        workspace_path=tmp_path,
    )

    no_capability_tool = build_agent_definition(
        binding,
        facade=_facade(tmp_path, capabilities=()),
    ).tools["ipykernel"]
    echo_tool = build_agent_definition(
        binding,
        facade=_facade(tmp_path, capabilities=(ECHO_CAPABILITY_SPEC,)),
    ).tools["ipykernel"]

    no_capability_imports = {
        item["module"] for item in no_capability_tool.config["imports"]
    }
    echo_imports = {item["module"] for item in echo_tool.config["imports"]}
    assert "yb.delegate" in no_capability_imports
    assert "yb.im" in no_capability_imports
    assert "yb.schedule" in no_capability_imports
    assert "yext.echo" not in no_capability_imports
    assert "yext.echo" in echo_imports
    assert "yb.delegate.*" in no_capability_tool.config["expand_functions"]
    assert "yb.im.*" in no_capability_tool.config["expand_functions"]
    assert "yb.schedule.*" in no_capability_tool.config["expand_functions"]
    assert "yext.echo.*" not in no_capability_tool.config["expand_functions"]
    assert "yext.echo.*" in echo_tool.config["expand_functions"]


def test_agent_prompt_guidance_is_mode_specific(tmp_path) -> None:
    character = make_character_record("actor-1", system_prompt="Base prompt.")
    backend = make_llm_backend_record("actor-1")
    actor = make_actor_record(
        "actor-1",
        character=character,
        llm_backend=backend,
    )
    binding = ActorBinding(
        actor=actor,
        character=character,
        llm=BoundLLM(backend=backend, model="gpt-4", stream_options={}),
        workspace_path=tmp_path,
    )

    im_prompt = build_agent_definition(binding, mode="im").prompt.system
    conversation_prompt = build_agent_definition(
        binding,
        mode="conversation",
    ).prompt.system

    assert "yb.im.respond" in im_prompt
    assert conversation_prompt == "Base prompt."


@pytest.mark.asyncio
async def test_bridge_routes_facade_background_lifecycle_messages() -> None:
    mailbox = MailBox()
    bridge = IntegrationInvokeBridge(
        cast(IntegrationCore, object()),
        mailbox_for_actor=lambda actor_id: mailbox if actor_id == "actor-1" else None,
    )
    bridge._token = "token"

    await bridge._dispatch(
        _request(
            kind="background_started",
            task_id="task-1",
            status="running",
        )
    )
    started = await mailbox.recv()

    assert isinstance(started, FacadeBackgroundTaskStarted)
    assert started.task_id == "task-1"

    await bridge._dispatch(
        _request(
            kind="background_finished",
            task_id="task-1",
            status="ok",
            summary="42",
        )
    )
    ended = await mailbox.recv()
    completed = await mailbox.recv()

    assert isinstance(ended, FacadeBackgroundTaskEnded)
    assert ended.status == "ok"
    assert isinstance(completed, BackgroundCompletedMessage)
    assert completed.agent_name == "agent-main"
    assert completed.content is not None
    assert yuullm.render_message_text(completed.content) == (
        "Background task task-1 completed:\n42\n\nInspect it with TASKS['task-1']."
    )


@pytest.mark.asyncio
async def test_bridge_routes_facade_im_response_message() -> None:
    mailbox = MailBox()
    bridge = IntegrationInvokeBridge(
        cast(IntegrationCore, object()),
        mailbox_for_actor=lambda actor_id: mailbox if actor_id == "actor-1" else None,
    )
    bridge._token = "token"

    await bridge._dispatch(
        _request(
            kind="im_response",
            payload={
                "msg_id": "msg-1",
                "text": "hello",
                "react": "working",
            },
        )
    )
    response = await mailbox.recv()

    assert isinstance(response, FacadeImResponse)
    assert response.target_msg_id == "msg-1"
    assert response.text == "hello"
    assert response.react == "working"


@pytest.mark.asyncio
async def test_bridge_routes_facade_delegate_task_message() -> None:
    mailbox = MailBox()
    bridge = IntegrationInvokeBridge(
        cast(IntegrationCore, object()),
        mailbox_for_actor=lambda actor_id: mailbox if actor_id == "actor-1" else None,
    )
    bridge._token = "token"

    response = await bridge._dispatch(
        _request(
            kind="delegate_submit",
            task_id="task-1",
            payload={
                "prompt": "inspect the logs",
                "delegate_name": "scout",
            },
        )
    )
    task = await mailbox.recv()

    assert response == {"ok": True, "result": {"task_id": "task-1"}}
    assert isinstance(task, FacadeDelegateTask)
    assert task.task_id == "task-1"
    assert task.prompt == "inspect the logs"
    assert task.delegate_name == "scout"


@pytest.mark.asyncio
async def test_bridge_routes_facade_schedule_request() -> None:
    calls: list[tuple[str, str, str, dict[str, object]]] = []

    async def schedule_for_actor(
        actor_id: str,
        agent_name: str,
        tool_name: str,
        payload: dict[str, object],
    ) -> object:
        calls.append((actor_id, agent_name, tool_name, payload))
        return "Created cron job nightly: 0 0 * * *"

    bridge = IntegrationInvokeBridge(
        cast(IntegrationCore, object()),
        schedule_for_actor=schedule_for_actor,
    )
    bridge._token = "token"

    response = await bridge._dispatch(
        _request(
            kind="schedule",
            capability_id="create_cron",
            payload={
                "cron": "0 0 * * *",
                "actions": ("agent:agent-main:nightly check",),
            },
        )
    )

    assert response == {
        "ok": True,
        "result": {"output": "Created cron job nightly: 0 0 * * *"},
    }
    assert calls == [
        (
            "actor-1",
            "agent-main",
            "create_cron",
            {
                "cron": "0 0 * * *",
                "actions": ["agent:agent-main:nightly check"],
            },
        )
    ]


@pytest.mark.asyncio
async def test_simple_loop_actor_counts_facade_background_tasks() -> None:
    actor = object.__new__(SimpleLoopActor)
    actor._background_task_ids = set()

    await SimpleLoopActor.handle_mail_message(
        actor,
        FacadeBackgroundTaskStarted(
            task_id="task-1",
            actor_id="actor-1",
            agent_name="agent-main",
            session_id="session-1",
            mailbox_id="actor:actor-1",
        ),
    )
    assert actor.has_background_tasks

    await SimpleLoopActor.handle_mail_message(
        actor,
        FacadeBackgroundTaskEnded(
            task_id="task-1",
            actor_id="actor-1",
            agent_name="agent-main",
            session_id="session-1",
            mailbox_id="actor:actor-1",
            status="ok",
        ),
    )
    assert not actor.has_background_tasks


@pytest.mark.asyncio
async def test_simple_loop_actor_runs_facade_delegate_task() -> None:
    mailbox = MailBox()
    runtime = FakeDelegateRuntime(result="delegate result")
    actor = object.__new__(SimpleLoopActor)
    actor._background_task_ids = set()
    actor._delegate_tasks = set()
    actor._runtime = runtime
    actor.mailbox = mailbox

    await SimpleLoopActor.handle_mail_message(
        actor,
        FacadeDelegateTask(
            task_id="task-1",
            actor_id="actor-1",
            agent_name="agent-main",
            session_id="session-1",
            mailbox_id="actor:actor-1",
            prompt="inspect the logs",
            delegate_name="scout",
        ),
    )
    completed = await asyncio.wait_for(mailbox.recv(), timeout=1)

    assert runtime.calls == [
        {
            "task_id": "task-1",
            "prompt": "inspect the logs",
            "parent_agent_name": "agent-main",
            "delegate_name": "scout",
        }
    ]
    assert not actor.has_background_tasks
    assert isinstance(completed, BackgroundCompletedMessage)
    assert completed.agent_name == "agent-main"
    assert completed.content is not None
    assert yuullm.render_message_text(completed.content) == (
        "Delegate task scout completed:\ndelegate result"
    )


def _request(
    *,
    kind: str,
    capability_id: str = "",
    task_id: str = "",
    status: str = "",
    summary: str = "",
    payload: dict[str, object] | None = None,
) -> bytes:
    return msgspec.json.encode(
        {
            "token": "token",
            "kind": kind,
            "actor_id": "actor-1",
            "capability_id": capability_id,
            "agent_name": "agent-main",
            "session_id": "session-1",
            "mailbox_id": "actor:actor-1",
            "task_id": task_id,
            "status": status,
            "summary": summary,
            "payload": payload or {},
        }
    )


class FakeDelegateRuntime:
    def __init__(self, result: str) -> None:
        self.result = result
        self.calls: list[dict[str, str]] = []

    async def run_delegate(
        self,
        *,
        task_id: str,
        prompt: str,
        parent_agent_name: str,
        delegate_name: str = "",
    ) -> str:
        self.calls.append(
            {
                "task_id": task_id,
                "prompt": prompt,
                "parent_agent_name": parent_agent_name,
                "delegate_name": delegate_name,
            }
        )
        return self.result


def _facade(tmp_path, *, capabilities):
    return ActorFacadeBinding(
        actor_id="actor-1",
        agent_name="actor-1",
        session_id="session-1",
        mailbox_id="actor:actor-1",
        capabilities=tuple(capabilities),
        root=tmp_path,
        sys_path=[str(tmp_path)],
        startup_code="import yb\nimport yext",
        session_state={},
    )
