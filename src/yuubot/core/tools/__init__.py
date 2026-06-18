"""Tool type registry — analogous to ``core/integrations``.

Tool factories register at startup; the admin API exposes
available tool kinds for configuration; the assembly layer
resolves ``tool_name`` strings to yuuagents ``Tool`` subclasses.
"""

from yuubot.core.tools.contracts import ToolFactory, ToolKindInfo, tool_kind_info
from yuubot.core.tools.registry import ToolRegistry, default_tool_factories

__all__ = [
    "ToolFactory",
    "ToolKindInfo",
    "ToolRegistry",
    "default_tool_factories",
    "tool_kind_info",
]
