"""Shared test helpers and fixtures."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import yuullm

from yuuagents.agent.agent import Agent
from yuuagents.agent.llm_backend import AgentLLMBackend
from yuuagents.core.eventbus import EventBus
from yuuagents.obs.entitylog import EntityLog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeSession:
    """Session that plays back scripted stream responses and owns history commits."""

    def __init__(
        self,
        factory: "FakeSessionFactory",
        history: yuullm.History,
    ) -> None:
        self._factory = factory
        self._history = list(history)

    @property
    def history(self) -> yuullm.History:
        return self._history

    def append(self, msg: yuullm.Message) -> None:
        if msg.role == "tool":
            _validate_tool_message(self._history, msg)
        self._history.append(msg)

    async def stream(
        self,
        **options: object,
    ) -> yuullm.StreamResult:
        messages, _tools = yuullm.split_history(self._history)
        self._factory.calls.append(list(messages))
        self._factory.kwargs.append(dict(options))
        turn = self._factory.turns.pop(0)
        store = yuullm.Store(
            usage=yuullm.Usage(
                provider="fake",
                model=self._factory.selector or "fake",
                input_tokens=10,
                output_tokens=5,
            ),
            cost=yuullm.Cost(
                input_cost=0.0,
                output_cost=0.0,
                total_cost=self._factory.cost_total,
            ),
        )

        async def _gen() -> AsyncIterator[yuullm.StreamItem]:
            content: yuullm.MessageContent = []
            for item in turn:
                yield item
                _accumulate_session_item(content, item)
            if content:
                self._history.append(yuullm.Message(role="assistant", content=content))

        return _gen(), store


class FakeSessionFactory:
    """Factory for fake stateful sessions used by tests."""

    def __init__(
        self,
        turns: list[list[yuullm.StreamItem]],
        *,
        cost_total: float = 0.0,
        selector: str = "fake-model",
        calls: list[list[yuullm.Message]] | None = None,
        kwargs: list[dict[str, object]] | None = None,
    ) -> None:
        self.turns = [list(t) for t in turns]
        self.calls = calls if calls is not None else []
        self.kwargs = kwargs if kwargs is not None else []
        self.cost_total = cost_total
        self.selector = selector
        self.sessions: list[FakeSession] = []

    def create_session(self, history: yuullm.History) -> FakeSession:
        session = FakeSession(self, history)
        self.sessions.append(session)
        return session

    def with_selector(self, selector: str) -> "FakeSessionFactory":
        return FakeSessionFactory(
            self.turns,
            cost_total=self.cost_total,
            selector=selector,
            calls=self.calls,
            kwargs=self.kwargs,
        )


def _accumulate_session_item(
    content: yuullm.MessageContent,
    item: yuullm.StreamItem,
) -> None:
    match item:
        case yuullm.Response(item=response):
            content.append(response)
        case yuullm.ToolCall() as tc:
            content.append(yuullm.tool_call_item(tc))
        case yuullm.ThinkingBlock() as tb:
            content.append(tb.to_message_item())
        case yuullm.AttemptRecovery():
            content.clear()
        case yuullm.Reasoning() | yuullm.Tick():
            pass


def _validate_tool_message(history: yuullm.History, msg: yuullm.Message) -> None:
    pending = _pending_tool_call_ids(history)
    for item in msg.content:
        if item["type"] == "tool_result" and item["tool_call_id"] in pending:
            return
    raise ValueError("tool result does not match an open tool call")


def _pending_tool_call_ids(history: yuullm.History) -> set[str]:
    pending: set[str] = set()
    for item in history:
        if not isinstance(item, yuullm.Message):
            continue
        if item.role == "assistant":
            pending.update(
                block["id"] for block in item.content if block["type"] == "tool_call"
            )
        if item.role == "tool":
            pending.difference_update(
                block["tool_call_id"]
                for block in item.content
                if block["type"] == "tool_result"
            )
    return pending


def text_response(text: str) -> yuullm.Response:
    return yuullm.Response({"type": "text", "text": text})


def tool_call(
    name: str, args: dict[str, object], *, call_id: str = "tc_1"
) -> yuullm.ToolCall:
    return yuullm.ToolCall(id=call_id, name=name, arguments=json.dumps(args))


def _make_agent(llm: FakeSessionFactory, bus: EventBus = EventBus()) -> Agent:
    """Create a test Agent.

    The returned agent is a minimal LLM execution primitive.
    """
    from yuuagents.agent.agent import make_agent_id

    session = llm.create_session([yuullm.system("You are a test agent.")])
    llm_backend = AgentLLMBackend(
        session=session,
        factory=llm,
        options={},
        model=llm.selector or "fake-model",
    )
    return Agent(
        id=make_agent_id(),
        name="test-agent",
        llm=llm_backend,
        log=EntityLog(),
        eventbus=bus,
    )
