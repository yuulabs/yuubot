"""Tool execution helpers for the yuuagents actor runtime.

These functions are intentionally module-level: they receive every runtime
object they touch (agent, stage, budgets) so the orchestrator stays explicit
about side effects.
"""

from __future__ import annotations

from typing import cast as typecast

import yuullm
from yuuagents import (
    Budget,
    Owner,
    OwnerType,
    Stage,
    ToolContext,
)
from yuuagents.agent import Agent
from yuuagents.tool_primitives import Task as YuuTask


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


def _render_task_result(task: YuuTask) -> str:
    """Render a completed tool Task's result as a text string."""
    if task.result is not None:
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
    budgets: dict[str, Budget],
) -> None:
    """Submit tool calls to the new Runtime, falling back to the old Runtime.

    Unknown tools raise ``KeyError`` from the new Runtime; in that case the
    legacy ``stage.runtime.submit()`` path is used. Results are appended to
    ``agent`` as tool messages.
    """
    new_tasks: list[tuple[yuullm.ToolCall, YuuTask]] = []

    for tc in tools:
        context = ToolContext(
            agent_id=agent.id,
            tool_call_id=tc.id,
            eventbus=stage.eventbus,
            entity_log=agent.log,
        )
        try:
            yt = await stage.new_runtime.submit_tool_call(
                Owner(type=OwnerType.AGENT, id=agent.id),
                tc,
                context,
            )
            new_tasks.append((tc, yt))
        except KeyError:
            budget = budgets.get(agent.id) or Budget()
            mt = stage.runtime.submit(agent.id, tc, budget)
            r = await mt.wait()
            agent.append(yuullm.tool(tc.id, str(r)))

    for tc, yt in new_tasks:
        ct = await stage.new_runtime.wait_task(yt.id)
        rt = _render_task_result(ct)
        agent.append(yuullm.tool(tc.id, rt))
