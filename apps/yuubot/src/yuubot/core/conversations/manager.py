"""Conversation agent lifecycle coordination."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path

import yuullm
from yuuagents import Budget, ProviderPoolSessionFactory
from yuuagents.core.eventbus import RuntimeEvent

from yuubot.core.actors.impls.python_session import ActorPythonSessionFactory
from yuubot.core.assembly import YuuAgentsActorRuntime, start_yuuagents_actor
from yuubot.core.bindings import (
    AgentBinding,
    agent_binding_from_resolved_conversation,
    resolve_conversation_record,
)
from yuubot.core.observability import YuubotTraceContextProvider
from yuubot.resources.records import (
    ActorRecord,
    CapabilitySetRecord,
    ConversationRecord,
    LLMBackendRecord,
)
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.store.models import ActorORM, CapabilitySetORM, LLMBackendORM

from .bindings import (
    ConversationSendBinding,
    ConversationUploadBinding,
    ConversationUploadedFile,
)
from .event_data import AgentEventIdentity
from .events import (
    ConversationFrontendEvent,
    ConversationSSEHeartbeat,
    ConversationSSEProjector,
)
from .runtime_events import (
    budget_for_event,
    cost_update_events,
    handle_llm_finished,
    handle_tool_result,
    record_event,
    set_title_from_first_turn,
)
from .send import send_message
from .store import ConversationStore
from .timing import _conversation_timing_span
from .uploads import (
    store_uploads,
)


_STOP_RECEIPT_TIMEOUT_S = 2.0


@dataclass
class ConversationManager:
    store: ConversationStore
    repository: ResourceRepository
    python_sessions: ActorPythonSessionFactory
    llm_session_factory_factory: Callable[
        [AgentBinding], ProviderPoolSessionFactory | None
    ]
    trace_context: YuubotTraceContextProvider | None = None
    workspace_root: Path = field(
        default_factory=lambda: Path("~/.yuubot/workspace").expanduser()
    )
    global_skills_path: Path | None = None
    _runtimes: dict[str, YuuAgentsActorRuntime] = field(
        default_factory=dict, init=False
    )
    _agent_to_conversation: dict[str, str] = field(default_factory=dict, init=False)
    _observed_runtimes: dict[str, int] = field(default_factory=dict, init=False)
    _subscribers: dict[
        str,
        set[asyncio.Queue[ConversationFrontendEvent | ConversationSSEHeartbeat]],
    ] = field(
        default_factory=dict,
        init=False,
    )
    # One in-flight turn task per conversation. A conversation can only have
    # one active turn at a time. The frontend disables the Send button during
    # generation (replaced by the Stop button), so a second ``send_message``
    # while a turn is in flight is not normally reachable from the UI. If one
    # does arrive, ``send_message`` defensively awaits the existing task
    # before starting a new one (no server-side queue is needed — the input
    # box itself is the buffer).
    _in_flight_tasks: dict[str, asyncio.Task[None]] = field(
        default_factory=dict, init=False
    )
    # Per-conversation cancel event. ``cancel_turn`` sets the event AND calls
    # ``task.cancel()``; the agent loop checks it once at the top of each
    # ``while not agent.done`` iteration as a single-point safety net (closes
    # the window where the loop is between awaits and ``task.cancel()``'s
    # scheduled CancelledError has not yet been delivered).
    _cancel_events: dict[str, asyncio.Event] = field(default_factory=dict, init=False)
    _runtime_expiry_tasks: dict[str, asyncio.Task[None]] = field(
        default_factory=dict,
        init=False,
    )
    _sse_projector: ConversationSSEProjector = field(
        default_factory=ConversationSSEProjector,
        init=False,
    )

    async def send_message(
        self,
        *,
        conversation_id: str,
        text: str,
        binding: ConversationSendBinding | None = None,
        message_id: str | None = None,
    ) -> tuple[ConversationRecord, str]:
        return await send_message(
            self,
            conversation_id=conversation_id,
            text=text,
            binding=binding,
            message_id=message_id,
        )

    async def store_uploads(
        self,
        *,
        conversation_id: str,
        files: list[tuple[str, bytes, str]],
        binding: ConversationUploadBinding | None = None,
    ) -> list[ConversationUploadedFile]:
        return await store_uploads(
            self,
            conversation_id=conversation_id,
            files=files,
            binding=binding,
        )


    def _start_turn_task(
        self,
        *,
        conversation_id: str,
        runtime: YuuAgentsActorRuntime,
        message: yuullm.Message,
    ) -> None:
        self._cancel_runtime_expiry(conversation_id)
        cancel_event = asyncio.Event()
        self._cancel_events[conversation_id] = cancel_event

        task = asyncio.create_task(
            self._run_turn_task(
                conversation_id=conversation_id,
                runtime=runtime,
                message=message,
                cancel_event=cancel_event,
            )
        )
        self._in_flight_tasks[conversation_id] = task

        def _cleanup(_t: asyncio.Task[None], cid: str = conversation_id) -> None:
            if self._in_flight_tasks.get(cid) is _t:
                self._in_flight_tasks.pop(cid, None)
            # ``_cancel_events`` is turn-scoped (the event for *this* turn's
            # single-point safety trip); pop on task-done.
            self._cancel_events.pop(cid, None)

        task.add_done_callback(_cleanup)

    async def _run_turn_task(
        self,
        *,
        conversation_id: str,
        runtime: YuuAgentsActorRuntime,
        message: yuullm.Message,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        try:
            await runtime.handle_conversation_message(
                conversation_id,
                message,
                cancel_event=cancel_event,
            )
        except asyncio.CancelledError:
            # The agent loop's own CancelledError handler already ran
            # (flushed the reporter, cancelled tools, synthesised results).
            # The loop is the sole emitter of ``agent.turn_completed``;
            # ``cancel_turn`` does NOT synthesise it. Re-raise so asyncio
            # marks the task cancelled.
            raise
        except Exception as exc:
            event = RuntimeEvent(
                name="agent.turn.error",
                agent_id="",
                agent_name="",
                timestamp=time.time(),
                data={"error": str(exc)},
            )
            frontend_event = self._sse_projector.error(
                conversation_id,
                event,
                str(exc),
            )
            for queue in tuple(self._subscribers.get(conversation_id, ())):
                await queue.put(frontend_event)
        finally:
            self._touch_runtime_expiry(conversation_id, runtime)

    async def cancel_turn(self, conversation_id: str) -> dict[str, bool]:
        """Cancel the in-flight turn for ``conversation_id`` (Stop button).

        Sets the single-point safety ``cancel_event`` and calls
        ``task.cancel()``. The loop's own CancelledError handler then:
        flushes the reporter, cancels running tools, and synthesises
        ``[cancelled]`` tool results so the in-memory history stays legal.
        The loop ``break``s out of the turn (always — there is no
        ``continue`` branch anymore; the queue mechanism is gone), so the
        loop's terminal path emits ``agent.turn_completed`` via the normal
        loop-exit path. ``cancel_turn`` itself does NOT synthesise
        ``turn_completed`` — the loop is the sole emitter.

        Waits briefly for the cancelled task to finish. If the provider SDK
        or a tool does not cooperate with cancellation, the HTTP stop receipt
        still returns and the task remains in-flight until its own cleanup
        eventually finishes.

        Returns ``{"cancelled": bool, "pending": bool}`` — ``pending`` means
        the cancellation was signalled but cleanup had not completed before
        the stop receipt timeout.
        """
        task = self._in_flight_tasks.get(conversation_id)
        if task is None or task.done():
            return {"cancelled": False, "pending": False}

        # Single-point safety trip: the loop checks ``cancel_event.is_set()``
        # once at the top of each iteration; if true it raises
        # CancelledError into the handler below. This closes the window
        # where the loop is between awaits and ``task.cancel()``'s
        # scheduled CancelledError has not yet been delivered.
        event = self._cancel_events.get(conversation_id)
        if event is not None:
            event.set()

        # Break mid-LLM-stream awaits. If the loop already exited between
        # the done() check and now, task.cancel() is a no-op on the
        # completing task; we report ``cancelled=False`` in that case.
        scheduled = task.cancel()
        if not scheduled:
            return {"cancelled": False, "pending": False}

        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=_STOP_RECEIPT_TIMEOUT_S)
            pending = False
        except TimeoutError:
            pending = True
        except asyncio.CancelledError:
            pending = False
        except Exception:
            # Loop's error path already emitted an error SSE event; suppress
            # so the HTTP receipt still returns 200 (the frontend drops
            # ``isSending`` via the error SSE or ``turn_completed``).
            pending = False

        return {"cancelled": True, "pending": pending}

    async def delete_conversation(self, conversation_id: str) -> bool:
        exists = await self.store.conversation_exists(conversation_id)
        if not exists:
            return False
        await self.cancel_turn(conversation_id)
        await self.drop_cached_conversation_agent(conversation_id)
        deleted = await self.store.delete_conversation(conversation_id)
        if deleted:
            self._drop_conversation_indexes(conversation_id)
        return deleted

    async def subscribe_events(
        self,
        conversation_id: str,
        *,
        heartbeat_interval: float = 25.0,
    ) -> AsyncIterator[ConversationFrontendEvent | ConversationSSEHeartbeat]:
        """Subscribe to the long-lived SSE stream for one conversation.

        The stream stays open across turns. ``agent.turn_completed`` is
        projected to a named ``turn_completed`` event that the frontend
        listens for; it does **not** close the stream. Closing on each
        turn was the regression that dropped the second turn's events:

        ```
        mount → connectSse (stream open)
        User msg 1 → daemon emits transcript_delta → turn_completed
          → stream closed by daemon → EventSource onerror → frontend
            tears down the EventSource  ← completion signalled via TCP close
        User msg 2 → handleSend does not reopen stream
          → daemon emits transcript_delta into an empty subscriber set
          → events dropped → "Waiting for response…" hangs
        ```

        A heartbeat (: heartbeat\\n\\n comment frame, rendered by the
        daemon SSE handler) is yielded whenever no event arrives within
        ``heartbeat_interval`` seconds — short enough to keep any idle
        HTTP hop or middlebox from closing the connection, long enough
        to be negligible overhead (default 25s).
        """
        queue: asyncio.Queue[ConversationFrontendEvent | ConversationSSEHeartbeat] = (
            asyncio.Queue()
        )
        subscribers = self._subscribers.setdefault(conversation_id, set())
        subscribers.add(queue)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=heartbeat_interval,
                    )
                except asyncio.TimeoutError:
                    yield ConversationSSEHeartbeat(conversation_id)
                    continue
                yield event
        finally:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(conversation_id, None)

    async def drop_cached_conversation_agent(self, conversation_id: str) -> bool:
        """Evict the in-memory runtime+agent cache for ``conversation_id``.

        Forces the next :meth:`send_message` to fall into the restart
        branch: a fresh runtime is built and ``ensure_conversation_agent``
        reads ``store.history`` to restore the persisted prefix.

        Used by tests to simulate a daemon-restart cache drop without
        losing the on-disk history. Returns ``True`` if a cached runtime
        was evicted.
        """
        self._cancel_runtime_expiry(conversation_id)
        runtime = self._runtimes.pop(conversation_id, None)
        if runtime is None:
            self._drop_conversation_indexes(conversation_id)
            return False
        await runtime.close()
        # Drop this conversation's agent-to-conversation index entries;
        # the next send re-registers them via ensure_conversation_agent.
        self._drop_conversation_indexes(conversation_id)
        return True

    def _drop_conversation_indexes(self, conversation_id: str) -> None:
        for agent_id, stored_id in list(self._agent_to_conversation.items()):
            if stored_id == conversation_id:
                self._agent_to_conversation.pop(agent_id, None)
        self._observed_runtimes.pop(conversation_id, None)
        self._cancel_events.pop(conversation_id, None)
        self._sse_projector.drop_conversation(conversation_id)

    def _cancel_runtime_expiry(self, conversation_id: str) -> None:
        task = self._runtime_expiry_tasks.pop(conversation_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _touch_runtime_expiry(
        self,
        conversation_id: str,
        runtime: YuuAgentsActorRuntime,
    ) -> None:
        if runtime.idle_timeout_s <= 0:
            return
        self._cancel_runtime_expiry(conversation_id)
        self._runtime_expiry_tasks[conversation_id] = asyncio.create_task(
            self._expire_runtime_when_idle(conversation_id, runtime)
        )

    async def _expire_runtime_when_idle(
        self,
        conversation_id: str,
        runtime: YuuAgentsActorRuntime,
    ) -> None:
        try:
            await asyncio.sleep(runtime.idle_timeout_s)
            task = self._in_flight_tasks.get(conversation_id)
            if task is not None and not task.done():
                self._touch_runtime_expiry(conversation_id, runtime)
                return
            if self._runtimes.get(conversation_id) is runtime:
                await self.drop_cached_conversation_agent(conversation_id)
        except asyncio.CancelledError:
            raise
        finally:
            current = asyncio.current_task()
            if self._runtime_expiry_tasks.get(conversation_id) is current:
                self._runtime_expiry_tasks.pop(conversation_id, None)

    async def _require_conversation(self, conversation_id: str) -> ConversationRecord:
        conversation = await self.store.get_conversation(conversation_id)
        if conversation is None:
            raise LookupError(f"conversation {conversation_id!r} does not exist")
        return conversation

    async def _active_actor(self, actor_id: str) -> ActorRecord:
        actor = await self.repository.get(ActorORM, actor_id)
        if actor is None or not actor.enabled:
            raise LookupError(f"active actor {actor_id!r} does not exist")
        return actor

    async def _require_capability_set(
        self, capability_set_id: str
    ) -> CapabilitySetRecord:
        capability_set = await self.repository.get(CapabilitySetORM, capability_set_id)
        if capability_set is None:
            raise LookupError(f"capability set {capability_set_id!r} does not exist")
        return capability_set

    async def _require_llm_backend(self, llm_backend_id: str) -> LLMBackendRecord:
        llm_backend = await self.repository.get(LLMBackendORM, llm_backend_id)
        if llm_backend is None:
            raise LookupError(f"llm backend {llm_backend_id!r} does not exist")
        return llm_backend

    def _resolve_workspace_path(self, relative_name: str | None) -> Path | None:
        """Resolve a CapabilitySet's workspace_path (relative name) under workspace_root.

        Returns None if relative_name is empty — caller handles None by not
        setting workspace_path on the binding (facade will be None, no workspace needed).
        """
        if not relative_name or not relative_name.strip():
            return None
        root = self.workspace_root.expanduser().resolve()
        path = (root / relative_name).resolve()
        if not path.is_relative_to(root):
            raise ValueError(
                f"workspace_path {relative_name!r} escapes workspace root {root}"
            )
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def _runtime_for(
        self, conversation: ConversationRecord
    ) -> YuuAgentsActorRuntime:
        runtime = self._runtimes.get(conversation.conversation_id)
        if runtime is not None:
            with _conversation_timing_span(
                "conversation.runtime",
                "runtime_cache_hit",
                conversation_id=conversation.conversation_id,
            ):
                pass
            return runtime

        with _conversation_timing_span(
            "conversation.runtime",
            "runtime_created",
            conversation_id=conversation.conversation_id,
        ) as timing:
            resolved = await resolve_conversation_record(self.repository, conversation)
            workspace_path = self._resolve_workspace_path(
                resolved.capability_set.workspace_path
            )
            binding = agent_binding_from_resolved_conversation(
                resolved,
                workspace_path=workspace_path,
                global_skills_path=self.global_skills_path,
            )
            facade = None
            if binding.workspace_path is not None:
                with _conversation_timing_span(
                    "conversation.runtime",
                    "facade_bound",
                    conversation_id=conversation.conversation_id,
                    actor_id=binding.actor.id,
                    owner_id=binding.owner_id,
                ) as facade_timing:
                    facade = await self.python_sessions.bind_facade(
                        binding,
                        mailbox_id=f"conversation:{conversation.conversation_id}",
                    )
                    facade_timing.attrs(
                        facade_root=str(facade.root),
                        venv_python=facade.venv_python or "",
                    )
            llm_session_factory = self.llm_session_factory_factory(binding)
            runtime = start_yuuagents_actor(
                binding,
                facade=facade,
                llm_session_factory=llm_session_factory,
                trace_context=self.trace_context,
            )
            self._runtimes[conversation.conversation_id] = runtime
            self._observe_runtime(conversation.conversation_id, runtime)

            # Inject real conversation_id into trace context so spans link
            # to yuubot conversation records (not synthetic uuid5).
            if self.trace_context is not None:
                definition_name = runtime.conversation_definition.name
                self.trace_context.register(
                    f"{definition_name}:conversation:{conversation.conversation_id}",
                    model=binding.llm.model,
                    conversation_id=conversation.conversation_id,
                )
            timing.attrs(
                actor_id=binding.actor.id,
                owner_id=binding.owner_id,
                workspace_path=str(workspace_path) if workspace_path else "",
                facade_required=facade is not None,
            )

        return runtime

    def _observe_runtime(
        self,
        conversation_id: str,
        runtime: YuuAgentsActorRuntime,
    ) -> None:
        runtime_id = id(runtime)
        if self._observed_runtimes.get(conversation_id) == runtime_id:
            return
        runtime.stage.eventbus.subscribe(self._on_runtime_event)
        self._observed_runtimes[conversation_id] = runtime_id

    async def _on_runtime_event(self, event: RuntimeEvent) -> None:
        conversation_id = self._conversation_id_for_event(event)
        if conversation_id is None:
            return
        frontend_events = await self._record_event(conversation_id, event)
        if not frontend_events:
            return
        for queue in tuple(self._subscribers.get(conversation_id, ())):
            for frontend_event in frontend_events:
                await queue.put(frontend_event)

    def _conversation_id_for_event(self, event: RuntimeEvent) -> str | None:
        if event.agent_id in self._agent_to_conversation:
            return self._agent_to_conversation[event.agent_id]
        identity = AgentEventIdentity.from_event(event)
        if identity.entity_id and identity.entity_id in self._agent_to_conversation:
            return self._agent_to_conversation[identity.entity_id]
        if identity.parent_id and identity.parent_id in self._agent_to_conversation:
            return self._agent_to_conversation[identity.parent_id]
        return None

    async def _record_event(
        self,
        conversation_id: str,
        event: RuntimeEvent,
    ) -> list[ConversationFrontendEvent]:
        return await record_event(self, conversation_id, event)

    def _cost_update_events(
        self,
        conversation_id: str,
        event: RuntimeEvent,
    ) -> list[ConversationFrontendEvent]:
        return cost_update_events(self, conversation_id, event)

    def _budget_for_event(self, event: RuntimeEvent) -> Budget | None:
        return budget_for_event(self, event)

    async def _handle_llm_finished(
        self,
        conversation_id: str,
        event: RuntimeEvent,
    ) -> None:
        await handle_llm_finished(self, conversation_id, event)

    async def _set_title_from_first_turn(self, conversation_id: str) -> None:
        await set_title_from_first_turn(self, conversation_id)

    async def _handle_tool_result(
        self,
        conversation_id: str,
        event: RuntimeEvent,
    ) -> list[ConversationFrontendEvent]:
        return await handle_tool_result(self, conversation_id, event)
