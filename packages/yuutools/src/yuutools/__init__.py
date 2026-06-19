"""yuutools — explicit, async-first tool framework for LLM agents."""

from yuutools._depends import DependencyMarker, depends
from yuutools._spec import ParamSpec, ToolSpec
from yuutools._tool import BoundTool, Tool, tool
from yuutools._manager import ToolManager

__all__ = [
    "DependencyMarker",
    "depends",
    "ParamSpec",
    "ToolSpec",
    "BoundTool",
    "Tool",
    "tool",
    "ToolManager",
]
