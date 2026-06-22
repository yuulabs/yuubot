"""Tests for the ``cost_update`` SSE event projection.

Scenario under test:

```
agent.step() → emits "llm.finished" { usage, cost }
  → ConversationManager._record_event:
      _handle_llm_finished → append_history_item (existing)
      NEW: project "cost_update" SSE event reading Budget.usage
  → SSE stream → Admin Conversation panel shows "$0.042 spent"
```

The projector path is verified directly (shape 1 in the instruction's Test
Boundary) and via ``ConversationManager._record_event`` (which exercises
the budget lookup). No live LLM provider call; the runtime event is
constructed by hand.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import yuullm
from yuuagents import Budget
from yuuagents.core.eventbus import RuntimeEvent
from yuuagents.types.values import EventData

from yuubot.core.conversation_events import ConversationSSEProjector
from yuubot.core.conversations import ConversationManager

_AGENT_ID = "agent-1"
_AGENT_NAME = "Test Agent"
_CONVERSATION_ID = "conversation-1"


def event(name: str, data: EventData, timestamp: float = 1.0) -> RuntimeEvent:
    return RuntimeEvent(
        name=name,
        agent_id=_AGENT_ID,
        agent_name=_AGENT_NAME,
        timestamp=timestamp,
        data=data,
    )


def test_projector_cost_update_event_shape() -> None:
    """The projector emits a ``cost_update`` event with the right type + payload."""
    projector = ConversationSSEProjector()
    finished_event = event("llm.finished", {"cost": None})  # event only carries timestamp/agent
    projected = projector.cost_update(
        _CONVERSATION_ID,
        finished_event,
        turn_cost=0.042,
        total_cost=0.137,
    )
    assert projected.event_type == "cost_update"
    assert projected.conversation_id == _CONVERSATION_ID
    as_dict = projected.as_dict()
    assert as_dict["turn_cost"] == 0.042
    assert as_dict["total_cost"] == 0.137
    # The sequence is monotonic per conversation.
    second = projector.cost_update(
        _CONVERSATION_ID,
        finished_event,
        turn_cost=0.020,
        total_cost=0.157,
    )
    assert second.sequence > projected.sequence


async def test_record_event_emits_cost_update_for_llm_finished_with_cost() -> None:
    """``_record_event`` for ``llm.finished`` returns a ``cost_update`` SSE event."""
    manager, store = manager_with_store()
    # Wire the budget lookup: register agent_id → conversation_id, and a
    # fake runtime exposing ``budget_for_agent`` returning a charged Budget.
    budget = Budget(limits={})
    budget.charge("usd", 0.137)
    manager._agent_to_conversation[_AGENT_ID] = _CONVERSATION_ID
    fake_runtime = SimpleNamespace(budget_for_agent=lambda agent_id: budget)
    manager._runtimes[_CONVERSATION_ID] = fake_runtime  # type: ignore[assignment]

    assistant_message = yuullm.assistant("hello")
    cost = yuullm.Cost(
        input_cost=0.010,
        output_cost=0.032,
        total_cost=0.042,
        source="provider",
    )
    finished_event = event(
        "llm.finished",
        {
            "model": "test-model",
            "usage": None,
            "cost": cost,
            "message": assistant_message,
        },
    )

    events = await manager._record_event(_CONVERSATION_ID, finished_event)

    assert len(events) == 1
    assert events[0].event_type == "cost_update"
    payload = events[0].as_dict()
    assert payload["turn_cost"] == approx(0.042)
    assert payload["total_cost"] == approx(0.137)
    # The history append (assistant message) still ran.
    assert store.append_history_item.call_count == 1


async def test_record_event_no_cost_update_when_llm_finished_has_no_cost() -> None:
    """An ``llm.finished`` with ``cost=None`` emits no SSE event."""
    manager, _store = manager_with_store()
    finished_event = event(
        "llm.finished",
        {
            "model": "test-model",
            "usage": None,
            "cost": None,
            "message": None,
        },
    )
    events = await manager._record_event(_CONVERSATION_ID, finished_event)
    assert events == []


async def test_record_event_total_cost_falls_back_to_turn_cost_without_budget() -> None:
    """When the budget lookup misses (cold path), ``total_cost = turn_cost``."""
    manager, _store = manager_with_store()
    # No agent_to_conversation / runtime mapping → budget lookup returns None.

    cost = yuullm.Cost(
        input_cost=0.020,
        output_cost=0.022,
        total_cost=0.042,
        source="provider",
    )
    finished_event = event(
        "llm.finished",
        {"model": "m", "usage": None, "cost": cost, "message": None},
    )
    events = await manager._record_event(_CONVERSATION_ID, finished_event)
    assert len(events) == 1
    assert events[0].event_type == "cost_update"
    payload = events[0].as_dict()
    assert payload["turn_cost"] == approx(0.042)
    assert payload["total_cost"] == approx(0.042)


async def test_record_event_accepts_scalar_float_cost() -> None:
    """A bare ``float`` cost (legacy scalar) projects a ``cost_update`` event."""
    manager, _store = manager_with_store()
    finished_event = event(
        "llm.finished",
        {"model": "m", "usage": None, "cost": 0.5, "message": None},
    )
    events = await manager._record_event(_CONVERSATION_ID, finished_event)
    assert len(events) == 1
    assert events[0].event_type == "cost_update"
    assert events[0].as_dict()["turn_cost"] == approx(0.5)


# ── helpers ──────────────────────────────────────────────────────────────


def approx(value: float) -> object:
    """Indirection so test bodies don't repeat the pytest.approx import."""
    import pytest

    return pytest.approx(value, rel=1e-9)


def manager_with_store() -> tuple[ConversationManager, MagicMock]:
    """Build a ConversationManager whose store no-ops on history writes.

    Mirrors the helper in ``test_conversation_events.py`` so the two suites
    stay consistent in how the manager is wired for direct ``_record_event``
    invocation.
    """
    store = MagicMock()
    store.append_history_item = AsyncMock()
    store.append_history_items = AsyncMock()
    store.conversation_exists = AsyncMock(return_value=True)
    store.list_history_items = AsyncMock(return_value=[])
    manager = ConversationManager(
        store=store,
        repository=MagicMock(),
        yuuagents_config=MagicMock(),
        python_sessions=MagicMock(),
        llm_session_factory_factory=MagicMock(),
    )
    return manager, store
