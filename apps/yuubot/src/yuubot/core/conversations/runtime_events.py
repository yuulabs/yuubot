"""Runtime event persistence and SSE projection."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yuullm
from yuuagents import Budget
from yuuagents.core.eventbus import RuntimeEvent

from yuubot.core.assembly._history_codec import decode_prompt_item

from .event_data import LLMFinishedData, _cost_total
from .events import ConversationFrontendEvent, render_tool_output_final_text
from .titles import _conversation_title_from_first_turn

if TYPE_CHECKING:
    from .manager import ConversationManager


PROJECTED_RUNTIME_EVENTS = {
    "output.chunk",
    "agent.turn.error",
    "agent.turn_started",
    "agent.turn_completed",
    "budget.exceeded",
}


async def record_event(
    manager: ConversationManager,
    conversation_id: str,
    event: RuntimeEvent,
) -> list[ConversationFrontendEvent]:
    if event.name in PROJECTED_RUNTIME_EVENTS:
        return manager._sse_projector.project_runtime_event(conversation_id, event)
    if event.name == "llm.finished":
        await handle_llm_finished(manager, conversation_id, event)
        return cost_update_events(manager, conversation_id, event)
    if event.name == "tool.result_appended":
        return await handle_tool_result(manager, conversation_id, event)
    return []


def cost_update_events(
    manager: ConversationManager,
    conversation_id: str,
    event: RuntimeEvent,
) -> list[ConversationFrontendEvent]:
    """Project a ``cost_update`` SSE event from an ``llm.finished`` event."""
    finished = LLMFinishedData.from_event(event)
    turn_cost = _cost_total(finished.cost)
    if turn_cost is None:
        return []
    budget = budget_for_event(manager, event)
    total_cost = (
        float(budget.usage.get("usd", 0.0)) if budget is not None else turn_cost
    )
    return [
        manager._sse_projector.cost_update(
            conversation_id,
            event,
            turn_cost=turn_cost,
            total_cost=total_cost,
        )
    ]


def budget_for_event(manager: ConversationManager, event: RuntimeEvent) -> Budget | None:
    agent_id = event.agent_id or ""
    if not agent_id:
        return None
    conversation_id = manager._agent_to_conversation.get(agent_id)
    if conversation_id is None:
        return None
    runtime = manager._runtimes.get(conversation_id)
    if runtime is None:
        return None
    return runtime.budget_for_agent(agent_id)


async def handle_llm_finished(
    manager: ConversationManager,
    conversation_id: str,
    event: RuntimeEvent,
) -> None:
    finished = LLMFinishedData.from_event(event)
    message = finished.message
    if isinstance(message, yuullm.Message):
        await manager.store.append_history_item(conversation_id, message)
        await set_title_from_first_turn(manager, conversation_id)


async def set_title_from_first_turn(
    manager: ConversationManager,
    conversation_id: str,
) -> None:
    rows = await manager.store.list_history_items(conversation_id)
    user_message: yuullm.Message | None = None
    assistant_message: yuullm.Message | None = None
    for row in rows:
        try:
            decoded = decode_prompt_item(row.item_kind, row.item_json)
        except ValueError:
            continue
        if not isinstance(decoded, yuullm.Message):
            continue
        if decoded.role == "user" and user_message is None:
            user_message = decoded
            continue
        if decoded.role == "assistant" and assistant_message is None:
            assistant_message = decoded
    if user_message is None or assistant_message is None:
        return
    title = _conversation_title_from_first_turn(user_message, assistant_message)
    await manager.store.update_title_if_empty(conversation_id, title)


async def handle_tool_result(
    manager: ConversationManager,
    conversation_id: str,
    event: RuntimeEvent,
) -> list[ConversationFrontendEvent]:
    data = event.data
    tool_call_id = str(data.get("tool_call_id") or "")
    result = render_tool_output_final_text(str(data.get("result") or ""))
    tool_name = str(data.get("tool_name") or "")

    await manager.store.append_history_item(
        conversation_id,
        yuullm.tool(tool_call_id, result),
    )

    missing = manager._sse_projector.missing_tool_result_delta(
        conversation_id,
        event,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        text=result,
    )
    return [] if missing is None else [missing]
