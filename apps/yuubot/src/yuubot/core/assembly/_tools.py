"""Assembly tool registry bridge.

The per-tool derivation logic and implicit-injection helpers that previously
lived here were retired (§3.6.1): tool configs are now produced by the
compiler system (``core/assembly/_compiler.py``) calling each
``ToolFactory.derive``, and the standard tool set is pre-filled into preset
CapabilitySets (``resources/builtin_presets.py``). This module keeps only the
cross-module registry handle used by ``_stage._register_tools`` to resolve a
tool's ``Tool`` subclass by name at runtime registration time.
"""

from __future__ import annotations

from yuubot.core.tools import ToolRegistry

_tool_registry: ToolRegistry | None = None


def set_assembly_tool_registry(registry: ToolRegistry) -> None:
    """Set the ToolRegistry used for tool name resolution during assembly.

    Called once at daemon startup. ``_stage._register_tools`` reads this
    module-level handle to resolve ``tool_name`` → ``Tool`` subclass when
    registering tool instances on the yuuagents runtime registry.
    """
    global _tool_registry
    _tool_registry = registry


def get_assembly_tool_registry() -> ToolRegistry | None:
    """Return the currently-configured assembly ToolRegistry (or None)."""
    return _tool_registry
