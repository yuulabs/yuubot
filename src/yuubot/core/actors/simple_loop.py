"""Default yuuagents loop actor implementation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import yuullm
import yuutrace
from yuuagents import Actor as YuuAgentsActor
from yuuagents.agent import Agent, LlmClient
from yuuagents.mailbox import ScheduleTriggerMessage

from yuubot.bootstrap.config import YuuAgentsConfig
from yuubot.core.actors.contracts import Actor
from yuubot.core.actors.python_session import ActorPythonSessionFactory
from yuubot.core.assembly import start_yuuagents_actor
from yuubot.core.bindings import ActorBinding, load_actor_binding
from yuubot.core.gateway import Mailbox
from yuubot.core.message_rendering import render_incoming_user_message
from yuubot.core.messages import IncomingMessage
from yuubot.core.observability import TraceObserver
from yuubot.resources.events import ResourceChanged
from yuubot.resources.repository import ResourceRepository

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 5
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 32.0


@dataclass
class SimpleLoopTurnResult:
    actor_id: str
    message_id: str
    agent_id: str
    assistant_text: str
    history_length: int


@dataclass
class SimpleLoopActor(Actor):
    """Minimal actor runtime for plain yuuagents loop actors."""

    binding: ActorBinding
    repository: ResourceRepository
    yuuagents_config: YuuAgentsConfig
    python_sessions: ActorPythonSessionFactory
    mailbox: Mailbox
    llm_client: LlmClient | None = None
    observer: TraceObserver | None = None
    turn_results: asyncio.Queue[SimpleLoopTurnResult] = field(
        default_factory=asyncio.Queue
    )
    _runtime: YuuAgentsActor | None = None
    _message_task: asyncio.Task[None] | None = None
    _conversation_id: str = ""
    restart_required: bool = False

    @property
    def actor_id(self) -> str:
        return self.binding.actor.id

    async def start(self) -> None:
        self._conversation_id = str(uuid4())
        if self.observer is not None:
            self.observer.register(
                self.binding.actor.name,
                conversation_id=self._conversation_id,
                character_name=self.binding.character.name,
                model=self.binding.llm.model,
            )
        facade = await self.python_sessions.bind_facade(self.binding)
        self._runtime = start_yuuagents_actor(
            self.binding,
            yuuagents_config=self.yuuagents_config,
            facade=facade,
            llm_client=self.llm_client,
            observer=self.observer,
        )
        self._start_message_loop()

    async def stop(self) -> None:
        await self._stop_message_loop()
        if self._runtime is not None:
            await self._runtime.close()
        self._runtime = None
        self.python_sessions.cleanup_actor(self.actor_id)
        if self.observer is not None:
            self.observer.unregister(self.binding.actor.name)

    async def handle_resource_changed(self, event: ResourceChanged) -> None:
        if event.is_table("characters") and self.binding.character.id in event.row_ids:
            self.restart_required = True
        elif event.is_table("llm_backends") and self.binding.llm.backend.id in event.row_ids:
            self.restart_required = True

    async def handle_message(self, message: IncomingMessage) -> None:
        runtime = self._require_runtime()
        conv = yuutrace.conversation(
            id=UUID(self._conversation_id),
            agent=self.binding.actor.name,
            model=self.binding.llm.model,
        )
        with conv:
            turn = conv.start_turn("assistant")
            with turn:
                agent = await runtime.handle_message(
                    ScheduleTriggerMessage(
                        agent_name=self.binding.actor.name,
                        job_id=message.message_id,
                        content=render_incoming_user_message(message),
                    )
                )
        if agent is None:
            raise RuntimeError(f"simple_loop actor {self.actor_id!r} has no main agent")
        await self.turn_results.put(_turn_result(self.actor_id, message.message_id, agent))

    async def next_turn_result(self) -> SimpleLoopTurnResult:
        return await asyncio.wait_for(self.turn_results.get(), timeout=5.0)

    def _start_message_loop(self) -> None:
        self._message_task = asyncio.create_task(self._consume_messages())

    async def _stop_message_loop(self) -> None:
        if self._message_task is None:
            return
        self._message_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._message_task
        self._message_task = None

    async def _consume_messages(self) -> None:
        consecutive_failures = 0
        while True:
            message = await self.mailbox.get()
            try:
                if self.restart_required:
                    await self._reload()
                await self.handle_message(message)
                consecutive_failures = 0
            except Exception:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    logger.error(
                        "actor %s failed %d consecutive messages, stopping",
                        self.actor_id,
                        consecutive_failures,
                    )
                    raise
                delay = min(BACKOFF_BASE_S * (2 ** (consecutive_failures - 1)), BACKOFF_CAP_S)
                logger.warning(
                    "actor %s failed (%d/%d), backing off %.1fs",
                    self.actor_id,
                    consecutive_failures,
                    MAX_CONSECUTIVE_FAILURES,
                    delay,
                )
                await asyncio.sleep(delay)

    async def _reload(self) -> None:
        self.restart_required = False
        if self.observer is not None:
            self.observer.unregister(self.binding.actor.name)
        if self._runtime is not None:
            await self._runtime.close()
            self._runtime = None
        self.python_sessions.cleanup_actor(self.actor_id)
        self.binding = await load_actor_binding(
            self.repository,
            self.actor_id,
            workspace_path=self.binding.workspace_path,
        )
        self._conversation_id = str(uuid4())
        if self.observer is not None:
            self.observer.register(
                self.binding.actor.name,
                conversation_id=self._conversation_id,
                character_name=self.binding.character.name,
                model=self.binding.llm.model,
            )
        facade = await self.python_sessions.bind_facade(self.binding)
        self._runtime = start_yuuagents_actor(
            self.binding,
            yuuagents_config=self.yuuagents_config,
            facade=facade,
            llm_client=self.llm_client,
            observer=self.observer,
        )

    def _require_runtime(self) -> YuuAgentsActor:
        if self._runtime is None:
            raise RuntimeError(f"simple_loop actor {self.actor_id!r} is not started")
        return self._runtime


@dataclass
class SimpleLoopActorFactory:
    repository: ResourceRepository
    yuuagents_config: YuuAgentsConfig
    python_sessions: ActorPythonSessionFactory
    llm_client_factory: Callable[[ActorBinding], LlmClient | None] | None = None
    observer: TraceObserver | None = None
    actor_type: str = "simple_loop"
    _actors: dict[str, SimpleLoopActor] = field(default_factory=dict)

    async def create(self, binding: ActorBinding, mailbox: Mailbox) -> Actor:
        actor = SimpleLoopActor(
            binding=binding,
            repository=self.repository,
            yuuagents_config=self.yuuagents_config,
            python_sessions=self.python_sessions,
            mailbox=mailbox,
            llm_client=self._llm_client(binding),
            observer=self.observer,
        )
        self._actors[actor.actor_id] = actor
        return actor

    def actor(self, actor_id: str) -> SimpleLoopActor:
        return self._actors[actor_id]

    def _llm_client(self, binding: ActorBinding) -> LlmClient | None:
        if self.llm_client_factory is None:
            return None
        return self.llm_client_factory(binding)


def _turn_result(
    actor_id: str,
    message_id: str,
    agent: Agent,
) -> SimpleLoopTurnResult:
    return SimpleLoopTurnResult(
        actor_id=actor_id,
        message_id=message_id,
        agent_id=agent.agent_id,
        assistant_text=_last_assistant_text(agent),
        history_length=len(agent.history),
    )


def _last_assistant_text(agent: Agent) -> str:
    for message in reversed(agent.history):
        if message.role == "assistant":
            return yuullm.render_message_text(message)
    return ""
