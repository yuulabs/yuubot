"""Capability runtime — unified entry point for CLI-style calls."""

from __future__ import annotations

from yuubot.capabilities import CapabilityContext, ContentBlock, execute


async def call_cap_cli(
    command: str,
    *,
    context: CapabilityContext | None = None,
) -> list[ContentBlock]:
    """Unified entry point for capability CLI calls.

    Thin wrapper around execute() for use by tools and other callers.
    """
    return await execute(command, context=context)
