"""Actor-visible facade support.

Submodules:
- bridge: RPC bridge and background task protocol
- context: actor-local context module rendering
- workspace: workspace and actor binding management
- protocol: shared RPC request/response Structs

The submodule re-exports below are resolved lazily at runtime via PEP 562
(``__getattr__`` / ``__dir__``): importing this package does NOT eagerly import
``bridge``, ``context``, ``protocol`` or ``workspace``. This matters because
``bridge`` pulls in ``yuullm`` (and through it the daemon's full LLM/HTTP/ORM
stack), which the isolated actor kernel — running on its own ``.venv`` with only
``msgspec`` + the data stack — must not depend on. The agent-side facade modules
(``yb``, ``tim``, ``yext``) only ever import direct submodules
(``yuubot.core.facade.protocol``, ``...context``) and never touch this barrel,
so they stay msgspec-only. Daemon-side code that uses the barrel
(``from yuubot.core.facade import IntegrationInvokeBridge``) resolves on first
attribute access, in the daemon process where ``yuullm`` is available.

Static type checkers (ty / pyright) do not follow PEP 562 ``__getattr__``
re-exports, so the names are also aliased under a ``TYPE_CHECKING`` guard —
that block is never executed at runtime (``TYPE_CHECKING`` is ``False``), it
exists solely so checkers resolve each public name to its real type. The set
of names in the ``TYPE_CHECKING`` block, the ``_LAZY_REEXPORTS`` map, and the
``__dir__`` listing must stay in lockstep; the lazy-init regression test pins
the public surface against drift.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Eager imports for static resolution only — never executed at runtime.
    # Keep these names in lockstep with ``_LAZY_REEXPORTS`` below.
    from yuubot.core.facade.bridge import (
        FacadeBackgroundTaskEnded as FacadeBackgroundTaskEnded,
        FacadeBackgroundTaskStarted as FacadeBackgroundTaskStarted,
        FacadeDelegateTask as FacadeDelegateTask,
        IntegrationInvokeBridge as IntegrationInvokeBridge,
    )
    from yuubot.core.facade.context import (
        FACADE_CONTEXT_MODULE as FACADE_CONTEXT_MODULE,
        render_context_module as render_context_module,
    )
    from yuubot.core.facade.protocol import (
        DelegateSubmitPayload as DelegateSubmitPayload,
        FacadeRpcRequest as FacadeRpcRequest,
        FacadeRpcResponse as FacadeRpcResponse,
        ImSendPayload as ImSendPayload,
        RpcError as RpcError,
    )
    from yuubot.core.facade.workspace import (
        ActorFacadeBinding as ActorFacadeBinding,
        FacadeEndpoint as FacadeEndpoint,
        FacadeWorkspace as FacadeWorkspace,
        YEXT_PACKAGE as YEXT_PACKAGE,
    )

# Mapping of each lazily-reexported public name to the submodule that defines
# it. ``importlib.import_module`` caches modules, so repeated attribute access
# after the first is cheap. The set of public names is preserved exactly — a
# name previously re-exported here stays resolvable through the barrel.
_LAZY_REEXPORTS: dict[str, str] = {
    # yuubot.core.facade.bridge (pulls yuullm — daemon-side only in practice)
    "FacadeBackgroundTaskEnded": "yuubot.core.facade.bridge",
    "FacadeBackgroundTaskStarted": "yuubot.core.facade.bridge",
    "FacadeDelegateTask": "yuubot.core.facade.bridge",
    "IntegrationInvokeBridge": "yuubot.core.facade.bridge",
    # yuubot.core.facade.context (msgspec-only)
    "FACADE_CONTEXT_MODULE": "yuubot.core.facade.context",
    "render_context_module": "yuubot.core.facade.context",
    # yuubot.core.facade.protocol (msgspec-only)
    "DelegateSubmitPayload": "yuubot.core.facade.protocol",
    "FacadeRpcRequest": "yuubot.core.facade.protocol",
    "FacadeRpcResponse": "yuubot.core.facade.protocol",
    "ImSendPayload": "yuubot.core.facade.protocol",
    "RpcError": "yuubot.core.facade.protocol",
    # yuubot.core.facade.workspace (stdlib-only at import)
    "ActorFacadeBinding": "yuubot.core.facade.workspace",
    "FacadeEndpoint": "yuubot.core.facade.workspace",
    "FacadeWorkspace": "yuubot.core.facade.workspace",
    "YEXT_PACKAGE": "yuubot.core.facade.workspace",
}

_LAZY_PUBLIC_NAMES = tuple(_LAZY_REEXPORTS) + ("facade_call_path",)


def __getattr__(name: str) -> object:
    """Resolve a lazily-reexported submodule attribute on first access."""
    module_name = _LAZY_REEXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'yuubot.core.facade' has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value  # cache on the module so subsequent lookups skip us
    return value


def __dir__() -> list[str]:
    """Enumerate the barrel's public surface (for ``dir(yuubot.core.facade)``)."""
    return sorted(_LAZY_PUBLIC_NAMES)


def facade_call_path(capability: object) -> str:
    """Return the supported hand-written call path for a capability."""
    capability_id = str(getattr(capability, "id", ""))
    if capability_id == "github.issue.list":
        return "yext.github.repo().issues.list_recent"
    if capability_id == "echo.echo":
        return "yext.echo.echo"
    raise LookupError(f"no hand-written facade call path for {capability_id!r}")
