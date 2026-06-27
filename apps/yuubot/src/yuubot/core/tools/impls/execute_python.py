"""ExecutePython tool factory — registers the ``execute_python`` tool type.

This factory wraps the ``ExecutePythonTool`` yuuagents ``Tool`` subclass
(defined in ``core/assembly/_python_tool.py``) and registers it with
yuubot's ``ToolRegistry`` at import time.

It also owns the system-layer derivation (``derive``) of the full
``PythonRuntime`` config from the assembly ``ToolDeriveContext`` + the actor
facade binding (§6.6). The derivation logic previously lived in the retired
``core/assembly/_tools.py`` private helpers (``_python_tool_runtime``,
``_facade_imports``, ``_facade_expand_functions``).
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
        (yext.*) derived from ``facade.integration_surfaces``, ``state`` ←
        context.identity, ``expand_functions`` ← system facade + per-module
        ``.*`` for each surfaced import path.
        """
        facade = context.facade
        startup_code = _build_startup_code(facade)
        import_modules = _visible_sdk_modules(facade)
        imports = (
            *_FACADE_IMPORTS,
            *(PythonImport(module=module) for module in sorted(import_modules)),
        )
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
            expand_functions=_build_expand_functions(import_modules),
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


def _visible_sdk_modules(
    facade: ActorFacadeBinding | None,
) -> set[str]:
    """``yext.*`` modules surfaced by the facade's visible integration SDKs.

    Each ``VisibleIntegrationSurface`` declares its callable SDK via
    ``sdk.import_paths`` (e.g. ``("yext.github",)``); the union over all
    selected + running surfaces is exactly the set of integration facade
    modules the kernel may import. Integrations without a callable facade
    (e.g. inbound-only IM kinds) declare an empty ``import_paths`` tuple and
    therefore contribute nothing — the actor never sees a SDK it cannot use.
    """
    modules: set[str] = set()
    if facade is None:
        return modules
    for surface in facade.integration_surfaces:
        modules.update(surface.sdk.import_paths)
    return modules


def _build_expand_functions(import_modules: set[str]) -> tuple[str, ...]:
    """Default expansion globs for the system facade + integration modules."""
    return (
        *_FACADE_EXPAND_FUNCTIONS,
        *(f"{module}.*" for module in sorted(import_modules)),
    )
