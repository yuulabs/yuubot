"""Tool execution helpers for the yuuagents actor runtime.

These functions are intentionally module-level: they receive every runtime
object they touch (agent, stage, budgets) so the orchestrator stays explicit
about side effects.
"""

from __future__ import annotations

from typing import cast as typecast

import yuullm
from yuuagents import (
    Agent,
    Owner,
    OwnerType,
    Stage,
    ToolContext,
)
from yuuagents.core.task import Task as YuuTask
from yuuagents.tool.primitives import ToolResult


def _extract_tool_calls(message: yuullm.Message) -> list[yuullm.ToolCall]:
    """Extract ToolCall structs from an assistant message's content.

    After ``agent.step()`` returns, the assistant message's content list
    contains tool_call items as dicts with ``type``, ``id``, ``name``, and
    ``arguments`` keys. These are converted to ``yuullm.ToolCall`` structs
    for submission to the new Runtime.
    """
    result: list[yuullm.ToolCall] = []
    for item in message.content:
        if isinstance(item, dict) and item.get("type") == "tool_call":
            tc = typecast("dict[str, object]", item)
            result.append(
                yuullm.ToolCall(
                    id=str(tc["id"]),
                    name=str(tc["name"]),
                    arguments=str(tc["arguments"]),
                )
            )
    return result


def _render_task_result(task: YuuTask) -> ToolResult:
    """Render a completed tool Task's result."""
    if task.result is not None:
        if isinstance(task.result, str):
            return task.result
        if isinstance(task.result, list):
            return task.result
        return str(task.result)
    if task.error is not None:
        msg = f"[{task.error.type}] {task.error.message}"
        if task.error.traceback:
            msg += "\n" + "\n".join(task.error.traceback)
        return msg
    return "no result"


async def _execute_tool_calls(
    agent: Agent,
    tools: list[yuullm.ToolCall],
    stage: Stage,
) -> None:
    """Submit tool calls to the new Runtime and append results to agent history."""
    new_tasks: list[tuple[yuullm.ToolCall, YuuTask]] = []

    for tc in tools:
        context = ToolContext(
            agent_id=agent.id,
            tool_call_id=tc.id,
            eventbus=stage.eventbus,
            entity_log=agent.log,
        )
        yt = await stage.runtime.submit_tool_call(
            Owner(type=OwnerType.AGENT, id=agent.id),
            tc,
            context,
        )
        new_tasks.append((tc, yt))

    for tc, yt in new_tasks:
        ct = await stage.runtime.wait_task(yt.id)
        rt = _render_task_result(ct)
        agent.append(yuullm.tool(tc.id, rt))
