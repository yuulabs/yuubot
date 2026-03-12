"""Capability tools — cap_call_cli and read_capability_doc for yuuagents.

These are yuutools Tool objects that get registered into the ToolManager
alongside builtin tools. They use dependency injection to access the
CapabilityContext from the AgentContext.
"""

from __future__ import annotations

import yuutools as yt

from loguru import logger


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
async def cap_call_cli(
    command: str,
    addon_context=yt.depends(lambda ctx: ctx.addon_context),
) -> str | list[dict]:
    """Execute a capability command and return the result."""
    from yuubot.capabilities import execute, CapabilityContext

    # Bridge: addon_context may be an AddonContext or CapabilityContext
    if not isinstance(addon_context, CapabilityContext):
        cap_ctx = CapabilityContext(
            config=addon_context.config,
            ctx_id=addon_context.ctx_id,
            user_id=addon_context.user_id,
            user_role=addon_context.user_role,
            agent_name=addon_context.agent_name,
            task_id=addon_context.task_id,
        )
    else:
        cap_ctx = addon_context

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
async def read_capability_doc(name: str) -> str:
    """Read capability documentation."""
    from yuubot.capabilities import load_capability_doc

    try:
        return load_capability_doc(name)
    except FileNotFoundError:
        return f"[ERROR] no documentation for capability {name!r}"
