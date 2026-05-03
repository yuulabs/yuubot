from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import aiosqlite
import pytest

from yuuagents import Budget, EventBus, MailBox, ScheduleTriggerMessage, UsageSink
from yuuagents.providers.schedule import ScheduleExecutor, _execute_actions


class _Sink(UsageSink):
    def __init__(self) -> None:
        super().__init__(
            eventbus=EventBus(),
            task_id=uuid4(),
            budget=Budget(limits={}),
        )


@pytest.mark.asyncio
async def test_schedule_executor_create_list_delete_cron(tmp_path) -> None:
    mailbox = MailBox()
    executor = ScheduleExecutor(mailbox=mailbox, db_path=str(tmp_path / "schedule.db"))
    sink = _Sink()

    created = await executor.run(
        "create_cron",
        {
            "job_id": "job-test",
            "cron": "0 0 1 1 *",
            "actions": ["agent:yuu:hello"],
            "once": True,
        },
        sink,
    )
    listed = await executor.run("list_crons", {}, _Sink())
    deleted = await executor.run("delete_cron", {"job_id": "job-test"}, _Sink())

    assert "Created cron job job-test" in created
    assert "job-test: 0 0 1 1 *" in listed
    assert "Deleted cron job job-test" in deleted

    async with aiosqlite.connect(tmp_path / "schedule.db") as db:
        async with db.execute("SELECT COUNT(*) FROM cron_jobs WHERE job_id = 'job-test'") as cur:
            row = await cur.fetchone()
    assert row == (0,)
    await executor.aclose()


@pytest.mark.asyncio
async def test_schedule_agent_action_sends_mailbox_trigger() -> None:
    mailbox = MailBox()

    result = await _execute_actions(["agent:yuu:scheduled hello"], mailbox, "job-1")
    message = await asyncio.wait_for(mailbox.recv(), timeout=1)

    assert result["action1_ok"] is True
    assert isinstance(message, ScheduleTriggerMessage)
    assert message.agent_name == "yuu"
    assert message.job_id == "job-1"
    assert message.content is not None
    assert message.content.role == "user"


@pytest.mark.asyncio
async def test_schedule_conditional_actions_follow_first_result() -> None:
    mailbox = MailBox()

    success = await _execute_actions(
        ["bash:printf ok", "agent:yuu:success", "agent:yuu:failure"],
        mailbox,
        "job-success",
    )
    success_message = await asyncio.wait_for(mailbox.recv(), timeout=1)

    failure = await _execute_actions(
        ["bash:false", "agent:yuu:success", "agent:yuu:failure"],
        mailbox,
        "job-failure",
    )
    failure_message = await asyncio.wait_for(mailbox.recv(), timeout=1)

    assert success["action1_ok"] is True
    assert success["action1_summary"] == "ok"
    assert success_message.content is not None
    assert json.dumps(success_message.content.content, ensure_ascii=False).find("success") >= 0

    assert failure["action1_ok"] is False
    assert failure_message.content is not None
    assert json.dumps(failure_message.content.content, ensure_ascii=False).find("failure") >= 0
