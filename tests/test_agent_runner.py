from __future__ import annotations

from uuid import UUID
from uuid import uuid4

import yuutools as yt
import yuullm

from yuuagents import AgentContext, Session
from yuuagents.agent import AgentConfig


class _FakeLLM:
    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.default_model = "fake-model"

    async def stream(self, history, tools=None, model=None):  # noqa: ANN001
        reply = self._replies.pop(0)

        async def _iter():
            yield yuullm.Response(item=reply)

        return _iter(), {}


def _make_session(*replies: str) -> Session:
    task_id = uuid4().hex
    return Session(
        config=AgentConfig(
            agent_id=f"agent-{task_id[:8]}",
            system="system prompt",
            tools=yt.ToolManager(),
            llm=_FakeLLM(*replies),
            max_steps=1,
        ),
        context=AgentContext(
            task_id=task_id,
            agent_id=f"agent-{task_id[:8]}",
            workdir="",
            docker_container="",
        ),
    )


async def test_session_start_and_wait():
    session = _make_session("first reply")

    session.start("first input")
    await session.wait()

    # system + user + assistant
    assert len(session.history) == 3
    assert session.history[0][0] == "system"
    assert session.history[1] == ("user", ["first input"])
    assert session.history[2] == ("assistant", ["first reply"])


async def test_session_resume_preserves_history():
    session = _make_session("reply")
    session.start("first input")
    await session.wait()

    old_history = list(session.history)

    session2 = _make_session("continued reply")
    session2.resume("followup", history=old_history)
    await session2.wait()

    # old_history (3 msgs) + new user + new assistant
    assert len(session2.history) == 5
    assert session2.history[0][0] == "system"
    assert session2.history[3] == ("user", ["followup"])
    assert session2.history[4] == ("assistant", ["continued reply"])


async def test_session_resume_can_reuse_conversation_id():
    session = _make_session("reply")
    session.start("first input")
    await session.wait()

    original_conversation_id = session.conversation_id
    assert isinstance(original_conversation_id, UUID)

    session2 = _make_session("continued reply")
    session2.resume(
        "followup",
        history=list(session.history),
        conversation_id=original_conversation_id,
    )
    await session2.wait()

    assert session2.conversation_id == original_conversation_id


async def test_session_send_injects_message():
    session = _make_session("reply")
    session.start("first input")
    # send is fire-and-forget; the agent will pick it up from mailbox
    await session.wait()

    assert session.history[1] == ("user", ["first input"])
