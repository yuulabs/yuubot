"""Actor: a routable agent entity that drives Conversations from its mailbox."""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec
from attrs import define, field

from ..chat import Conversation, ConversationBlocked, ConversationBusy
from ..chat.harness import HarnessConfig
from ..chat.history import HistoryHelper
from ..llm import Provider
from ..domain.messages import ActorMessage, ConversationContext, GenReasoning, GenText, InputMessage, ModelCard, text_content
from ..domain.records import DEFAULT_CONTEXT_COMPRESSION_TOKENS
from ..python import KernelPool
from ..runtime.event_payloads import (
    ActorBlockedPayload,
    ActorBusyPayload,
    ActorContextCompactedPayload,
    ActorContextCompactionStoppedPayload,
    ActorOutputPayload,
)
from ..tools import all_tool_configs, tool_specs
from .prompt import developer_prompt
from .workspace import prepare_workspace

if TYPE_CHECKING:
    from ..runtime import Mailbox, Runtime


class ActorConfig(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    name: str
    description: str = ""
    workspace: str
    persona: str = ""
    model: ModelCard
    context_compression_tokens: int = DEFAULT_CONTEXT_COMPRESSION_TOKENS


CONTEXT_COMPACTION_SUMMARY_PROMPT = """\
Summarize the current work so another run can continue immediately.

Include:
- current goal
- completed work
- critical files, decisions, and constraints
- remaining actions

Do not call tools. Return only the summary."""


def _context_compaction_continue_message(original_user_text: str) -> str:
    return (
        "This is an automatic context compression continuation. Continue running from the summary above.\n\n"
        "Most recent original user message:\n"
        f"{original_user_text}"
    )


def _daemon_url(runtime: Runtime) -> str:
    from ..web.run_state import read as read_run_state

    state = read_run_state(runtime.data_dir)
    if state is not None:
        return f"http://{state.host}:{state.port}"
    return "http://127.0.0.1:8765"


async def build_conversation_context(
    runtime: Runtime,
    actor_config: ActorConfig,
    conversation_id: str,
) -> ConversationContext:
    return ConversationContext(
        actor_config.model,
        conversation_id,
        integrations={name: integration.session_context() for name, integration in runtime.integrations.items()},
        actor=actor_config.id,
        workspace=Path(actor_config.workspace).resolve(),
        rpc={"daemon_url": _daemon_url(runtime)},
    )


@define
class Actor:
    """Default actor: waits on its mailbox and drives one Conversation per message.

    ``run`` is stopped by task cancellation only; there is no in-band stop
    message. ``close`` interrupts the active conversation so cancellation of
    the surrounding task can land promptly.
    """

    config: ActorConfig
    runtime: Runtime
    provider: Provider
    mailbox: Mailbox
    kernels: KernelPool
    status: str = "idle"
    _active: Conversation | None = field(default=None, init=False)
    _mailbox_conversation: str | None = field(default=None, init=False)
    _mailbox_conversation_touched_at: float = field(default=0.0, init=False)
    _mailbox_context_compactions: int = field(default=0, init=False)

    @classmethod
    def from_config(cls, config: ActorConfig, runtime: Runtime, provider: Provider) -> Actor:
        prepare_workspace(Path(config.workspace))
        kernels = KernelPool(runtime.python_kernels, runtime.kernel_limiter)
        kernels.start()
        return cls(
            config=config,
            runtime=runtime,
            provider=provider,
            mailbox=runtime.get_mailbox(f"actor:{config.id}"),
            kernels=kernels,
        )

    async def spawn_conversation(self, conversation_id: str | None = None) -> Conversation:
        cid = conversation_id or uuid.uuid4().hex
        context = await build_conversation_context(self.runtime, self.config, cid)
        tools = all_tool_configs()
        prompt = developer_prompt(
            self.config.persona,
            context.workspace,
            list(self.runtime.integrations.values()),
            actor_id=context.actor,
            has_python=self._has_python,
            global_skills=self.runtime.skill_summaries(),
        )
        history = await HistoryHelper.load(
            self.runtime.history,
            cid,
            tool_specs=tool_specs(tools),
            system_prompt=prompt,
        )
        return Conversation(
            cid,
            context,
            history,
            self.provider,
            HarnessConfig(tools),
            self.runtime,
        )

    async def run(self) -> None:
        self.status = "running"
        try:
            while True:
                message = await self.mailbox.receive()
                await self.handle_mailbox_message(message)
        except ConversationBlocked as exc:
            self.status = "blocked"
            conversation_id = self._active.id if self._active is not None else ""
            await self.runtime.state.set_actor_status(
                self.config.id, "blocked", {"type": type(exc).__name__, "message": str(exc)}
            )
            self.runtime.emit(
                ActorBlockedPayload(
                    self.config.id,
                    conversation_id,
                    str(exc),
                )
            )
        except asyncio.CancelledError:
            self.status = "terminated"
            raise

    async def handle_mailbox_message(self, message: ActorMessage) -> None:
        conversation = await self._conversation_for_message(message)
        inbound_kind = message.source.get("inbound_kind")
        can_compact = message.conversation_id is None and inbound_kind not in {"task_delivery", "conversation_callback"}
        self._active = conversation
        try:
            if inbound_kind in {"task_delivery", "conversation_callback"}:
                outputs = await conversation.append_developer_notice(message.text)
            else:
                input_message = InputMessage("user", self.config.id, text_content(message.text))
                outputs = await conversation.run_loop(input_message, session_mode="actor")
        except ConversationBusy:
            task_id = message.source.get("task_id")
            if inbound_kind == "task_delivery" and isinstance(task_id, str):
                conversation.queue_task_delivery(task_id)
                if task_id in self.runtime.tasks:
                    record = self.runtime.tasks.get(task_id)
                    if record.delivery_state == "pending":
                        record.delivery_state = "queued"
            self.runtime.emit(ActorBusyPayload(self.config.id, conversation.id))
        else:
            self.runtime.emit(
                ActorOutputPayload(
                    self.config.id,
                    conversation.id,
                    len(outputs),
                )
            )
            if can_compact:
                await self._compact_or_continue_mailbox(conversation, message.text)
        finally:
            self._active = None

    async def close(self) -> None:
        if self._active is not None:
            self._active.interrupt()
        await self.kernels.shutdown()
        self.status = "terminated"

    async def _conversation_for_message(self, message: ActorMessage) -> Conversation:
        if message.conversation_id is not None:
            return await self.runtime.conversations.get_or_create(self, message.conversation_id)

        now = time.time()
        conversation_id = self._mailbox_conversation
        if (
            conversation_id is not None
            and now - self._mailbox_conversation_touched_at <= self.runtime.conversations.ttl_s
            and self.runtime.conversations.has(conversation_id)
        ):
            conversation = await self.runtime.conversations.get_or_create(self, conversation_id)
        else:
            conversation = await self.runtime.conversations.get_or_create(self)
            self._mailbox_context_compactions = 0
        self._mailbox_conversation = conversation.id
        self._mailbox_conversation_touched_at = now
        return conversation

    @property
    def _has_python(self) -> bool:
        return any(config.type == "execute_python" for config in all_tool_configs().values())

    def _reached_context_compression_threshold(self, conversation: Conversation) -> bool:
        stop = conversation.last_stop
        return stop is not None and stop.usage.input_tokens >= self.config.context_compression_tokens

    async def _compact_or_continue_mailbox(self, conversation: Conversation, original_user_text: str) -> None:
        if not self._reached_context_compression_threshold(conversation):
            return
        if self._mailbox_context_compactions >= 1:
            await self._stop_mailbox_context_compaction(conversation)
            return

        old_conversation_id = conversation.id
        trigger_input_tokens = conversation.last_stop.usage.input_tokens if conversation.last_stop is not None else 0
        summary = await self._summarize_for_context_compaction(conversation)
        compacted = await self.runtime.conversations.get_or_create(self)
        await compacted.append_items(
            [
                InputMessage("developer", "yuubot", text_content(summary)),
            ]
        )
        self._mailbox_conversation = compacted.id
        self._mailbox_conversation_touched_at = time.time()
        self._mailbox_context_compactions = 1
        self.runtime.emit(
            ActorContextCompactedPayload(
                self.config.id,
                old_conversation_id,
                compacted.id,
                trigger_input_tokens,
                self.config.context_compression_tokens,
            )
        )

        self._active = compacted
        continuation = InputMessage(
            "user",
            self.config.id,
            text_content(_context_compaction_continue_message(original_user_text)),
        )
        outputs = await compacted.run_loop(continuation, session_mode="actor")
        self.runtime.emit(
            ActorOutputPayload(
                self.config.id,
                compacted.id,
                len(outputs),
            )
        )
        await self._compact_or_continue_mailbox(compacted, original_user_text)

    async def _summarize_for_context_compaction(self, conversation: Conversation) -> str:
        self._active = conversation
        outputs = await conversation.append_developer_notice(CONTEXT_COMPACTION_SUMMARY_PROMPT)
        summary = "\n".join(item.text for item in outputs if isinstance(item, (GenText, GenReasoning))).strip()
        if summary:
            return summary
        return "No summary was produced during automatic context compression."

    async def _stop_mailbox_context_compaction(self, conversation: Conversation) -> None:
        await self.runtime.conversations.discard(conversation.id)
        self._mailbox_conversation = None
        self._mailbox_conversation_touched_at = 0.0
        self._mailbox_context_compactions = 0
        self.status = "idle"
        await self.runtime.state.set_actor_status(self.config.id, "idle")
        self.runtime.emit(
            ActorContextCompactionStoppedPayload(
                self.config.id,
                conversation.id,
                conversation.last_stop.usage.input_tokens if conversation.last_stop is not None else 0,
                self.config.context_compression_tokens,
            )
        )
