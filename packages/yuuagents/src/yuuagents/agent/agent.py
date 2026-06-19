from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import yuullm
from attrs import define, field

from yuuagents.agent.llm_backend import AgentLLMBackend
from yuuagents.obs.entitylog import (
    EntityLog,
    PeriodicReporter,
    _NoopReporter,
    content_block,
)
from yuuagents.core.eventbus import EventBus

if TYPE_CHECKING:
    from yuuagents.agent.definition import AgentDefinition
    from yuuagents.core.stage import Stage


def make_agent_id() -> str:
    return f"agent_{uuid4().hex[:12]}"


@define
class Agent:
    """An LLM state instance: identity, LLM backend, and entity log.

    Agent is a pure LLM execution primitive — it does NOT hold budget,
    runtime, eventbus, or pending tool calls. The orchestrator (yuubot)
    manages those.
    """

    id: str
    name: str
    llm: AgentLLMBackend
    log: EntityLog = field(factory=EntityLog, repr=False)
    eventbus: EventBus | None = field(default=None, repr=False)

    _entity_reporter: PeriodicReporter | _NoopReporter = field(init=False, repr=False)
    _done_flag: bool = field(default=False, init=False)
    _closed: bool = field(default=False, init=False)

    def __attrs_post_init__(self) -> None:
        if self.eventbus is not None:
            self._entity_reporter = PeriodicReporter(
                log=self.log,
                eventbus=self.eventbus,
                entity_id=self.id,
                entity_type="agent",
                block_factory=content_block,
            )
        else:
            self._entity_reporter = _NoopReporter()

    @classmethod
    def build(
        cls,
        stage: Stage,
        definition: AgentDefinition,
        *,
        agent_id: str | None = None,
        initial_history: yuullm.History | None = None,
    ) -> Agent:
        """Factory: create an Agent from Stage resources and AgentDefinition.

        If ``initial_history`` is provided, use it directly (caller constructs
        tool specs and history). Otherwise, build a minimal history from the
        definition's system prompt.
        """
        from yuuagents.agent.actor import _resolve_agent_llm

        agent_id = agent_id or make_agent_id()

        # Resolve LLM backend
        llm_session_factory, llm_options = _resolve_agent_llm(stage, definition.llm)

        # Use provided initial history or build minimal one
        if initial_history is not None:
            history = initial_history
        else:
            history = _build_initial_history(definition)

        llm_session = llm_session_factory.create_session(history)

        llm_backend = AgentLLMBackend(
            session=llm_session,
            factory=llm_session_factory,
            options=llm_options,
            model=definition.llm.model,
        )

        return cls(
            id=agent_id,
            name=definition.name,
            llm=llm_backend,
            log=EntityLog(),
            eventbus=stage.eventbus,
        )

    @property
    def history(self) -> yuullm.History:
        return self.llm.session.history

    @property
    def done(self) -> bool:
        return self._done_flag

    def append(self, message: yuullm.Message) -> None:
        """Append a message to the LLM session history."""
        self.llm.session.append(message)
        self._done_flag = False

    def replace_history(self, history: yuullm.History) -> None:
        """Replace the session history with a new one."""
        self.llm.session = self.llm.factory.create_session(list(history))
        self._done_flag = False

    async def step(self) -> tuple[yuullm.Message, yuullm.Store]:
        """Call the LLM, update history, stream output through self.log.

        Returns (assistant_message, store).
        Does NOT charge budget, does NOT execute tools.
        """
        await self._entity_reporter.start()

        history_len_before_stream = len(self.history)

        stream, store = await self.llm.session.stream(**self.llm.options)

        streamed_reasoning = False
        async for item in stream:
            match item:
                case yuullm.Tick():
                    pass
                case yuullm.Reasoning(item=reasoning):
                    streamed_reasoning = True
                    await self.log.write(_reasoning_item(reasoning))
                case yuullm.ThinkingBlock() as tb:
                    if not streamed_reasoning:
                        await self.log.write(tb.to_message_item())
                case yuullm.Response(item=response):
                    await self.log.write(response)
                case yuullm.ToolCall() as tc:
                    tool_call_item = yuullm.tool_call_item(tc)
                    await self.log.write(tool_call_item)
                case yuullm.AttemptRecovery() as recovery:
                    await self._emit_recovery(recovery)

        message = _last_assistant_message(self.history, history_len_before_stream)
        tool_calls = _message_tool_calls(message)

        self._done_flag = len(tool_calls) == 0

        await self._entity_reporter.flush()
        return message, store

    async def close(self, status: str = "closed") -> None:
        """End agent lifecycle."""
        if self._closed:
            return
        self._closed = True
        await self._entity_reporter.flush_final(status)

    async def _emit_recovery(self, recovery: yuullm.AttemptRecovery) -> None:
        if self.eventbus is not None:
            await self.eventbus.emit(
                "llm.recovered",
                {
                    "agent_id": self.id,
                    "agent_name": self.name,
                    "recovery": recovery,
                },
            )

    async def flush_entitylog(self) -> None:
        await self._entity_reporter.flush()


def _build_initial_history(definition: AgentDefinition | None = None) -> yuullm.History:
    """Build minimal initial history from a definition (system prompt only)."""
    history: yuullm.History = []
    if definition is not None and definition.prompt.system:
        history.append(yuullm.system(definition.prompt.system))
    return history


# ── Helper functions ─────────────────────────────────────────────


def _last_assistant_message(
    history: yuullm.History,
    history_len_before_stream: int,
) -> yuullm.Message:
    if len(history) <= history_len_before_stream:
        raise RuntimeError("LLM stream finished without a committed assistant message")
    item = history[-1]
    if not isinstance(item, yuullm.Message) or item.role != "assistant":
        raise RuntimeError("LLM stream finished without a committed assistant message")
    return item


def _reasoning_item(item: yuullm.ContentItem) -> yuullm.ThinkingItem:
    return {"type": "thinking", "thinking": yuullm.render_item_text(item)}


def _message_tool_calls(message: yuullm.Message) -> list[yuullm.ToolCall]:
    return [
        yuullm.ToolCall(
            id=item["id"],
            name=item["name"],
            arguments=item["arguments"],
        )
        for item in message.content
        if item["type"] == "tool_call"
    ]
