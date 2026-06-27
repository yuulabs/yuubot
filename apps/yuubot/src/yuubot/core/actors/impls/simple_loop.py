"""Default yuuagents loop actor implementation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field

import yuullm
from yuuagents import ProviderPoolSessionFactory
from yuuagents.core.mailbox import (
    BackgroundCompletedMessage,
    MailMessage,
    ScheduleTriggerMessage,
)

from yuubot.core.actors.contracts import Actor
from yuubot.core.actors.impls.python_session import ActorPythonSessionFactory
from yuubot.core.assembly import YuuAgentsActorRuntime, start_yuuagents_actor
from yuubot.core.bindings import ActorBinding, AgentBinding, load_actor_binding
from yuubot.core.facade import (
    FacadeBackgroundTaskEnded,
    FacadeBackgroundTaskStarted,
    FacadeDelegateTask,
)
from yuubot.core.gateway import Mailbox
from yuubot.core.integrations.core import IntegrationCore
from yuubot.core.message_rendering import render_incoming_user_message
from yuubot.core.messages import IncomingMessage
from yuubot.core.observability import YuubotTraceContextProvider
from yuubot.resources.events import ResourceChanged
from yuubot.resources.repository import ResourceRepository

logger = logging.getLogger(__name__)


@dataclass
class SimpleLoopActor(Actor):
    """Minimal actor runtime for plain yuuagents loop actors."""

    binding: ActorBinding
    repository: ResourceRepository
    python_sessions: ActorPythonSessionFactory
    mailbox: Mailbox
    integrations: IntegrationCore | None = None
    llm_session_factory: ProviderPoolSessionFactory | None = None
    trace_context: YuubotTraceContextProvider | None = None
    _runtime: YuuAgentsActorRuntime | None = None
    _message_task: asyncio.Task[None] | None = None
    _delegate_tasks: set[asyncio.Task[None]] = field(default_factory=set, init=False)
    _background_task_ids: set[str] = field(default_factory=set, init=False)
    restart_required: bool = False
    _agent_binding: AgentBinding | None = None

    @property
    def actor_id(self) -> str:
        return self.binding.actor.id

    @property
    def has_background_tasks(self) -> bool:
        return bool(self._background_task_ids)

    async def start(self) -> None:
        agent_binding = self._default_agent_binding()
        self._agent_binding = agent_binding
        await self.python_sessions.prepare_facade_environment(self.actor_id)
        if self.trace_context is not None:
            self.trace_context.register(
                self.binding.actor_name,
                model=agent_binding.llm.model,
            )
        facade = await self.python_sessions.bind_facade(
            agent_binding,
            mailbox_id=self.mailbox.mailbox_id,
        )
        self._runtime = start_yuuagents_actor(
            agent_binding,
            facade=facade,
            mailbox=self.mailbox,
            llm_session_factory=self.llm_session_factory,
            trace_context=self.trace_context,
        )
        self._start_message_loop()

    async def stop(self) -> None:
        await self._stop_message_loop()
        if self._runtime is not None:
            await self._runtime.close()
        self._runtime = None
        self._agent_binding = None
        self.python_sessions.cleanup_actor(self.actor_id)

    async def handle_resource_changed(self, event: ResourceChanged) -> None:
        agent_binding = self._agent_binding
        if agent_binding is None:
            return
        if (
            event.is_table("capability_sets")
            and agent_binding.capability_set.id in event.row_ids
        ):
            self.restart_required = True
        elif (
            event.is_table("llm_backends")
            and agent_binding.llm.backend.id in event.row_ids
        ):
            self.restart_required = True

    async def handle_message(self, message: IncomingMessage) -> None:
        runtime = self._require_runtime()
        agent = await runtime.handle_message(
            ScheduleTriggerMessage(
                agent_name=self.binding.actor_name,
                job_id=message.message_id,
                content=render_incoming_user_message(message),
            )
        )
        if agent is None:
            raise RuntimeError(f"simple_loop actor {self.actor_id!r} has no main agent")

    async def handle_mail_message(self, message: MailMessage) -> None:
        if isinstance(message, FacadeBackgroundTaskStarted):
            self._background_task_ids.add(message.task_id)
            return
        if isinstance(message, FacadeBackgroundTaskEnded):
            self._background_task_ids.discard(message.task_id)
            return
        if isinstance(message, FacadeDelegateTask):
            self._submit_delegate_task(message)
            return
        if isinstance(message, IncomingMessage):
            await self.handle_message(message)
            return
        runtime = self._require_runtime()
        await runtime.handle_message(message)

    async def run_schedule_tool(
        self,
        *,
        agent_name: str,
        tool_name: str,
        payload: dict[str, object],
    ) -> object:
        return await self._require_runtime().run_schedule_tool(
            agent_name=agent_name,
            tool_name=tool_name,
            payload=payload,
        )

    def _start_message_loop(self) -> None:
        self._message_task = asyncio.create_task(self._consume_messages())

    async def _stop_message_loop(self) -> None:
        if self._message_task is None:
            return
        self._message_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._message_task
        self._message_task = None
        for task in list(self._delegate_tasks):
            task.cancel()
        for task in list(self._delegate_tasks):
            with suppress(asyncio.CancelledError):
                await task
        self._delegate_tasks.clear()

    async def _consume_messages(self) -> None:
        while True:
            message = await self.mailbox.recv()
            if self.restart_required:
                await self._reload()
            await self.handle_mail_message(message)

    def _submit_delegate_task(self, message: FacadeDelegateTask) -> None:
        self._background_task_ids.add(message.task_id)
        task = asyncio.create_task(self._run_delegate_task(message))
        self._delegate_tasks.add(task)
        task.add_done_callback(self._delegate_tasks.discard)

    async def _run_delegate_task(self, message: FacadeDelegateTask) -> None:
        runtime = self._require_runtime()
        status = "ok"
        try:
            result = await runtime.run_delegate(
                task_id=message.task_id,
                prompt=message.prompt,
                parent_agent_name=message.agent_name,
                delegate_name=message.delegate_name,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status = "error"
            result = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "actor %s delegate task %s failed",
                self.actor_id,
                message.task_id,
                exc_info=True,
            )
        finally:
            self._background_task_ids.discard(message.task_id)
        await self.mailbox.send(
            BackgroundCompletedMessage(
                task_id=message.task_id,
                actor_id=message.actor_id,
                agent_name=message.agent_name,
                session_id=message.session_id,
                content=yuullm.user(_delegate_completion_text(message, status, result)),
            )
        )

    async def _reload(self) -> None:
        self.restart_required = False
        if self._runtime is not None:
            await self._runtime.close()
            self._runtime = None
        self.python_sessions.cleanup_actor(self.actor_id)
        self.binding = await load_actor_binding(
            self.repository,
            self.actor_id,
        )
        agent_binding = self._default_agent_binding()
        self._agent_binding = agent_binding
        await self.python_sessions.prepare_facade_environment(self.actor_id)
        if self.trace_context is not None:
            self.trace_context.register(
                self.binding.actor_name,
                model=agent_binding.llm.model,
            )
        facade = await self.python_sessions.bind_facade(
            agent_binding,
            mailbox_id=self.mailbox.mailbox_id,
        )
        self._runtime = start_yuuagents_actor(
            agent_binding,
            facade=facade,
            mailbox=self.mailbox,
            llm_session_factory=self.llm_session_factory,
            trace_context=self.trace_context,
        )

    def _require_runtime(self) -> YuuAgentsActorRuntime:
        if self._runtime is None:
            raise RuntimeError(f"simple_loop actor {self.actor_id!r} is not started")
        return self._runtime

    def _default_agent_binding(self) -> AgentBinding:
        return self.binding.default_agent_binding()


def _delegate_completion_text(
    message: FacadeDelegateTask,
    status: str,
    result: str,
) -> str:
    label = message.delegate_name.strip() or message.task_id
    if status == "ok":
        if result:
            return f"Delegate task {label} completed:\n{result}"
        return f"Delegate task {label} completed without an assistant response."
    return f"Delegate task {label} failed:\n{result}"


@dataclass
class SimpleLoopActorFactory:
    repository: ResourceRepository
    python_sessions: ActorPythonSessionFactory
    integrations: IntegrationCore | None = None
    llm_session_factory_factory: (
        Callable[[AgentBinding], ProviderPoolSessionFactory | None] | None
    ) = None
    trace_context: YuubotTraceContextProvider | None = None
    actor_type: str = "simple_loop"
    _actors: dict[str, SimpleLoopActor] = field(default_factory=dict)

    async def create(self, binding: ActorBinding, mailbox: Mailbox) -> Actor:
        agent_binding = binding.default_agent_binding()
        actor = SimpleLoopActor(
            binding=binding,
            repository=self.repository,
            python_sessions=self.python_sessions,
            mailbox=mailbox,
            integrations=self.integrations,
            llm_session_factory=self._llm_session_factory(agent_binding),
            trace_context=self.trace_context,
        )
        self._actors[actor.actor_id] = actor
        return actor

    def actor(self, actor_id: str) -> SimpleLoopActor:
        return self._actors[actor_id]

    def _llm_session_factory(self, binding: AgentBinding) -> ProviderPoolSessionFactory | None:
        if self.llm_session_factory_factory is None:
            return None
        return self.llm_session_factory_factory(binding)
