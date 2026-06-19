from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from attrs import define, field
import yuullm


class MailMessage:
    """Marker base for messages delivered through an actor mailbox."""


@define
class ScheduleTriggerMessage(MailMessage):
    """Sent by ScheduleExecutor when a cron job fires an agent action."""

    mid: UUID = field(factory=uuid4)
    content: yuullm.Message | None = None
    agent_name: str = ""
    job_id: str = ""


@define
class BackgroundCompletedMessage(MailMessage):
    """Sent to wake an actor after a detached task terminal event."""

    mid: UUID = field(factory=uuid4)
    content: yuullm.Message | None = None
    task_id: str = ""
    agent_id: str = ""
    agent_name: str = ""
    actor_id: str = ""
    session_id: str = ""


@define
class MailBox:
    """Actor message inbox. Use MailMessage subclasses for type-safe dispatch."""

    mailbox_id: str = field(factory=lambda: f"mailbox_{uuid4().hex[:12]}")
    _queue: asyncio.Queue[MailMessage] = field(
        factory=asyncio.Queue, init=False, repr=False
    )

    async def send(self, msg: MailMessage) -> None:
        await self._queue.put(msg)

    def send_nowait(self, msg: MailMessage) -> None:
        self._queue.put_nowait(msg)

    async def recv(self) -> MailMessage:
        return await self._queue.get()

    async def put(self, msg: MailMessage) -> None:
        await self.send(msg)

    async def get(self) -> MailMessage:
        return await self.recv()

    def empty(self) -> bool:
        return self._queue.empty()
