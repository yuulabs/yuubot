"""HTTP and WebSocket helpers for boundary-only tests."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import cast

import httpx
import websockets
from yuubot import Yuubot
from yuubot.domain import StreamEvent
from yuubot.llm import Provider, ScriptedProvider, scripted_reply
from yuubot.web import make_server

from .workspaces import workspace_shard

JsonObject = dict[str, object]


def _test_prefix(test_name: str) -> str:
    stem = re.sub(r"[^a-zA-Z0-9]+", "-", test_name).strip("-").lower()[:48]
    return f"{stem}-{uuid.uuid4().hex[:8]}"


async def boot_app(data_dir: Path, *, provider: Provider | None = None) -> Yuubot:
    app = await Yuubot.create(data_dir)
    if provider is not None:
        app.provider_instances["fake"] = provider
    return app


@contextlib.asynccontextmanager
async def running_server(app: Yuubot, *, development: bool = False) -> AsyncIterator[object]:
    server = make_server(app, port=0, development=development)
    ready = asyncio.Event()
    serve_task = asyncio.create_task(server.serve())

    async def wait_ready() -> None:
        while not server._server.started:  # noqa: SLF001
            await asyncio.sleep(0)
        ready.set()

    wait_task = asyncio.create_task(wait_ready())
    try:
        await asyncio.wait_for(ready.wait(), timeout=10.0)
        yield server
    finally:
        wait_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await wait_task
        server.shutdown()
        await serve_task


class SharedTestContext:
    def __init__(
        self,
        server: object,
        tmp_path: Path,
        test_name: str,
        workspaces: tuple[Path, Path],
    ) -> None:
        self.server = server
        self.tmp_path = tmp_path
        self.prefix = _test_prefix(test_name)
        self.actor_id = self.name("actor")
        self.provider_id = self.name("provider")
        shard = workspace_shard(workspaces, test_name)
        self.workspace = workspaces[shard]
        self.workspace_alt = workspaces[1 - shard]
        self._http = httpx.AsyncClient(timeout=30.0)
        self._actors: list[str] = []
        self._providers: list[str] = []
        self._routes: list[str] = []
        self._conversations: list[str] = []
        self._integrations: set[str] = set()

    def name(self, suffix: str) -> str:
        return f"{self.prefix}-{suffix}"

    def conversation_id(self, suffix: str = "c1") -> str:
        conversation_id = self.name(suffix)
        self._conversations.append(conversation_id)
        return conversation_id

    def route_id(self, suffix: str) -> str:
        route_id = self.name(suffix)
        self._routes.append(route_id)
        return route_id

    async def put_provider(
        self,
        provider: Provider | None = None,
        *,
        provider_id: str | None = None,
        model: str = "fake",
    ) -> str:
        resolved = provider_id or self.provider_id
        app = getattr(self.server, "app", None)
        if isinstance(app, Yuubot) and provider is not None:
            app.provider_instances[resolved] = provider
        await put_provider(self.server, resolved, model=model, client=self._http)
        if resolved not in self._providers:
            self._providers.append(resolved)
        return resolved

    async def setup_actor(
        self,
        provider: Provider | None = None,
        *,
        actor_id: str | None = None,
        provider_id: str | None = None,
        workspace: Path | None = None,
        enable: bool = True,
        model: str = "fake",
    ) -> str:
        resolved_actor = actor_id or self.actor_id
        resolved_provider = provider_id or self.provider_id
        resolved_workspace = workspace or self.workspace
        await self.put_provider(provider, provider_id=resolved_provider, model=model)
        await put_actor(
            self.server,
            resolved_actor,
            workspace=resolved_workspace,
            provider=resolved_provider,
            model=model,
            client=self._http,
        )
        if resolved_actor not in self._actors:
            self._actors.append(resolved_actor)
        if enable:
            await enable_actor(self.server, resolved_actor, client=self._http)
        return resolved_actor

    async def put_integration(self, integration_type: str, *, name: str, config: dict[str, object]) -> JsonObject:
        self._integrations.add(integration_type)
        return await put_integration(
            self.server,
            integration_type,
            name=name,
            config=config,
            client=self._http,
        )

    async def create_route(self, *, route_id: str, pattern: str, actor_id: str, enabled: bool = True) -> JsonObject:
        if route_id not in self._routes:
            self._routes.append(route_id)
        return await create_route(
            self.server,
            route_id=route_id,
            pattern=pattern,
            actor_id=actor_id,
            enabled=enabled,
            client=self._http,
        )

    async def cleanup(self) -> None:
        url = base_url(self.server)
        if self._routes:
            for route_id in reversed(self._routes):
                await _try_http_json("DELETE", f"{url}/api/routes/{route_id}", client=self._http)
        if self._conversations:
            for conversation_id in reversed(self._conversations):
                await _try_http_json("DELETE", f"{url}/api/conversations/{conversation_id}", client=self._http)
        if self._actors:
            await self._cleanup_shares()
        if self._actors:
            for actor_id in reversed(self._actors):
                await _try_http_json("DELETE", f"{url}/api/actors/{actor_id}", client=self._http)
        if self._providers:
            for provider_id in reversed(self._providers):
                await _try_http_json("DELETE", f"{url}/api/providers/{provider_id}", client=self._http)
                app = getattr(self.server, "app", None)
                if isinstance(app, Yuubot):
                    self._remove_provider_instance(app, provider_id)
        await self._cleanup_integrations()
        await self._http.aclose()

    async def reset_integrations(self) -> None:
        await self._cleanup_integrations()
        self._integrations.clear()

    async def _cleanup_shares(self) -> None:
        try:
            payload = await http_json("GET", f"{base_url(self.server)}/api/shares", client=self._http)
        except AssertionError:
            return
        for grant in cast(list[JsonObject], payload.get("items", [])):
            if grant.get("actor_id") in self._actors:
                share_id = grant.get("id")
                if isinstance(share_id, str):
                    await _try_http_json("DELETE", f"{base_url(self.server)}/api/shares/{share_id}", client=self._http)

    async def _cleanup_integrations(self) -> None:
        app = getattr(self.server, "app", None)
        if not isinstance(app, Yuubot):
            return
        for integration_type in self._integrations:
            record = app.integration_records.pop(integration_type, None)
            if record is not None:
                await app.runtime.disable_integration(record.name)
            await app.runtime.db.execute("delete from app_integrations where type = ?", (integration_type,))
        if self._integrations:
            await app.runtime.db.commit()

    @staticmethod
    def _remove_provider_instance(app: Yuubot, provider_id: str) -> None:
        app.provider_instances.pop(provider_id, None)


async def _try_http_json(
    method: str,
    url: str,
    body: JsonObject | bytes | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> JsonObject:
    try:
        return await http_json(method, url, body, client=client)
    except AssertionError:
        return {}


def base_url(server: object) -> str:
    port = cast(int, getattr(server, "server_port"))
    return f"http://127.0.0.1:{port}"


def ws_url(server: object) -> str:
    port = cast(int, getattr(server, "server_port"))
    return f"ws://127.0.0.1:{port}/api/ws"


async def http_json(
    method: str,
    url: str,
    body: JsonObject | bytes | None = None,
    *,
    content_type: str = "application/json",
    expected_status: int = 200,
    client: httpx.AsyncClient | None = None,
) -> JsonObject:
    headers: dict[str, str] = {}
    content: bytes | None = None
    if isinstance(body, dict):
        content = json.dumps(body).encode()
        headers["content-type"] = content_type
    elif isinstance(body, bytes):
        content = body
        headers["content-type"] = content_type
    if client is None:
        async with httpx.AsyncClient() as ephemeral:
            response = await ephemeral.request(method, url, content=content, headers=headers, timeout=30.0)
    else:
        response = await client.request(method, url, content=content, headers=headers)
    assert response.status_code == expected_status, response.text
    if not response.content:
        return {}
    return cast(JsonObject, response.json())


def multipart_body(boundary: str, filename: str, content_type: str, data: bytes) -> bytes:
    return b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            data,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )


async def put_provider(
    server: object,
    provider_id: str = "fake",
    *,
    model: str = "fake",
    client: httpx.AsyncClient | None = None,
) -> JsonObject:
    app = getattr(server, "app", None)
    injected = app.provider_instances.get(provider_id) if isinstance(app, Yuubot) else None
    if injected is None:
        injected = ScriptedProvider([[StreamEvent(group_id="stop", kind="stream_stop", payload={"reason": "stop"})]])
    result = await http_json(
        "PUT",
        f"{base_url(server)}/api/providers/{provider_id}",
        {
            "name": provider_id.title(),
            "protocol": "openai-compatible",
            "config": {"endpoint": "", "api_key": "test-key", "options": {}},
        },
        client=client,
    )
    await http_json(
        "PUT",
        f"{base_url(server)}/api/providers/{provider_id}/model-cards/{model}",
        {
            "selector": model,
            "toolcall": True,
            "input_price_per_million": 1.0,
        },
        client=client,
    )
    if isinstance(app, Yuubot) and injected is not None:
        app.provider_instances[provider_id] = injected
    return result


async def put_actor(
    server: object,
    actor_id: str,
    *,
    workspace: Path,
    provider: str = "fake",
    model: str = "fake",
    client: httpx.AsyncClient | None = None,
) -> JsonObject:
    return await http_json(
        "PUT",
        f"{base_url(server)}/api/actors/{actor_id}",
        {
            "name": actor_id.title(),
            "workspace": str(workspace),
            "provider": provider,
            "model": {"selector": model},
        },
        client=client,
    )


async def enable_actor(server: object, actor_id: str, *, client: httpx.AsyncClient | None = None) -> JsonObject:
    return await http_json("POST", f"{base_url(server)}/api/actors/{actor_id}/enable", {}, client=client)


async def disable_actor(server: object, actor_id: str, *, client: httpx.AsyncClient | None = None) -> JsonObject:
    return await http_json("POST", f"{base_url(server)}/api/actors/{actor_id}/disable", {}, client=client)


async def put_integration(
    server: object,
    integration_type: str,
    *,
    name: str,
    config: dict[str, object],
    client: httpx.AsyncClient | None = None,
) -> JsonObject:
    return await http_json(
        "PUT",
        f"{base_url(server)}/api/integrations/{integration_type}/config",
        {"name": name, "config": config},
        client=client,
    )


async def enable_integration(
    server: object,
    integration_type: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> JsonObject:
    return await http_json("POST", f"{base_url(server)}/api/integrations/{integration_type}/enable", {}, client=client)


async def create_route(
    server: object,
    *,
    route_id: str,
    pattern: str,
    actor_id: str,
    enabled: bool = True,
    client: httpx.AsyncClient | None = None,
) -> JsonObject:
    return await http_json(
        "POST",
        f"{base_url(server)}/api/routes",
        {"id": route_id, "pattern": pattern, "actor_id": actor_id, "enabled": enabled},
        client=client,
    )


async def bootstrap(server: object) -> JsonObject:
    return await http_json("GET", f"{base_url(server)}/api/bootstrap")


async def conversation_history(server: object, conversation_id: str) -> list[JsonObject]:
    payload = await http_json("GET", f"{base_url(server)}/api/conversations/{conversation_id}/history")
    return cast(list[JsonObject], payload["items"])


async def conversation_summary(server: object, conversation_id: str) -> JsonObject:
    return await http_json("GET", f"{base_url(server)}/api/conversations/{conversation_id}")


async def conversation_costs(server: object, conversation_id: str) -> list[JsonObject]:
    payload = await http_json("GET", f"{base_url(server)}/api/conversations/{conversation_id}/costs")
    return cast(list[JsonObject], payload["items"])


async def post_inbound(
    server: object,
    route: str,
    text: str,
    *,
    conversation_id: str | None = None,
    integration_type: str = "github",
) -> JsonObject:
    await _ensure_webhook_integration(server, integration_type)
    body: JsonObject = {"route": route, "text": text}
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    return await http_json("POST", f"{base_url(server)}/webhooks/app/{integration_type}", body)


async def _ensure_webhook_integration(server: object, integration_type: str) -> None:
    app = getattr(server, "app", None)
    if isinstance(app, Yuubot) and app.integration_enabled(integration_type):
        return
    if integration_type == "github":
        await put_integration(server, integration_type, name="gh", config={"access_token": "test-token"})
    else:
        await put_integration(server, integration_type, name=integration_type, config={"access_token": "test-token"})
    await enable_integration(server, integration_type)


async def recv_ws_frames(
    server: object,
    commands: list[JsonObject],
    *,
    stop_when: Callable[[JsonObject, list[JsonObject]], bool] | None = None,
) -> list[JsonObject]:
    frames: list[JsonObject] = []
    async with websockets.connect(ws_url(server), open_timeout=5) as ws:
        for command in commands:
            await ws.send(json.dumps(command))
        while True:
            frame = cast(JsonObject, json.loads(await asyncio.wait_for(ws.recv(), timeout=30)))
            frames.append(frame)
            if stop_when is not None and stop_when(frame, frames):
                break
            if frame.get("type") == "error":
                raise AssertionError(frame)
    return frames


async def ws_conversation_send(
    server: object,
    *,
    command_id: str,
    actor_id: str,
    conversation_id: str,
    content: list[JsonObject] | str,
    wait_for_stop: bool = True,
) -> list[JsonObject]:
    payload_content: list[JsonObject]
    if isinstance(content, str):
        payload_content = [{"kind": "text", "text": content}]
    else:
        payload_content = content

    def stop(frame: JsonObject, _: list[JsonObject]) -> bool:
        if not wait_for_stop:
            return frame.get("type") == "conversation.send.accepted"
        if frame.get("type") != "conversation.stream":
            return False
        event = cast(JsonObject, frame["payload"])["event"]
        if cast(JsonObject, event)["kind"] != "stream_stop":
            return False
        payload = cast(JsonObject, event).get("payload", {})
        reason = cast(JsonObject, payload).get("reason") if isinstance(payload, dict) else None
        return reason in {"stop", "interrupted"}

    return await recv_ws_frames(
        server,
        [
            {
                "id": command_id,
                "type": "conversation.send",
                "payload": {
                    "actor_id": actor_id,
                    "conversation_id": conversation_id,
                    "content": payload_content,
                },
            }
        ],
        stop_when=stop,
    )


async def setup_amy(
    server: object,
    tmp_path: Path,
    *,
    enable: bool = True,
) -> None:
    workspace = tmp_path / "workspace"
    await put_provider(server)
    await put_actor(server, "amy", workspace=workspace)
    if enable:
        await enable_actor(server, "amy")


async def wait_for_history_kind(
    server: object,
    conversation_id: str,
    kind: str,
    *,
    attempts: int = 200,
) -> list[JsonObject]:
    for _ in range(attempts):
        try:
            history = await conversation_history(server, conversation_id)
        except AssertionError as exc:
            if "conversation not found" not in str(exc):
                raise
            history = []
        if history and history[-1]["kind"] == kind:
            return history
        await asyncio.sleep(0.02)
    raise AssertionError(f"conversation {conversation_id} did not reach history kind {kind!r}")


__all__ = ["boot_app", "put_provider", "scripted_reply"]
