"""Integration facade RPC bridge and background task protocol."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import msgspec
import yuullm
from yuuagents.core.mailbox import BackgroundCompletedMessage, MailBox, MailMessage

from yuubot.core.capabilities import struct_to_dict
from yuubot.core.facade.protocol import (
    DelegateSubmitPayload,
    FacadeRpcRequest,
    FacadeRpcResponse,
    ImResponsePayload,
    RpcError,
)
from yuubot.core.integrations.context import InvocationContext
from yuubot.core.integrations.core import IntegrationCore

if TYPE_CHECKING:
    from yuubot.core.facade.workspace import FacadeEndpoint


class FacadeBackgroundTaskStarted(MailMessage, msgspec.Struct):
    task_id: str
    actor_id: str
    agent_name: str
    session_id: str
    mailbox_id: str
    summary: str = ""


class FacadeBackgroundTaskEnded(MailMessage, msgspec.Struct):
    task_id: str
    actor_id: str
    agent_name: str
    session_id: str
    mailbox_id: str
    status: str
    summary: str = ""


class FacadeImResponse(MailMessage, msgspec.Struct):
    actor_id: str
    agent_name: str
    session_id: str
    mailbox_id: str
    target_msg_id: str = ""
    text: str = ""
    react: str = ""


class FacadeDelegateTask(MailMessage, msgspec.Struct):
    task_id: str
    actor_id: str
    agent_name: str
    session_id: str
    mailbox_id: str
    prompt: str
    delegate_name: str = ""


@dataclass
class IntegrationInvokeBridge:
    """Local daemon-owned RPC bridge used by generated yext modules."""

    integrations: IntegrationCore
    mailbox_for_actor: Callable[[str], MailBox | None] | None = None
    schedule_for_actor: (
        Callable[[str, str, str, dict[str, object]], Awaitable[object]] | None
    ) = None
    host: str = "127.0.0.1"
    _token: str = ""
    _server: asyncio.Server | None = None

    async def start(self) -> None:
        if self._server is not None:
            return
        self._token = secrets.token_urlsafe(24)
        self._server = await asyncio.start_server(self._handle_client, self.host, 0)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    @property
    def endpoint(self) -> FacadeEndpoint:
        from yuubot.core.facade.workspace import FacadeEndpoint

        if self._server is None or not self._server.sockets:
            raise RuntimeError("integration invoke bridge is not started")
        port = cast(int, self._server.sockets[0].getsockname()[1])
        return FacadeEndpoint(host=self.host, port=port, token=self._token)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw = await reader.readline()
            response = await self._dispatch(raw)
        except Exception as exc:
            response = _error_response(exc)
        writer.write(msgspec.json.encode(response) + b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def _dispatch(self, raw: bytes) -> FacadeRpcResponse:
        try:
            request = msgspec.json.decode(raw, type=FacadeRpcRequest)
        except msgspec.ValidationError as exc:
            raise TypeError(f"facade request decode failed: {exc}") from None
        if request.token != self._token:
            raise PermissionError("invalid facade bridge token")
        if request.kind == "background_started":
            return await self._background_started(request)
        if request.kind == "background_finished":
            return await self._background_finished(request)
        if request.kind == "im_response":
            return await self._im_response(request)
        if request.kind == "delegate_submit":
            return await self._delegate_submit(request)
        if request.kind == "schedule":
            return await self._schedule(request)
        if request.kind != "invoke":
            raise ValueError(f"unknown facade request kind: {request.kind}")

        output = await self.integrations.invoke(
            actor_id=request.actor_id,
            capability_id=request.capability_id,
            payload=request.payload,
            context=InvocationContext(actor_id=request.actor_id),
        )
        return FacadeRpcResponse(ok=True, result=struct_to_dict(output, omit_defaults=True))

    async def _background_started(
        self,
        request: FacadeRpcRequest,
    ) -> FacadeRpcResponse:
        mailbox = self._mailbox(request.actor_id)
        if mailbox is not None:
            await mailbox.send(
                FacadeBackgroundTaskStarted(
                    task_id=request.task_id,
                    actor_id=request.actor_id,
                    agent_name=request.agent_name,
                    session_id=request.session_id,
                    mailbox_id=request.mailbox_id,
                    summary=request.summary,
                )
            )
        return FacadeRpcResponse(ok=True)

    async def _background_finished(
        self,
        request: FacadeRpcRequest,
    ) -> FacadeRpcResponse:
        mailbox = self._mailbox(request.actor_id)
        if mailbox is not None:
            await mailbox.send(
                FacadeBackgroundTaskEnded(
                    task_id=request.task_id,
                    actor_id=request.actor_id,
                    agent_name=request.agent_name,
                    session_id=request.session_id,
                    mailbox_id=request.mailbox_id,
                    status=request.status,
                    summary=request.summary,
                )
            )
            await mailbox.send(
                BackgroundCompletedMessage(
                    task_id=request.task_id,
                    agent_name=request.agent_name,
                    actor_id=request.actor_id,
                    session_id=request.session_id,
                    content=yuullm.user(_background_completion_text(request)),
                )
            )
        return FacadeRpcResponse(ok=True)

    async def _im_response(self, request: FacadeRpcRequest) -> FacadeRpcResponse:
        payload = msgspec.convert(request.payload, type=ImResponsePayload, strict=False)
        mailbox = self._mailbox(request.actor_id)
        if mailbox is not None:
            await mailbox.send(
                FacadeImResponse(
                    actor_id=request.actor_id,
                    agent_name=request.agent_name,
                    session_id=request.session_id,
                    mailbox_id=request.mailbox_id,
                    target_msg_id=payload.msg_id,
                    text=payload.text,
                    react=payload.react,
                )
            )
        return FacadeRpcResponse(ok=True)

    async def _delegate_submit(self, request: FacadeRpcRequest) -> FacadeRpcResponse:
        payload = msgspec.convert(request.payload, type=DelegateSubmitPayload, strict=False)
        if not payload.prompt.strip():
            raise ValueError("delegate prompt is required")
        task_id = request.task_id or secrets.token_hex(8)
        mailbox = self._mailbox(request.actor_id)
        if mailbox is not None:
            await mailbox.send(
                FacadeDelegateTask(
                    task_id=task_id,
                    actor_id=request.actor_id,
                    agent_name=request.agent_name,
                    session_id=request.session_id,
                    mailbox_id=request.mailbox_id,
                    prompt=payload.prompt,
                    delegate_name=payload.delegate_name,
                )
            )
        return FacadeRpcResponse(ok=True, result={"task_id": task_id})

    async def _schedule(self, request: FacadeRpcRequest) -> FacadeRpcResponse:
        if self.schedule_for_actor is None:
            raise RuntimeError("schedule capabilities are not available")
        result = await self.schedule_for_actor(
            request.actor_id,
            request.agent_name,
            request.capability_id,
            request.payload,
        )
        return FacadeRpcResponse(ok=True, result={"output": result})

    def _mailbox(self, actor_id: str) -> MailBox | None:
        if self.mailbox_for_actor is None:
            return None
        return self.mailbox_for_actor(actor_id)


def _error_response(exc: Exception) -> FacadeRpcResponse:
    return FacadeRpcResponse(
        ok=False,
        error=RpcError(type=type(exc).__name__, message=str(exc)),
    )


def _background_completion_text(request: FacadeRpcRequest) -> str:
    inspect_hint = f"Inspect it with TASKS[{request.task_id!r}]."
    if request.status == "ok":
        if request.summary:
            return (
                f"Background task {request.task_id} completed:\n"
                f"{request.summary}\n\n{inspect_hint}"
            )
        return f"Background task {request.task_id} completed. {inspect_hint}"
    if request.summary:
        return (
            f"Background task {request.task_id} finished with status "
            f"{request.status}:\n{request.summary}\n\n{inspect_hint}"
        )
    return (
        f"Background task {request.task_id} finished with status "
        f"{request.status}. {inspect_hint}"
    )
