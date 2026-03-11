"""Addon tools — execute_addon_cli and read_addon_doc for yuuagents.

These are yuutools Tool objects that get registered into the ToolManager
alongside builtin tools. They use dependency injection to access the
AddonContext from the AgentContext.
"""

from __future__ import annotations

import yuutools as yt

from loguru import logger


@yt.tool(
    params={
        "command": (
            "Addon command in CLI format: 'addon_name subcommand [--flags ...] [-- json_data]'. "
            "Use -- to separate structured JSON data from CLI arguments. "
            "Example: 'im send --ctx 5 -- [{\"type\":\"text\",\"text\":\"hello\"}]'"
        ),
    },
    description=(
        "Execute a built-in addon command. Addons are yuubot's in-process capabilities "
        "(im, mem, web, img, schedule, hhsh). Returns multimodal content (text, images, etc.)."
    ),
)
async def execute_addon_cli(
    command: str,
    addon_context=yt.depends(lambda ctx: ctx.addon_context),
) -> str | list[dict]:
    """Execute an addon command and return the result."""
    from yuubot.addons import execute

    try:
        result = await execute(command, context=addon_context)
    except (ValueError, KeyError) as e:
        return f"[ERROR] {e}"
    except Exception:
        logger.opt(exception=True).error("addon execution failed: {}", command)
        return "[ERROR] addon execution failed unexpectedly"

    # If single text block, return as string for simpler display
    if (
        len(result) == 1
        and isinstance(result[0], dict)
        and result[0].get("type") == "text"
    ):
        return result[0]["text"]

    # Multimodal: return as list of content blocks
    return result


@yt.tool(
    params={
        "name": "Addon name to read documentation for (e.g. 'mem', 'web', 'img').",
    },
    description=(
        "Read the full documentation for an addon. "
        "Call this before using an addon for the first time to understand its commands and parameters."
    ),
)
async def read_addon_doc(name: str) -> str:
    """Read addon documentation."""
    from yuubot.addons import load_addon_doc

    try:
        return load_addon_doc(name)
    except FileNotFoundError:
        return f"[ERROR] no documentation for addon {name!r}"
