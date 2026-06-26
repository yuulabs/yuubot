"""ExecutePython tool factory — registers the ``execute_python`` tool type.

This factory wraps the ``ExecutePythonTool`` yuuagents ``Tool`` subclass
(defined in ``core/assembly/_python_tool.py``) and registers it with
yuubot's ``ToolRegistry`` at import time.

It also owns the system-layer derivation (``derive``) of the full
``PythonRuntime`` config from the assembly ``ToolDeriveContext`` + the actor
facade binding (§6.6). The derivation logic previously lived in the retired
``core/assembly/_tools.py`` private helpers (``_python_tool_runtime``,
``_facade_imports``, ``_facade_expand_functions``,
``_handwritten_external_modules``, ``_python_session_state``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import msgspec
from yuuagents import PythonImport, PythonKernelConfig
from yuuagents.python.runtime import PythonRuntime

from yuubot.core.assembly._constants import PYTHON_PROVIDER_KEY
from yuubot.core.assembly._python_tool import ExecutePythonTool
from yuubot.core.facade import ActorFacadeBinding
from yuubot.core.tools.contracts import EmptyFrontendFields

if TYPE_CHECKING:
    from yuuagents.tool.primitives import Tool
    from yuubot.core.assembly._compiler import ToolDeriveContext


# System facade imports always present in the agent kernel (§6.6 imports).
_FACADE_IMPORTS: tuple[PythonImport, ...] = (
    PythonImport(module="yb"),
    PythonImport(module="yb.actor"),
    PythonImport(module="yb.delegate"),
    PythonImport(module="yb.schedule"),
    PythonImport(module="yb.tasks"),
    PythonImport(module="tim"),
)

# Default function-doc expansion globs for the system facade modules.
_FACADE_EXPAND_FUNCTIONS: tuple[str, ...] = (
    "yb.*",
    "yb.actor.*",
    "yb.delegate.*",
    "yb.schedule.*",
    "yb.tasks.*",
    "tim.*",
)

# Data-analysis aliases pre-imported into the kernel so the agent can use
# ``pd``/``np``/``plt`` directly and matplotlib defaults to the headless
# Agg backend (no inline auto-display of figures).
_PRELOADED_DATA_ALIASES = (
    "import matplotlib\n"
    'matplotlib.use("Agg")\n'
    "import pandas as pd\n"
    "import numpy as np\n"
    "import matplotlib.pyplot as plt\n"
)


class ExecutePythonToolFactory:
    """ToolFactory for the built-in Python execution tool."""

    @property
    def name(self) -> str:
        return PYTHON_PROVIDER_KEY

    @property
    def description(self) -> str:
        return (
            "Execute Python code in an ipykernel session with access to "
            "the agent's facade (yb, yext, tim modules). Supports stdout, "
            "stderr capture, and rich output display."
        )

    @property
    def config_schema(self) -> type[PythonRuntime]:
        return PythonRuntime

    @property
    def user_fields_type(self) -> type[msgspec.Struct]:
        return EmptyFrontendFields

    def derive(
        self,
        user_fields: dict[str, object],
        context: "ToolDeriveContext",
    ) -> PythonRuntime:
        """Derive the full ``PythonRuntime`` from context + facade (§6.6).

        ``config.python`` ← context.venv_python, ``config.cwd`` ←
        context.workspace_path, ``config.sys_path`` / ``config.startup_code``
        ← facade, ``imports`` ← system facade + visible integration SDK
        (yext.*), ``state`` ← context.identity, ``expand_functions`` ←
        system facade + per-module ``.*``.
        """
        facade = context.facade
        startup_code = _build_startup_code(facade)
        imports = _build_imports(facade)
        state = {
            "actor_id": context.actor_id,
            "agent_name": context.agent_name,
            "session_id": context.session_id,
            "mailbox_id": context.mailbox_id,
        }
        return PythonRuntime(
            config=PythonKernelConfig(
                python=context.venv_python or None,
                cwd=context.workspace_path or None,
                sys_path=tuple(facade.sys_path) if facade is not None else (),
                startup_code=startup_code,
            ),
            imports=imports,
            state=state,
            expand_functions=_build_expand_functions(facade),
        )

    def tool_class(self) -> type[Tool[Any, Any]]:
        return ExecutePythonTool


def _build_startup_code(facade: ActorFacadeBinding | None) -> str:
    """Facade startup_code + preloaded data-analysis aliases (§6.6)."""
    code = facade.startup_code if facade is not None else ""
    if code and not code.endswith("\n"):
        code += "\n"
    code += _PRELOADED_DATA_ALIASES
    return code


def _build_imports(facade: ActorFacadeBinding | None) -> tuple[PythonImport, ...]:
    """System facade (yb/tim) + integration facade (yext.*) modules.

    ``yext.*`` modules are derived from the facade's visible capabilities:
    any ``github.*`` capability exposes the hand-written ``yext.github``
    facade module (preserving the previous ``_handwritten_external_modules``
    behaviour). T5 replaces this with ``VisibleIntegrationSurface.sdk``.
    """
    modules = _handwritten_external_modules(facade)
    return (
        *_FACADE_IMPORTS,
        *(PythonImport(module=module) for module in sorted(modules)),
    )


def _build_expand_functions(
    facade: ActorFacadeBinding | None,
) -> tuple[str, ...]:
    """Default expansion globs for the system facade + integration modules."""
    modules = _handwritten_external_modules(facade)
    return (
        *_FACADE_EXPAND_FUNCTIONS,
        *(f"{module}.*" for module in sorted(modules)),
    )


def _handwritten_external_modules(
    facade: ActorFacadeBinding | None,
) -> set[str]:
    """yext modules surfaced by the facade's visible capabilities.

    Today only the GitHub facade is hand-written: any ``github.*``
    capability on the facade exposes ``yext.github``.
    """
    modules: set[str] = set()
    if facade is None:
        return modules
    for capability in facade.capabilities:
        if capability.id.startswith("github."):
            modules.add("yext.github")
    return modules
