"""Capability tools — call_cap_cli and read_cap_doc for yuuagents.

These are yuutools Tool objects that get registered into the ToolManager
alongside builtin tools. They use dependency injection to access the
CapabilityContext from the AgentContext.
"""

from __future__ import annotations

import yuutools as yt

from loguru import logger


def _resolve_capability_context(
    *,
    capability_context,
    addon_context,
    agent_context,
):
    from yuubot.capabilities import CapabilityContext
    from yuubot.capabilities.runtime import capability_context_for_agent

    explicit = capability_context or addon_context
    if isinstance(explicit, CapabilityContext):
        return explicit

    agent_id = getattr(agent_context, "agent_id", "")
    resolved = capability_context_for_agent(agent_id) if agent_id else None
    if resolved is None:
        raise RuntimeError("capability context unavailable for this agent")
    return resolved


@yt.tool(
    params={
        "command": (
            "Capability command in CLI format: 'cap_name subcommand [--flags ...] [-- json_data]'. "
            "Use -- to separate structured JSON data from CLI arguments. "
            "Example: 'im send --ctx 5 -- [{\"type\":\"text\",\"text\":\"hello\"}]'"
        ),
    },
    description=(
        "Execute a built-in capability command. Capabilities are yuubot's in-process abilities "
        "(im, mem, web, img, schedule, hhsh). Returns multimodal content (text, images, etc.)."
    ),
)
async def call_cap_cli(
    command: str,
    capability_context=None,
    addon_context=None,
    agent_context=yt.depends(lambda ctx: ctx),
) -> str | list[dict]:
    """Execute a capability command and return the result."""
    from yuubot.capabilities import execute, CapabilityContext

    cap_ctx = _resolve_capability_context(
        capability_context=capability_context,
        addon_context=addon_context,
        agent_context=agent_context,
    )
    if not isinstance(cap_ctx, CapabilityContext):
        raise RuntimeError("invalid capability context")

    try:
        result = await execute(command, context=cap_ctx)
    except (ValueError, KeyError) as e:
        return f"[ERROR] {e}"
    except Exception:
        logger.opt(exception=True).error("capability execution failed: {}", command)
        return "[ERROR] capability execution failed unexpectedly"

    if (
        len(result) == 1
        and isinstance(result[0], dict)
        and result[0].get("type") == "text"
    ):
        return result[0]["text"]

    return result


@yt.tool(
    params={
        "name": "Capability name to read documentation for (e.g. 'mem', 'web', 'img').",
    },
    description=(
        "Read the full documentation for a capability. "
        "Call this before using a capability for the first time to understand its commands and parameters."
    ),
)
async def read_cap_doc(
    name: str,
    capability_context=None,
    addon_context=None,
    agent_context=yt.depends(lambda ctx: ctx),
) -> str:
    """Read capability documentation."""
    from yuubot.capabilities import CapabilityContext, load_capability_doc

    resolved = _resolve_capability_context(
        capability_context=capability_context,
        addon_context=addon_context,
        agent_context=agent_context,
    )
    cap_ctx = resolved if isinstance(resolved, CapabilityContext) else None
    action_filter = None
    if cap_ctx is not None and cap_ctx.action_filters is not None:
        action_filter = cap_ctx.action_filters.get(name)

    try:
        return load_capability_doc(name, action_filter=action_filter)
    except FileNotFoundError:
        return f"[ERROR] no documentation for capability {name!r}"
