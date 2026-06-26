"""Tool compilation system: ToolSelection → ToolBinding (§3.6).

The compiler is the single entry point that converts a CapabilitySet's
``ToolSelection`` list into typed runtime ``ToolBinding`` objects. It calls
``ToolFactory.derive`` for each selection and performs *no* implicit tool
injection — the ``tools`` tuple on a ``CapabilitySetRecord`` is the complete
tool set. Tools that must be present (bash / read / edit / write /
execute_python / restart_kernel) are pre-filled into preset CapabilitySets
by the admin UI / builtin presets, not injected here.
"""

from __future__ import annotations

from dataclasses import dataclass

import msgspec

from yuubot.core.facade import ActorFacadeBinding
from yuubot.core.tools import ToolRegistry
from yuubot.core.tools.contracts import ToolFactory
from yuubot.resources.records import ToolSelection


@dataclass
class ToolDeriveContext:
    """Assembly-time runtime state collected by the compiler.

    All fields are prepared by the compiler's caller (assembly) and consumed
    by ``ToolFactory.derive``. Identity fields mirror ``ActorFacadeBinding``
    when a facade is bound so that context-driven derivation reproduces the
    facade's identity exactly.
    """

    workspace_path: str
    venv_python: str
    facade: ActorFacadeBinding | None
    actor_id: str
    agent_name: str
    session_id: str
    mailbox_id: str


@dataclass
class ToolBinding:
    """Runtime view: a compiled tool instance config."""

    tool_name: str
    config: msgspec.Struct  # instance of factory.config_schema


def compile_tool_bindings(
    selections: list[ToolSelection],
    context: ToolDeriveContext,
    registry: ToolRegistry,
) -> list[ToolBinding]:
    """Pure 1:1 compiler: each ToolSelection → one ToolBinding.

    No implicit injection. Only the tools explicitly listed in
    ``CapabilitySet.tools`` are compiled.
    """
    bindings: list[ToolBinding] = []
    for selection in selections:
        factory: ToolFactory = registry.get(selection.tool_name)
        config = factory.derive(selection.user_fields, context)
        bindings.append(ToolBinding(tool_name=selection.tool_name, config=config))
    return bindings
