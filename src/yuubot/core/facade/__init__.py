"""Agent-visible integration facade.

Submodules:
- bridge: RPC bridge and background task protocol
- codegen: package code generation
- workspace: workspace and actor binding management
- client: generated client template
"""

from yuubot.core.facade.bridge import (
    FacadeRpcRequest as FacadeRpcRequest,
    IntegrationInvokeBridge as IntegrationInvokeBridge,
    YextBackgroundTaskEnded as YextBackgroundTaskEnded,
    YextBackgroundTaskStarted as YextBackgroundTaskStarted,
)
from yuubot.core.facade.client import (
    YEXT_CONTEXT_MODULE as YEXT_CONTEXT_MODULE,
    render_client_module as _render_client_module,  # noqa: F401 — used by tests
)
from yuubot.core.facade.codegen import (
    YEXT_PACKAGE as YEXT_PACKAGE,
    clear_facade_module_cache as clear_facade_module_cache,
    facade_call_path as facade_call_path,
    write_facade_package as write_facade_package,
)
from yuubot.core.facade.workspace import (
    ActorFacadeBinding as ActorFacadeBinding,
    FacadeEndpoint as FacadeEndpoint,
    FacadeWorkspace as FacadeWorkspace,
)
