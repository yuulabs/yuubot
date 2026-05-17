"""Echo actor example for testing actor/integration communication."""

from __future__ import annotations

import asyncio
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from yuubot.core.actors.contracts import Actor
from yuubot.core.actors.python_session import (
    ActorPythonSessionFactory,
    ExecutePythonSession,
)
from yuubot.core.bindings import ActorBinding
from yuubot.core.facade import facade_call_path
from yuubot.core.gateway import Mailbox
from yuubot.core.integrations import IntegrationCore
from yuubot.core.integrations.echo import ECHO_CAPABILITY_SPEC
from yuubot.core.messages import IncomingMessage
from yuubot.resources.events import ResourceChanged

ECHO_ACTOR_TYPE = "echo"


@dataclass
class EchoOnceActor(Actor):
    binding: ActorBinding
    mailbox: Mailbox
    python_sessions: ActorPythonSessionFactory
    echo_results: asyncio.Queue[dict[str, object]] = field(
        default_factory=asyncio.Queue
    )
    _python: ExecutePythonSession | None = None
    _message_task: asyncio.Task[None] | None = None

    @property
    def actor_id(self) -> str:
        return self.binding.actor.id

    async def start(self) -> None:
        await self._start_execute_python()
        self._message_task = asyncio.create_task(self._consume_messages())

    async def stop(self) -> None:
        await self._stop_message_loop()
        await self._stop_execute_python()

    async def handle_resource_changed(self, event: ResourceChanged) -> None:
        _ = event

    async def handle_message(self, message: IncomingMessage) -> None:
        if self._python is None:
            raise RuntimeError("echo actor execute_python harness is not started")
        result = await self._python.call_facade(
            call_path=facade_call_path(ECHO_CAPABILITY_SPEC),
            value=_first_text(message.content),
        )
        await self.echo_results.put(result)

    async def next_echo_result(self) -> dict[str, object]:
        return await asyncio.wait_for(self.echo_results.get(), timeout=5.0)

    async def _consume_messages(self) -> None:
        while True:
            message = await self.mailbox.recv()
            if isinstance(message, IncomingMessage):
                await self.handle_message(message)

    async def _start_execute_python(self) -> None:
        try:
            self._python = await self.python_sessions.create(self.binding)
        except Exception:
            await self._stop_execute_python()
            raise

    async def _stop_execute_python(self) -> None:
        if self._python is not None:
            await self._python.close()
            self._python = None
        self.python_sessions.cleanup_actor(self.actor_id)

    async def _stop_message_loop(self) -> None:
        if self._message_task is None:
            return
        self._message_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._message_task
        self._message_task = None


@dataclass
class EchoOnceActorFactory:
    integrations: IntegrationCore
    python_sessions: ActorPythonSessionFactory | None = None
    actor_type: str = ECHO_ACTOR_TYPE
    _actors: dict[str, EchoOnceActor] = field(default_factory=dict)
    _tmp_dir: tempfile.TemporaryDirectory[str] | None = field(
        default=None,
        init=False,
        repr=False,
    )

    async def create(self, binding: ActorBinding, mailbox: Mailbox) -> Actor:
        actor = EchoOnceActor(
            binding=binding,
            mailbox=mailbox,
            python_sessions=self._python_sessions(),
        )
        self._actors[actor.actor_id] = actor
        return actor

    def actor(self, actor_id: str) -> EchoOnceActor:
        return self._actors[actor_id]

    def _python_sessions(self) -> ActorPythonSessionFactory:
        if self.python_sessions is None:
            self._tmp_dir = tempfile.TemporaryDirectory(prefix="yuubot-yext-")
            self.python_sessions = ActorPythonSessionFactory.in_directory(
                integrations=self.integrations,
                root=Path(self._tmp_dir.name),
            )
        return self.python_sessions


def _first_text(content: list[dict[str, object]]) -> str:
    for item in content:
        if item.get("type") == "text":
            return str(item.get("text", ""))
    return ""
