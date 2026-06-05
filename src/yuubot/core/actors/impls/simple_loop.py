"""Default yuuagents loop actor implementation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field

import yuullm
from yuuagents.agent import LlmClient
from yuuagents.mailbox import (
    BackgroundCompletedMessage,
    MailMessage,
    ScheduleTriggerMessage,
)

from yuubot.bootstrap.config import YuuAgentsConfig
from yuubot.core.actors.contracts import Actor
from yuubot.core.actors.impls.python_session import ActorPythonSessionFactory
from yuubot.core.assembly import YuuAgentsActorRuntime, start_yuuagents_actor
from yuubot.core.bindings import ActorBinding, load_actor_binding
from yuubot.core.facade import (
    FacadeBackgroundTaskEnded,
    FacadeBackgroundTaskStarted,
    FacadeDelegateTask,
    FacadeImResponse,
)
from yuubot.core.gateway import Mailbox
from yuubot.core.integrations.core import IntegrationCore
from yuubot.core.message_rendering import render_incoming_user_message
from yuubot.core.messages import IncomingMessage
from yuubot.core.observability import YuubotTraceContextProvider
from yuubot.resources.events import ResourceChanged
from yuubot.resources.repository import ResourceRepository

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 5
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 32.0


@dataclass
class SimpleLoopActor(Actor):
    """Minimal actor runtime for plain yuuagents loop actors."""

    binding: ActorBinding
    repository: ResourceRepository
    yuuagents_config: YuuAgentsConfig
    python_sessions: ActorPythonSessionFactory
    mailbox: Mailbox
    integrations: IntegrationCore | None = None
    llm_client: LlmClient | None = None
    trace_context: YuubotTraceContextProvider | None = None
    _runtime: YuuAgentsActorRuntime | None = None
    _message_task: asyncio.Task[None] | None = None
    _delegate_tasks: set[asyncio.Task[None]] = field(default_factory=set, init=False)
    _background_task_ids: set[str] = field(default_factory=set, init=False)
    _im_message_sources: dict[str, str] = field(default_factory=dict, init=False)
    _latest_im_message_id: str = ""
    restart_required: bool = False

    @property
    def actor_id(self) -> str:
        return self.binding.actor.id

    @property
    def has_background_tasks(self) -> bool:
        return bool(self._background_task_ids)

    async def start(self) -> None:
        if self.trace_context is not None:
            self.trace_context.register(
                self.binding.actor.name,
                character_name=self.binding.character.name,
                model=self.binding.llm.model,
            )
        facade = await self.python_sessions.bind_facade(
            self.binding,
            mailbox_id=self.mailbox.mailbox_id,
        )
        self._runtime = start_yuuagents_actor(
            self.binding,
            yuuagents_config=self.yuuagents_config,
            facade=facade,
            mailbox=self.mailbox,
            llm_client=self.llm_client,
            trace_context=self.trace_context,
        )
        self._start_message_loop()

    async def stop(self) -> None:
        await self._stop_message_loop()
        if self._runtime is not None:
            await self._runtime.close()
        self._runtime = None
        self.python_sessions.cleanup_actor(self.actor_id)

    async def handle_resource_changed(self, event: ResourceChanged) -> None:
        if event.is_table("characters") and self.binding.character.id in event.row_ids:
            self.restart_required = True
        elif (
            event.is_table("llm_backends")
            and self.binding.llm.backend.id in event.row_ids
        ):
            self.restart_required = True

    async def handle_message(self, message: IncomingMessage) -> None:
        self._remember_im_message(message)
        runtime = self._require_runtime()
        agent = await runtime.handle_message(
            ScheduleTriggerMessage(
                agent_name=self.binding.actor.name,
                job_id=message.message_id,
                content=render_incoming_user_message(message),
            )
        )
        if agent is None:
            raise RuntimeError(f"simple_loop actor {self.actor_id!r} has no main agent")

    async def handle_mail_message(self, message: MailMessage) -> None:
        if isinstance(message, FacadeImResponse):
            await self._send_im_response(message)
            return
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

    async def ensure_conversation_agent(
        self,
        conversation_id: str,
        history: yuullm.History,
    ):
        return await self._require_runtime().ensure_conversation_agent(
            conversation_id,
            history,
        )

    async def handle_conversation_message(
        self,
        conversation_id: str,
        message: yuullm.Message,
        history: yuullm.History,
    ):
        return await self._require_runtime().handle_conversation_message(
            conversation_id,
            message,
            history,
        )

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
        consecutive_failures = 0
        while True:
            message = await self.mailbox.recv()
            try:
                if self.restart_required:
                    await self._reload()
                await self.handle_mail_message(message)
                consecutive_failures = 0
            except Exception as exc:
                logger.warning(
                    "actor %s failed processing message: %s: %s",
                    self.actor_id,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    logger.error(
                        "actor %s failed %d consecutive messages, stopping",
                        self.actor_id,
                        consecutive_failures,
                    )
                    raise
                delay = min(
                    BACKOFF_BASE_S * (2 ** (consecutive_failures - 1)), BACKOFF_CAP_S
                )
                logger.warning(
                    "actor %s failed (%d/%d), backing off %.1fs",
                    self.actor_id,
                    consecutive_failures,
                    MAX_CONSECUTIVE_FAILURES,
                    delay,
                )
                await asyncio.sleep(delay)

    def _try_running_instance(self, integration_id: str):
        if self.integrations is None:
            return None
        try:
            return self.integrations.running_instance(integration_id)
        except LookupError:
            return None

    def _remember_im_message(self, message: IncomingMessage) -> None:
        if message.source.producer != "integration":
            return
        self._im_message_sources[message.message_id] = message.source.id
        self._latest_im_message_id = message.message_id

    async def _send_im_response(self, response: FacadeImResponse) -> None:
        target_msg_id = response.target_msg_id or self._latest_im_message_id
        if not target_msg_id:
            logger.warning("actor %s has no IM message target", self.actor_id)
            return
        integration_id = self._im_message_sources.get(target_msg_id)
        if not integration_id:
            logger.warning(
                "actor %s cannot resolve IM message %s",
                self.actor_id,
                target_msg_id,
            )
            return
        instance = self._try_running_instance(integration_id)
        if instance is None:
            logger.warning(
                "actor %s cannot respond to IM message %s; integration %s is not running",
                self.actor_id,
                target_msg_id,
                integration_id,
            )
            return
        try:
            await instance.response(
                target_msg_id,
                msg=response.text,
                react=response.react or None,
            )
        except Exception:
            logger.warning(
                "actor %s failed sending IM response for message %s",
                self.actor_id,
                target_msg_id,
                exc_info=True,
            )

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
            workspace_path=self.binding.workspace_path,
        )
        if self.trace_context is not None:
            self.trace_context.register(
                self.binding.actor.name,
                character_name=self.binding.character.name,
                model=self.binding.llm.model,
            )
        facade = await self.python_sessions.bind_facade(
            self.binding,
            mailbox_id=self.mailbox.mailbox_id,
        )
        self._runtime = start_yuuagents_actor(
            self.binding,
            yuuagents_config=self.yuuagents_config,
            facade=facade,
            mailbox=self.mailbox,
            llm_client=self.llm_client,
            trace_context=self.trace_context,
        )

    def _require_runtime(self) -> YuuAgentsActorRuntime:
        if self._runtime is None:
            raise RuntimeError(f"simple_loop actor {self.actor_id!r} is not started")
        return self._runtime


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
    yuuagents_config: YuuAgentsConfig
    python_sessions: ActorPythonSessionFactory
    integrations: IntegrationCore | None = None
    llm_client_factory: Callable[[ActorBinding], LlmClient | None] | None = None
    trace_context: YuubotTraceContextProvider | None = None
    actor_type: str = "simple_loop"
    _actors: dict[str, SimpleLoopActor] = field(default_factory=dict)

    async def create(self, binding: ActorBinding, mailbox: Mailbox) -> Actor:
        actor = SimpleLoopActor(
            binding=binding,
            repository=self.repository,
            yuuagents_config=self.yuuagents_config,
            python_sessions=self.python_sessions,
            mailbox=mailbox,
            integrations=self.integrations,
            llm_client=self._llm_client(binding),
            trace_context=self.trace_context,
        )
        self._actors[actor.actor_id] = actor
        return actor

    def actor(self, actor_id: str) -> SimpleLoopActor:
        return self._actors[actor_id]

    def _llm_client(self, binding: ActorBinding) -> LlmClient | None:
        if self.llm_client_factory is None:
            return None
        return self.llm_client_factory(binding)
