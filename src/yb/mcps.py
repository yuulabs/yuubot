"""MCP data source facade for execute_python.

Use ``await search(query)`` to discover enabled MCP server capabilities. Search
results omit parameter schemas. Before calling a tool, use
``client = get_client(server_id)`` and ``await client.get_spec(name)``, then
``await client.invoke(name, **kwargs)``. Read resources with
``await client.read_resource(uri)``. Credentials are daemon-managed.
"""

from __future__ import annotations

from typing import Literal, cast

from yb._daemon import daemon_url, request_json

McpKind = Literal["tool", "resource", "prompt"]


class McpSearchResult:
    server_id: str
    kind: McpKind
    name: str
    description: str
    uri: str

    def __init__(self, payload: dict[str, object]) -> None:
        self.server_id = str(payload.get("server_id", ""))
        kind = payload.get("kind", "tool")
        if kind in {"tool", "resource", "prompt"}:
            self.kind = cast(McpKind, kind)
        else:
            self.kind = "tool"
        self.name = str(payload.get("name", ""))
        self.description = str(payload.get("description", ""))
        self.uri = str(payload.get("uri", ""))

    def __repr__(self) -> str:
        return f"McpSearchResult(server_id={self.server_id!r}, kind={self.kind!r}, name={self.name!r})"


class McpResult:
    server_id: str
    name: str
    content: object
    raw: dict[str, object]

    def __init__(self, payload: dict[str, object]) -> None:
        self.server_id = str(payload.get("server_id", ""))
        self.name = str(payload.get("name", ""))
        self.content = payload.get("content")
        raw = payload.get("raw")
        self.raw = cast(dict[str, object], raw) if isinstance(raw, dict) else {}


class McpClient:
    def __init__(self, server_id: str, *, base_url: str | None = None) -> None:
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
        spec = payload.get("spec")
        return spec if isinstance(spec, str) else ""

    async def invoke(self, name: str, **kwargs: object) -> McpResult:
        payload = await request_json("POST", f"{self._base_url}/api/mcps/{self.server_id}/invoke/{name}", json=kwargs)
        return McpResult(payload)

    async def read_resource(self, uri: str) -> McpResult:
        payload = await request_json(
            "POST",
            f"{self._base_url}/api/mcps/{self.server_id}/resources/read",
            json={"uri": uri},
        )
        return McpResult(payload)


async def search(query: str = "", *, kind: str = "", server: str = "") -> list[McpSearchResult]:
    payload = await request_json(
        "GET",
        f"{daemon_url()}/api/mcps/search",
        params={"query": query, "kind": kind, "server": server},
    )
    items = payload.get("items", [])
    if not isinstance(items, list):
        return []
    return [McpSearchResult(cast(dict[str, object], item)) for item in items if isinstance(item, dict)]


def get_client(server_id: str) -> McpClient:
    return McpClient(server_id)


async def _list_capabilities(base_url: str, server_id: str, kind: str) -> list[McpSearchResult]:
    payload = await request_json("GET", f"{base_url}/api/mcps/search", params={"server": server_id, "kind": kind})
    items = payload.get("items", [])
    if not isinstance(items, list):
        return []
    return [McpSearchResult(cast(dict[str, object], item)) for item in items if isinstance(item, dict)]
