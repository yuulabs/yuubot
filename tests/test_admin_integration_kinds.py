"""Admin HTTP surface tests."""

from __future__ import annotations

import httpx
import msgspec
import pytest

from yuubot.bootstrap.config import BootstrapConfig
from yuubot.core.gateway import Gateway
from yuubot.core.integrations import IntegrationFactoryRegistry, default_integration_factories
from yuubot.core.integrations.contracts import IntegrationInstance, IntegrationStorage
from yuubot.core.integrations.echo import ECHO_CAPABILITY_ID
from yuubot.core.secrets import Secret
from yuubot.resources.records import IntegrationRecord
from yuubot.resources.root import Resources
from yuubot.resources.store.models import IntegrationORM
from yuubot.runtime.admin import DaemonClient, build_admin_asgi_app


@pytest.fixture
def admin_app(resources: Resources, yuubot_config: BootstrapConfig):
    return build_admin_asgi_app(
        config=yuubot_config.admin,
        resources=resources,
        daemon=DaemonClient(base_url="http://daemon"),
        integration_factories=default_integration_factories(),
    )


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


async def test_integration_kinds_endpoint_exposes_echo(admin_app) -> None:
    async with _client(admin_app) as client:
        response = await client.get("/api/integration-kinds")

    assert response.status_code == 200
    payload = response.json()
    assert "kinds" in payload

    by_name = {kind["name"]: kind for kind in payload["kinds"]}
    assert "echo" in by_name

    echo = by_name["echo"]
    assert echo["description"]

    schema = echo["config_schema"]
    assert schema["type"] == "object"
    assert "source_path" in schema["properties"]
    assert "channel_id" in schema["properties"]
    assert schema["properties"]["source_path"]["title"] == "Source path"

    capability_ids = [cap["id"] for cap in echo["capabilities"]]
    assert ECHO_CAPABILITY_ID in capability_ids


async def test_secret_config_schema_and_reveal_endpoint(
    resources: Resources,
    yuubot_config: BootstrapConfig,
) -> None:
    registry = IntegrationFactoryRegistry()
    registry.register(SecretIntegrationFactory())
    app = build_admin_asgi_app(
        config=yuubot_config.admin,
        resources=resources,
        daemon=DaemonClient(base_url="http://daemon"),
        integration_factories=registry,
    )

    await resources.repository.insert(
        IntegrationORM,
        IntegrationRecord(
            id="secret-int",
            name="secret-kind",
            config={"bot_token": Secret("plain-token"), "label": "test"},
        ),
    )

    with resources.store.db.activate():
        row = await IntegrationORM.get(id="secret-int")
    assert row.config["bot_token"]["$enc"] == "v1"
    assert "plain-token" not in repr(row.config)

    loaded = await resources.repository.get(IntegrationORM, "secret-int")
    assert loaded is not None
    assert isinstance(loaded.config["bot_token"], Secret)
    assert loaded.config["bot_token"].reveal() == "plain-token"

    async with _client(app) as client:
        kinds = await client.get("/api/integration-kinds")
        revealed = await client.get(
            "/api/integrations/secret-int/secrets/bot_token/reveal"
        )

    schema = kinds.json()["kinds"][0]["config_schema"]
    assert schema["properties"]["bot_token"]["format"] == "secret"
    assert revealed.status_code == 200
    assert revealed.json()["data"]["value"] == "plain-token"


class SecretIntegrationConfig(msgspec.Struct, forbid_unknown_fields=False):
    bot_token: Secret
    label: str = ""


class SecretIntegrationFactory:
    name = "secret-kind"
    description = "Secret config test integration."
    config_schema = SecretIntegrationConfig

    def capability_specs(self):
        return []

    async def create(
        self,
        record: IntegrationRecord,
        *,
        gateway: Gateway,
        storage: IntegrationStorage,
    ) -> IntegrationInstance:
        _ = record, gateway, storage
        raise NotImplementedError
