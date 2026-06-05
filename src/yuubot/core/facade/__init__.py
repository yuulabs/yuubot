"""Actor-visible facade support.

Submodules:
- bridge: RPC bridge and background task protocol
- codegen: package code generation
- context: actor-local context module rendering
- workspace: workspace and actor binding management
- client: generated client template
"""

from yuubot.core.facade.bridge import (
    FacadeBackgroundTaskEnded as FacadeBackgroundTaskEnded,
    FacadeBackgroundTaskStarted as FacadeBackgroundTaskStarted,
    FacadeDelegateTask as FacadeDelegateTask,
    FacadeImResponse as FacadeImResponse,
    FacadeRpcRequest as FacadeRpcRequest,
    IntegrationInvokeBridge as IntegrationInvokeBridge,
)
from yuubot.core.facade.client import (
    render_client_module as _render_client_module,  # noqa: F401 — used by tests
)
from yuubot.core.facade.codegen import (
    YEXT_PACKAGE as YEXT_PACKAGE,
    clear_facade_module_cache as clear_facade_module_cache,
    facade_call_path as facade_call_path,
    facade_module_name as facade_module_name,
    write_facade_package as write_facade_package,
)
from yuubot.core.facade.context import (
    FACADE_CONTEXT_MODULE as FACADE_CONTEXT_MODULE,
    render_context_module as render_context_module,
)
from yuubot.core.facade.workspace import (
    ActorFacadeBinding as ActorFacadeBinding,
    FacadeEndpoint as FacadeEndpoint,
    FacadeWorkspace as FacadeWorkspace,
)
