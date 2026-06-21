"""Actor-visible facade support.

Submodules:
- bridge: RPC bridge and background task protocol
- context: actor-local context module rendering
- workspace: workspace and actor binding management
- protocol: shared RPC request/response Structs
"""

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


def facade_call_path(capability: object) -> str:
    """Return the supported hand-written call path for a capability."""
    capability_id = str(getattr(capability, "id", ""))
    if capability_id == "github.issue.list":
        return "yext.github.repo().issues.list_recent"
    raise LookupError(f"no hand-written facade call path for {capability_id!r}")
