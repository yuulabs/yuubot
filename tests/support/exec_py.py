"""Module-scoped execute_python actor reuse for E2E tests."""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from yuubot import Yuubot
from yuubot.llm import StreamClient

from .api import (
    _try_http_json,
    base_url,
    enable_actor,
    http_json,
    put_actor,
)
from .workspaces import reset_workspace_files, workspace_shard


def _module_prefix(module_name: str) -> str:
    stem = re.sub(r"[^a-zA-Z0-9]+", "-", module_name).strip("-").lower()[:48]
    return f"{stem}-{uuid.uuid4().hex[:8]}"


class ExecPyModuleContext:
    def __init__(self, server: object, workspaces: tuple[Path, Path], module_name: str) -> None:
        self.server = server
        self.prefix = _module_prefix(module_name)
        shard = workspace_shard(workspaces, module_name)
        self.workspace = workspaces[shard]
        self.actor_id = f"{self.prefix}-actor"
        self.provider_id = f"{self.prefix}-provider"
        self._http = httpx.AsyncClient(timeout=30.0)
        self._integrations: set[str] = set()

    def name(self, suffix: str) -> str:
        return f"{self.prefix}-{suffix}"

    def conversation_id(self, suffix: str) -> str:
        return self.name(suffix)

    def set_provider(self, provider: StreamClient, model: str = "fake") -> None:
        app = getattr(self.server, "app", None)
        assert isinstance(app, Yuubot)
        app.gateway_client = provider

    async def reset_state(self) -> None:
        reset_workspace_files(self.workspace)
        await self.reset_integrations()

    async def activate(self, provider: StreamClient, model: str = "fake") -> None:
        self.set_provider(provider, model)
        await _try_http_json(
            "POST",
            f"{base_url(self.server)}/api/actors/{self.actor_id}/disable",
            {},
            self._http,
        )
        await enable_actor(self.server, self.actor_id, client=self._http)

    async def put_integration(self, integration_type: str, name: str, config: dict[str, object]) -> None:
        self._integrations.add(integration_type)
        await http_json(
            "PUT",
            f"{base_url(self.server)}/api/integrations/{integration_type}/config",
            {"name": name, "config": config},
            client=self._http,
        )

    async def enable_integration(self, integration_type: str) -> None:
        self._integrations.add(integration_type)
        await http_json(
            "POST",
            f"{base_url(self.server)}/api/integrations/{integration_type}/enable",
            {},
            client=self._http,
        )

    async def reset_integrations(self) -> None:
        app = getattr(self.server, "app", None)
        if not isinstance(app, Yuubot):
            self._integrations.clear()
            return
        for integration_type in list(self._integrations):
            record = app.integration_records.pop(integration_type, None)
            if record is not None:
                await app.runtime.disable_integration(record.name)
            await app.runtime.db.execute("delete from app_integrations where type = ?", (integration_type,))
        if self._integrations:
            await app.runtime.db.commit()
        self._integrations.clear()

    async def setup(self) -> None:
        await put_actor(
            self.server,
            self.actor_id,
            workspace=self.workspace,
            client=self._http,
        )

    async def cleanup(self) -> None:
        url = base_url(self.server)
        await _try_http_json("POST", f"{url}/api/actors/{self.actor_id}/disable", {}, self._http)
        await _try_http_json("DELETE", f"{url}/api/actors/{self.actor_id}", client=self._http)
        await self.reset_integrations()
        reset_workspace_files(self.workspace)
        await self._http.aclose()


@pytest.fixture(scope="module")
async def exec_py_context(
    shared_server: object,
    prepared_workspaces: tuple[Path, Path],
    request: pytest.FixtureRequest,
) -> AsyncIterator[ExecPyModuleContext]:
    module_name = request.node.module.__name__
    context = ExecPyModuleContext(shared_server, prepared_workspaces, module_name)
    await context.setup()
    try:
        yield context
    finally:
        await context.cleanup()
