"""MCP capability facade for ``execute_python``.

Call ``await search(query="", kind="", server="")`` to discover enabled
servers. Results contain ``server_id``, ``kind`` (``tool``, ``resource``, or
``prompt``), ``name``, ``description``, and ``uri`` but no argument schema.
For a server, use ``client = get_client(server_id)``; then call
``await client.list_tools()`` / ``list_resources()`` / ``list_prompts()`` for
filtered listings. Before invoking a tool, fetch its schema with
``await client.get_spec(name)`` and pass the schema's arguments as keyword
arguments to ``await client.invoke(name, **kwargs)``. Read a resource with
``await client.read_resource(uri)``. Credentials and authentication are
managed by the daemon; do not request or invent credentials in Python.
"""

from __future__ import annotations

from typing import Literal

import msgspec

from yb._daemon import daemon_url, request_json

McpKind = Literal["tool", "resource", "prompt"]


class McpSearchResult(msgspec.Struct, frozen=True):
    server_id: str = ""
    kind: McpKind = "tool"
    name: str = ""
    description: str = ""
    uri: str = ""


class McpResult(msgspec.Struct, frozen=True):
    server_id: str = ""
    name: str = ""
    content: object = None
    raw: dict[str, object] = msgspec.field(default_factory=dict)


class _McpSearchResponse(msgspec.Struct, frozen=True):
    items: list[McpSearchResult] = msgspec.field(default_factory=list)


class _McpSpecResponse(msgspec.Struct, frozen=True):
    spec: str = ""


class McpClient:
    def __init__(self, server_id: str, base_url: str | None = None) -> None:
        self.server_id = server_id
        self._base_url = (base_url or daemon_url()).rstrip("/")

    async def list_tools(self) -> list[McpSearchResult]:
        return await _list_capabilities(self._base_url, self.server_id, "tool")

    async def list_resources(self) -> list[McpSearchResult]:
        return await _list_capabilities(self._base_url, self.server_id, "resource")

    async def list_prompts(self) -> list[McpSearchResult]:
        return await _list_capabilities(self._base_url, self.server_id, "prompt")

    async def get_spec(self, name: str) -> str:
        payload = await request_json("GET", f"{self._base_url}/api/mcps/{self.server_id}/spec/{name}")
        return msgspec.convert(payload, _McpSpecResponse).spec

    async def invoke(self, name: str, **kwargs: object) -> McpResult:
        payload = await request_json("POST", f"{self._base_url}/api/mcps/{self.server_id}/invoke/{name}", json=kwargs)
        return msgspec.convert(payload, McpResult)

    async def read_resource(self, uri: str) -> McpResult:
        payload = await request_json(
            "POST",
            f"{self._base_url}/api/mcps/{self.server_id}/resources/read",
            json={"uri": uri},
        )
        return msgspec.convert(payload, McpResult)


async def search(query: str = "", kind: str = "", server: str = "") -> list[McpSearchResult]:
    payload = await request_json(
        "GET",
        f"{daemon_url()}/api/mcps/search",
        params={"query": query, "kind": kind, "server": server},
    )
    return msgspec.convert(payload, _McpSearchResponse).items


def get_client(server_id: str) -> McpClient:
    return McpClient(server_id)


async def _list_capabilities(base_url: str, server_id: str, kind: str) -> list[McpSearchResult]:
    payload = await request_json("GET", f"{base_url}/api/mcps/search", params={"server": server_id, "kind": kind})
    return msgspec.convert(payload, _McpSearchResponse).items
