"""Shared facade RPC protocol types — request/response Structs and payload definitions.

Both the daemon (bridge.py) and the actor subprocess (yb/) use these types.
Serialization only happens at the TCP boundary (msgspec.json.encode / decode).
Inside the process boundary, all data flows as typed Structs.
"""

from __future__ import annotations

import msgspec


class FacadeRpcRequest(msgspec.Struct):
    """Wire format for integration facade RPC requests."""

    token: str
    actor_id: str
    integration_id: str = ""
    capability_id: str = ""
    kind: str = "invoke"
    payload: dict[str, object] = msgspec.field(default_factory=dict)
    agent_name: str = ""
    session_id: str = ""
    mailbox_id: str = ""
    task_id: str = ""
    status: str = ""
    summary: str = ""


class RpcError(msgspec.Struct):
    """Structured error payload for facade RPC responses."""

    type: str
    message: str


class FacadeRpcResponse(msgspec.Struct):
    """Wire format for integration facade RPC responses."""

    ok: bool
    result: dict[str, object] = msgspec.field(default_factory=dict)
    error: RpcError | None = None


class ImSendPayload(msgspec.Struct):
    """Payload for the im_send kind — actor sends a message to a channel."""

    path: str = ""
    text: str = ""


class DelegateSubmitPayload(msgspec.Struct):
    """Payload for the delegate_submit kind — actor delegates a task."""

    prompt: str
    delegate_name: str = ""
