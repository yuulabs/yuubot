from __future__ import annotations

from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    AsyncExitStack,
    asynccontextmanager,
)
import inspect
import time
from collections.abc import Awaitable
from typing import Literal, Protocol

from attrs import define, field

from yuuagents.types.values import EventData, EventPayload

EventName = Literal[
    "agent.started",
    "agent.turn",
    "agent.turn_started",
    "agent.turn_completed",
    "agent.turn.error",
    "output.entity",
    "output.chunk",
    "output.entity_end",
    "llm.started",
    "llm.finished",
    "llm.recovered",
    "budget.exceeded",
    "runtime.task_pending",
    "runtime.task_running",
    "runtime.task_completed",
    "runtime.task_failed",
    "runtime.task_cancelled",
    "runtime.task_timed_out",
    "runtime.task_detached",
    "runtime.task_killed",
    "runtime.usage_reported",
    "actor.message_received",
    "actor.message_unhandled",
    "python.session_started",
    "python.cell_started",
    "python.cell_finished",
    "python.timeout",
    "python.interrupted",
    "python.session_closed",
]

type ScopeHandle = AbstractContextManager[object] | AbstractAsyncContextManager[object]
type ObserverResult = None | Awaitable[None]
type ScopeResult = ScopeHandle | Awaitable[ScopeHandle | None] | None


@define
class RuntimeEvent:
    name: str
    agent_id: str
    agent_name: str
    timestamp: float = field(factory=time.time)
    data: EventPayload = field(factory=dict)


class Observer(Protocol):
    def on_event(self, event: RuntimeEvent) -> ObserverResult: ...


class ScopeObserver(Protocol):
    def on_scope(self, event: RuntimeEvent) -> ScopeResult: ...


@define
class EventBus:
    """Publish-subscribe event bus. Observers receive RuntimeEvent on emit()."""

    _observers: list[Observer | ScopeObserver | EventCallable] = field(
        factory=list,
        init=False,
        repr=False,
    )

    def subscribe(self, observer: Observer | ScopeObserver | EventCallable) -> None:
        self._observers.append(observer)

    async def emit(self, event_name: str, payload: EventPayload | None = None) -> None:
        event = self._event(event_name, payload)
        for observer in self._observers:
            try:
                on_event = getattr(observer, "on_event", None)
                if on_event is not None:
                    result = on_event(event)
                elif callable(observer):
                    result = observer(event)
                else:
                    continue
                if inspect.isawaitable(result):
                    await result
            except BaseException:
                continue

    @asynccontextmanager
    async def scope(
        self,
        event_name: EventName,
        payload: EventPayload | None = None,
    ):
        """Enter observer-managed scope lifecycles for a runtime operation."""

        event = self._event(event_name, payload)
        async with AsyncExitStack() as stack:
            for observer in self._observers:
                try:
                    on_scope = getattr(observer, "on_scope", None)
                    if on_scope is None:
                        continue
                    result = on_scope(event)
                    if inspect.isawaitable(result):
                        result = await result
                    if result is not None:
                        if not isinstance(
                            result, AbstractAsyncContextManager | AbstractContextManager
                        ):
                            continue
                        await _enter_scope_handle(stack, result)
                except BaseException:
                    continue
            try:
                yield event
            except BaseException as exc:
                error_payload = dict(event.data)
                error_payload["error"] = f"{type(exc).__name__}: {exc}"
                await self.emit(_error_event_name(event_name), error_payload)
                raise

    def _event(
        self,
        event_name: str,
        payload: EventPayload | None,
    ) -> RuntimeEvent:
        data: EventData = dict(payload or {})
        return RuntimeEvent(
            name=event_name,
            agent_id=str(data.get("agent_id", "")),
            agent_name=str(data.get("agent_name", "")),
            data=data,
        )


class EventCallable(Protocol):
    def __call__(self, event: RuntimeEvent) -> ObserverResult: ...


async def _enter_scope_handle(stack: AsyncExitStack, handle: ScopeHandle) -> None:
    if isinstance(handle, AbstractAsyncContextManager):
        await stack.enter_async_context(handle)
        return
    if isinstance(handle, AbstractContextManager):
        stack.enter_context(handle)


def _error_event_name(event_name: EventName) -> EventName:
    if event_name == "agent.turn":
        return "agent.turn.error"
    return event_name
