"""Actor: a routable agent entity that drives Conversations from its mailbox."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec
from attrs import define, field

from ..chat import Conversation, ConversationBlocked, ConversationBusy
from ..chat.harness import HarnessConfig
from ..chat.history import HistoryHelper
from ..llm import Provider
from ..domain.messages import ActorMessage, ConversationContext, InputMessage, ModelCard, text_content
from ..python import KernelPool
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


def _daemon_url(runtime: Runtime) -> str:
    from ..web.run_state import read as read_run_state

    state = read_run_state(runtime.data_dir)
    if state is not None:
        return f"http://{state.host}:{state.port}"
    return "http://127.0.0.1:8765"


async def build_conversation_context(
    *,
    runtime: Runtime,
    actor_config: ActorConfig,
    conversation_id: str,
) -> ConversationContext:
    return ConversationContext(
        model=actor_config.model,
        conversation_id=conversation_id,
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
        context = await build_conversation_context(runtime=self.runtime, actor_config=self.config, conversation_id=cid)
        tools = all_tool_configs()
        prompt = developer_prompt(
            self.config.persona,
            context.workspace,
            [integration.package_path for integration in self.runtime.integrations.values()],
            has_python=self._has_python,
        )
        history = await HistoryHelper.load(
            self.runtime.history,
            cid,
            tool_specs=tool_specs(tools),
            system_prompt=prompt,
        )
        return Conversation(
            id=cid,
            context=context,
            history=history,
            provider=self.provider,
            harness_config=HarnessConfig(tools=tools),
            runtime=self.runtime,
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
            self.runtime.emit("actor.blocked", actor_id=self.config.id, conversation_id=conversation_id, reason=str(exc))
        except asyncio.CancelledError:
            self.status = "terminated"
            raise

    async def handle_mailbox_message(self, message: ActorMessage) -> None:
        conversation = await self.runtime.conversations.get_or_create(self, message.conversation_id)
        self._active = conversation
        try:
            inbound_kind = message.source.get("inbound_kind")
            if inbound_kind in {"task_delivery", "conversation_callback"}:
                developer = InputMessage(role="developer", name="yuubot", content=text_content(message.text))
                await conversation._append(developer)
                outputs = await conversation.run_continuation()
            elif inbound_kind == "cron_wakeup":
                input_message = InputMessage(role="user", name=self.config.id, content=text_content(message.text))
                outputs = await conversation.run_loop(input_message)
            else:
                input_message = InputMessage(role="user", name=self.config.id, content=text_content(message.text))
                outputs = await conversation.run_loop(input_message)
        except ConversationBusy:
            self.runtime.emit("actor.busy", actor_id=self.config.id, conversation_id=conversation.id)
        else:
            self.runtime.emit(
                "actor.output",
                actor_id=self.config.id,
                conversation_id=conversation.id,
                outputs=len(outputs),
            )
        finally:
            self._active = None

    async def close(self) -> None:
        if self._active is not None:
            self._active.interrupt()
        await self.kernels.shutdown()
        self.status = "terminated"

    @property
    def _has_python(self) -> bool:
        return any(config.type == "execute_python" for config in all_tool_configs().values())
