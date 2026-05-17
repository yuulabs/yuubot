from __future__ import annotations

from typing import cast

import msgspec
import pytest
import yuullm
from yuuagents.mailbox import BackgroundCompletedMessage, MailBox

from yuubot.core.actors.simple_loop import SimpleLoopActor
from yuubot.core.facade import (
    IntegrationInvokeBridge,
    YextBackgroundTaskEnded,
    YextBackgroundTaskStarted,
    _render_client_module,
)
from yuubot.core.integrations import IntegrationCore


def test_generated_yext_client_exports_submit_bg() -> None:
    source = _render_client_module()

    compile(source, "<generated yext._client>", "exec")
    assert "def submit_bg(coro: Any, tid_suggest: str | None = None) -> str:" in source
    assert "Submit a long-running coroutine as a background task." in source
    assert "TASKS[task_id]" in source
    assert "_tasks().pop" not in source


@pytest.mark.asyncio
async def test_bridge_routes_yext_background_lifecycle_messages() -> None:
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

    assert isinstance(started, YextBackgroundTaskStarted)
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

    assert isinstance(ended, YextBackgroundTaskEnded)
    assert ended.status == "ok"
    assert isinstance(completed, BackgroundCompletedMessage)
    assert completed.agent_name == "agent-main"
    assert completed.content is not None
    assert yuullm.render_message_text(completed.content) == (
        "Background task task-1 completed:\n42\n\nInspect it with TASKS['task-1']."
    )


@pytest.mark.asyncio
async def test_simple_loop_actor_counts_yext_background_tasks() -> None:
    actor = object.__new__(SimpleLoopActor)
    actor._background_task_ids = set()

    await SimpleLoopActor.handle_mail_message(
        actor,
        YextBackgroundTaskStarted(
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
        YextBackgroundTaskEnded(
            task_id="task-1",
            actor_id="actor-1",
            agent_name="agent-main",
            session_id="session-1",
            mailbox_id="actor:actor-1",
            status="ok",
        ),
    )
    assert not actor.has_background_tasks


def _request(
    *,
    kind: str,
    task_id: str,
    status: str,
    summary: str = "",
) -> bytes:
    return msgspec.json.encode(
        {
            "token": "token",
            "kind": kind,
            "actor_id": "actor-1",
            "agent_name": "agent-main",
            "session_id": "session-1",
            "mailbox_id": "actor:actor-1",
            "task_id": task_id,
            "status": status,
            "summary": summary,
        }
    )
