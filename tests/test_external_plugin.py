"""External integration plugin lifecycle tests."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import httpx
import pytest

from yuubot.bootstrap.config import BootstrapConfig
from yuubot.core.gateway import Gateway
from yuubot.core.integrations import IntegrationFactoryRegistry, default_integration_factories
from yuubot.core.integrations.context import InvocationContext
from yuubot.core.integrations.contracts import LocalIntegrationStorage
from yuubot.core.routing import RouteBindings
from yuubot.resources.records import IntegrationRecord
from yuubot.resources.root import Resources
from yuubot.resources.store.models import IntegrationORM
import yuubot.runtime.admin.app as admin_module
from yuubot.runtime.admin import DaemonClient, build_admin_asgi_app
from yuubot.runtime.plugin_manager import ExternalPluginManager


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


async def test_external_plugin_loader_exposes_manifest_capabilities(tmp_path: Path) -> None:
    source = _write_plugin_source(tmp_path / "source", package_name="demo_plugin")
    manager = ExternalPluginManager(
        plugins_dir=tmp_path / "plugins",
        data_root=tmp_path / "data",
    )

    manifest = await manager.install(source, install_environment=False)
    registry = default_integration_factories()
    registry.register_loader(manager.loader())

    assert manifest.name == "demo"
    demo = registry.get("demo")
    specs = demo.capability_specs()
    assert [spec.id for spec in specs] == ["demo.search"]
    schema_ref = specs[0].input_schema["$ref"].removeprefix("#/$defs/")
    properties = specs[0].input_schema["$defs"][schema_ref]["properties"]
    assert properties["query"]["type"] == "string"


async def test_admin_installs_external_plugin_record(
    tmp_path: Path,
    resources: Resources,
    yuubot_config: BootstrapConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_plugin_source(tmp_path / "source", package_name="demo_plugin")
    manager = ExternalPluginManager(
        plugins_dir=tmp_path / "plugins",
        data_root=tmp_path / "data",
    )
    registry = IntegrationFactoryRegistry()
    registry.register_loader(manager.loader())
    captured: dict[str, object] = {}

    async def fake_request_daemon(
        daemon: DaemonClient,
        path: str,
        *,
        method: str,
        body: bytes = b"",
        content_type: str = "application/json",
    ) -> admin_module.DaemonResponse:
        _ = daemon, content_type
        captured.update({"path": path, "method": method, "body": body})
        payload = json.loads(body.decode())
        record = await resources.repository.insert(
            IntegrationORM,
            IntegrationRecord(
                id=payload["id"],
                name=payload["name"],
                config=payload["config"],
                enabled=payload["enabled"],
            ),
        )
        response_body = json.dumps(
            {"status": "ok", "data": {"id": record.id, "name": record.name}},
            ensure_ascii=True,
        ).encode()
        return admin_module.DaemonResponse(status_code=201, body=response_body)

    monkeypatch.setattr(admin_module, "_request_daemon", fake_request_daemon)
    app = build_admin_asgi_app(
        config=yuubot_config.admin,
        resources=resources,
        daemon=DaemonClient(base_url="http://daemon", daemon_secret="server-only"),
        integration_factories=registry,
        plugin_manager=manager,
    )

    async with _client(app) as client:
        installed = await client.post(
            "/api/plugins/install",
            json={
                "source_path": str(source),
                "install_environment": False,
                "config": {"api_key": "secret"},
            },
        )
        listed = await client.get("/api/plugins")
        kinds = await client.get("/api/integration-kinds")

    assert installed.status_code == 201
    assert installed.json()["plugin"]["name"] == "demo"
    assert installed.json()["warnings"] == []
    assert captured["path"] == "/api/resources/integrations"
    assert captured["method"] == "POST"
    assert listed.json()["plugins"][0]["integration_id"] == "demo"
    assert kinds.json()["kinds"][0]["name"] == "demo"


async def test_external_plugin_facade_invokes_subprocess(tmp_path: Path) -> None:
    source = _write_plugin_source(tmp_path / "source", package_name="demo_plugin")
    manager = ExternalPluginManager(
        plugins_dir=tmp_path / "plugins",
        data_root=tmp_path / "data",
    )
    await manager.install(source, install_environment=False)
    factory = manager.loader().load("demo")
    assert factory is not None

    instance = await factory.create(
        IntegrationRecord(id="demo", name="demo"),
        gateway=Gateway(RouteBindings(rules=[])),
        storage=LocalIntegrationStorage(tmp_path / "data" / "integrations" / "demo"),
    )
    capability = instance.capabilities()[0]
    payload = capability.input_type(query="meeting notes")
    try:
        result = await capability.invoke(payload, InvocationContext(actor_id="actor"))
    finally:
        await instance.close()

    assert result.value == {
        "ok": True,
        "function": "search",
        "payload": {"query": "meeting notes"},
    }


def _write_plugin_source(root: Path, *, package_name: str) -> Path:
    package = root / package_name
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        textwrap.dedent(
            """
            [project]
            name = "demo-yuubot-plugin"
            version = "0.1.0"
            requires-python = ">=3.11"

            [build-system]
            requires = ["hatchling"]
            build-backend = "hatchling.build"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "manifest.yaml").write_text(
        textwrap.dedent(
            f"""
            name: demo
            version: "0.1.0"
            description: Demo external plugin
            entry: {package_name}
            facade:
              namespace: demo
              functions:
                - name: search
                  description: Search demo records
                  params:
                    query:
                      type: str
                      description: Search query
                  returns: dict
            config:
              type: object
              properties:
                api_key:
                  type: string
                  format: secret
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        textwrap.dedent(
            """
            from __future__ import annotations

            import argparse
            import json
            import os
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.path == "/health":
                        self.send_response(200)
                        self.end_headers()
                        return
                    self.send_response(404)
                    self.end_headers()

                def do_POST(self):
                    if self.headers.get("Authorization") != f"Bearer {os.environ['YUUBOT_INTERNAL_TOKEN']}":
                        self.send_response(403)
                        self.end_headers()
                        return
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length).decode() or "{}")
                    function = self.path.rsplit("/", 1)[-1]
                    body = json.dumps(
                        {"ok": True, "function": function, "payload": payload}
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, format, *args):
                    return


            parser = argparse.ArgumentParser()
            parser.add_argument("--port", type=int, required=True)
            args = parser.parse_args()
            ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return root
