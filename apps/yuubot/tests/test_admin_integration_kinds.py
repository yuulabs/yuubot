"""Admin HTTP surface tests."""

from __future__ import annotations

from pathlib import Path

import httpx
import msgspec
import pytest
import yuullm

from yuubot.bootstrap.config import BootstrapConfig
from yuubot.core.gateway import Gateway
from yuubot.core.integrations import (
    IntegrationFactoryRegistry,
    default_integration_factories,
)
from yuubot.core.integrations.contracts import IntegrationInstance, IntegrationStorage
from yuubot.core.integrations.impls.echo import ECHO_CAPABILITY_ID
from yuubot.core.integrations.impls.github import (
    GITHUB_FILE_READ_CAPABILITY_ID,
    GITHUB_ISSUE_COMMENT_CAPABILITY_ID,
    GITHUB_ISSUE_CREATE_CAPABILITY_ID,
    GITHUB_ISSUE_LIST_CAPABILITY_ID,
    GITHUB_ISSUE_READ_CAPABILITY_ID,
)
from yuubot.core.secrets import Secret
from yuubot.core.validation import LLMProviderOptions
from yuubot.resources.records import (
    BudgetPolicy,
    IntegrationRecord,
    LLMBackendRecord,
    ModelCapabilities,
    ModelCatalog,
    PricingTable,
)
from yuubot.resources.root import Resources
from yuubot.resources.store.models import IntegrationORM, LLMBackendORM
import yuubot.runtime.admin.app as admin_module
from yuubot.runtime.admin import DaemonClient, build_admin_asgi_app
from yuubot.core.integrations.impls.github.models import GitHubOAuthTokenResponse


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


async def test_integration_kinds_endpoint_exposes_github_schema(admin_app) -> None:
    async with _client(admin_app) as client:
        response = await client.get("/api/integration-kinds")

    assert response.status_code == 200
    by_name = {kind["name"]: kind for kind in response.json()["kinds"]}
    assert "github" in by_name

    github = by_name["github"]
    schema = github["config_schema"]
    assert schema["type"] == "object"
    assert "client_id" in schema["properties"]
    assert schema["properties"]["client_secret"]["format"] == "secret"
    assert schema["properties"]["access_token"]["format"] == "secret"
    assert schema["properties"]["oauth_state"]["format"] == "secret"
    assert "oauth_scope" in schema["properties"]
    assert "default_owner" in schema["properties"]
    assert "default_repo" in schema["properties"]

    capability_ids = {cap["id"] for cap in github["capabilities"]}
    assert capability_ids == {
        GITHUB_ISSUE_LIST_CAPABILITY_ID,
        GITHUB_ISSUE_READ_CAPABILITY_ID,
        GITHUB_ISSUE_CREATE_CAPABILITY_ID,
        GITHUB_ISSUE_COMMENT_CAPABILITY_ID,
        GITHUB_FILE_READ_CAPABILITY_ID,
    }


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


async def test_github_oauth_start_redirects_and_stores_state(
    resources: Resources,
    yuubot_config: BootstrapConfig,
) -> None:
    app = build_admin_asgi_app(
        config=yuubot_config.admin,
        resources=resources,
        daemon=DaemonClient(base_url="http://daemon"),
        integration_factories=default_integration_factories(),
    )
    await resources.repository.insert(
        IntegrationORM,
        IntegrationRecord(
            id="github-main",
            name="github",
            config={
                "client_id": "client-id",
                "client_secret": Secret("client-secret"),
                "oauth_authorize_url": "https://github.test/login/oauth/authorize",
            },
        ),
    )

    async with _client(app) as client:
        response = await client.get(
            "/api/integrations/github-main/github/oauth/start",
            follow_redirects=False,
        )

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://github.test/login/oauth/authorize?")
    assert "client_id=client-id" in location
    assert "scope=repo" in location
    loaded = await resources.repository.get(IntegrationORM, "github-main")
    assert loaded is not None
    state = loaded.config["oauth_state"]
    assert isinstance(state, Secret)
    assert state.reveal()
    assert f"state={state.reveal()}" in location


async def test_github_oauth_callback_exchanges_code_and_stores_access_token(
    resources: Resources,
    yuubot_config: BootstrapConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeOAuthClient:
        async def exchange_code(
            self,
            *,
            client_id: str,
            client_secret: str,
            code: str,
            redirect_uri: str,
        ) -> GitHubOAuthTokenResponse:
            captured.update(
                {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                }
            )
            return GitHubOAuthTokenResponse(access_token="oauth-token")

        async def close(self) -> None:
            captured["closed"] = True

    captured: dict[str, object] = {}

    def fake_oauth_client(token_url: str) -> FakeOAuthClient:
        captured["token_url"] = token_url
        return FakeOAuthClient()

    monkeypatch.setattr(
        admin_module,
        "_create_github_oauth_client",
        fake_oauth_client,
    )
    app = build_admin_asgi_app(
        config=yuubot_config.admin,
        resources=resources,
        daemon=DaemonClient(base_url="http://daemon"),
        integration_factories=default_integration_factories(),
    )
    await resources.repository.insert(
        IntegrationORM,
        IntegrationRecord(
            id="github-main",
            name="github",
            config={
                "client_id": "client-id",
                "client_secret": Secret("client-secret"),
                "oauth_state": Secret("state-123"),
                "oauth_access_token_url": "https://github.test/login/oauth/access_token",
            },
        ),
    )

    async with _client(app) as client:
        response = await client.get(
            "/api/integrations/github-main/github/oauth/callback"
            "?code=code-123&state=state-123",
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == "/integrations/github-main?github=connected"
    assert captured["token_url"] == "https://github.test/login/oauth/access_token"
    assert captured["client_id"] == "client-id"
    assert captured["client_secret"] == "client-secret"
    assert captured["code"] == "code-123"
    assert captured["redirect_uri"] == (
        "http://testserver/api/integrations/github-main/github/oauth/callback"
    )
    assert captured["closed"] is True
    loaded = await resources.repository.get(IntegrationORM, "github-main")
    assert loaded is not None
    access_token = loaded.config["access_token"]
    oauth_state = loaded.config["oauth_state"]
    assert isinstance(access_token, Secret)
    assert access_token.reveal() == "oauth-token"
    assert isinstance(oauth_state, Secret)
    assert oauth_state.reveal() == ""


async def test_github_oauth_callback_rejects_state_mismatch(
    resources: Resources,
    yuubot_config: BootstrapConfig,
) -> None:
    app = build_admin_asgi_app(
        config=yuubot_config.admin,
        resources=resources,
        daemon=DaemonClient(base_url="http://daemon"),
        integration_factories=default_integration_factories(),
    )
    await resources.repository.insert(
        IntegrationORM,
        IntegrationRecord(
            id="github-main",
            name="github",
            config={
                "client_id": "client-id",
                "client_secret": Secret("client-secret"),
                "oauth_state": Secret("expected"),
            },
        ),
    )

    async with _client(app) as client:
        response = await client.get(
            "/api/integrations/github-main/github/oauth/callback"
            "?code=code-123&state=wrong",
        )

    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


async def test_admin_resource_proxy_injects_daemon_secret(
    resources: Resources,
    yuubot_config: BootstrapConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_request_daemon(
        daemon: DaemonClient,
        path: str,
        *,
        method: str,
        body: bytes = b"",
        content_type: str = "application/json",
    ) -> admin_module.DaemonResponse:
        captured.update(
            {
                "daemon_secret": daemon.daemon_secret,
                "path": path,
                "method": method,
                "body": body,
                "content_type": content_type,
            }
        )
        return admin_module.DaemonResponse(
            status_code=201,
            body=b'{"status":"ok","data":{"id":"backend-1"},"actions":["refresh"]}',
        )

    monkeypatch.setattr(admin_module, "_request_daemon", fake_request_daemon)
    app = build_admin_asgi_app(
        config=yuubot_config.admin,
        resources=resources,
        daemon=DaemonClient(base_url="http://daemon", daemon_secret="server-only"),
        integration_factories=default_integration_factories(),
    )

    async with _client(app) as client:
        response = await client.post(
            "/api/resources/llm-backends?refresh=true",
            json={"name": "backend-1"},
        )

    assert response.status_code == 201
    assert response.json()["actions"] == ["refresh"]
    assert captured["daemon_secret"] == "server-only"
    assert captured["path"] == "/api/resources/llm-backends?refresh=true"
    assert captured["method"] == "POST"
    assert b"backend-1" in captured["body"]
    assert captured["content_type"].startswith("application/json")


async def test_admin_resource_proxy_preserves_daemon_errors(
    resources: Resources,
    yuubot_config: BootstrapConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_request_daemon(
        daemon: DaemonClient,
        path: str,
        *,
        method: str,
        body: bytes = b"",
        content_type: str = "application/json",
    ) -> admin_module.DaemonResponse:
        _ = daemon, path, method, body, content_type
        return admin_module.DaemonResponse(
            status_code=400,
            body=b'{"status":"error","code":"validation_error","detail":"bad actor"}',
        )

    monkeypatch.setattr(admin_module, "_request_daemon", fake_request_daemon)
    app = build_admin_asgi_app(
        config=yuubot_config.admin,
        resources=resources,
        daemon=DaemonClient(base_url="http://daemon", daemon_secret="server-only"),
        integration_factories=default_integration_factories(),
    )

    async with _client(app) as client:
        response = await client.put(
            "/api/resources/actors/actor-1",
            json={"character": {"id": "missing"}},
        )

    assert response.status_code == 400
    assert response.json() == {
        "status": "error",
        "code": "validation_error",
        "detail": "bad actor",
    }


async def test_provider_models_endpoint_fetches_models_server_side(
    resources: Resources,
    yuubot_config: BootstrapConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = await resources.repository.insert(
        LLMBackendORM,
        LLMBackendRecord(
            id="deepseek-main",
            name="deepseek-main",
            yuuagents_provider="openai",
            model_capabilities=ModelCapabilities(chat=True),
            models=ModelCatalog(),
            pricing=PricingTable(),
            budget=BudgetPolicy(),
            provider_options=LLMProviderOptions(
                base_url="https://api.deepseek.com",
                provider_name="deepseek",
            ),
        ),
    )
    captured: dict[str, str] = {}

    def fake_create_provider_model_client(
        record: LLMBackendRecord,
        *,
        api_key: str = "",
        base_url: str = "",
    ) -> FakeModelClient:
        captured["backend_id"] = record.id
        captured["api_key"] = api_key
        captured["base_url"] = base_url
        return FakeModelClient()

    monkeypatch.setattr(
        admin_module,
        "_create_provider_model_client",
        fake_create_provider_model_client,
    )
    app = build_admin_asgi_app(
        config=yuubot_config.admin,
        resources=resources,
        daemon=DaemonClient(base_url="http://daemon"),
        integration_factories=default_integration_factories(),
    )

    async with _client(app) as client:
        response = await client.post(
            f"/api/providers/{backend.id}/models",
            json={
                "api_key": "sk-test",
                "base_url": "https://api.deepseek.com",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "data": [
            {"id": "deepseek-chat", "displayName": "DeepSeek Chat"},
            {"id": "deepseek-reasoner"},
        ],
    }
    assert captured == {
        "backend_id": "deepseek-main",
        "api_key": "sk-test",
        "base_url": "https://api.deepseek.com",
    }


async def test_provider_models_endpoint_reports_missing_backend(
    admin_app,
) -> None:
    async with _client(admin_app) as client:
        response = await client.post("/api/providers/missing/models")

    assert response.status_code == 404
    assert response.json()["detail"] == "llm backend not found"


async def test_provider_validate_reports_default_model_and_capabilities(
    resources: Resources,
    yuubot_config: BootstrapConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = await resources.repository.insert(
        LLMBackendORM,
        LLMBackendRecord(
            id="provider-main",
            name="provider-main",
            yuuagents_provider="openai",
            default_model="deepseek-chat",
            model_capabilities=ModelCapabilities(chat=True, tool_calling=True),
            models=ModelCatalog(),
            pricing=PricingTable(),
            budget=BudgetPolicy(),
        ),
    )

    monkeypatch.setattr(
        admin_module,
        "_create_provider_model_client",
        lambda *args, **kwargs: FakeModelClient(),
    )
    app = build_admin_asgi_app(
        config=yuubot_config.admin,
        resources=resources,
        daemon=DaemonClient(base_url="http://daemon"),
        integration_factories=default_integration_factories(),
    )

    async with _client(app) as client:
        response = await client.post(f"/api/providers/{backend.id}/validate")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "data": {
            "valid": True,
            "detail": "",
            "default_model_valid": True,
            "models": [
                {"id": "deepseek-chat", "displayName": "DeepSeek Chat"},
                {"id": "deepseek-reasoner"},
            ],
            "capabilities": {
                "chat": True,
                "vision": False,
                "tool_calling": True,
                "reasoning": False,
                "embedding": False,
                "structured_output": False,
            },
        },
    }


async def test_monitor_spa_route_is_not_shadowed_by_trace_ui(
    resources: Resources,
    yuubot_config: BootstrapConfig,
    tmp_path: Path,
) -> None:
    web_dist = tmp_path / "web-dist"
    web_dist.mkdir()
    (web_dist / "index.html").write_text(
        "<main>yuubot monitor</main>", encoding="utf-8"
    )
    app = build_admin_asgi_app(
        config=msgspec.structs.replace(
            yuubot_config.admin,
            web_dist_dir=str(web_dist),
        ),
        resources=resources,
        daemon=DaemonClient(base_url="http://daemon"),
        integration_factories=default_integration_factories(),
        trace_db_path=str(tmp_path / "traces.db"),
    )

    async with _client(app) as client:
        monitor = await client.get("/monitor")

    assert monitor.status_code == 200
    assert "yuubot monitor" in monitor.text


def test_provider_model_client_uses_provider_name_from_backend() -> None:
    """_create_provider_model_client derives provider_name from
    backend.provider_options.provider_name."""
    backend = LLMBackendRecord(
        id="deepseek-main",
        name="deepseek-main",
        yuuagents_provider="openai",
        model_capabilities=ModelCapabilities(chat=True),
        models=ModelCatalog(),
        pricing=PricingTable(),
        budget=BudgetPolicy(),
        provider_options=LLMProviderOptions(
            base_url="https://api.deepseek.com",
            provider_name="deepseek",
            api_key="sk-example",
        ),
    )

    # api_key from request body parameter takes priority over
    # provider_options.api_key; both paths construct without error
    from_request = admin_module._create_provider_model_client(
        backend, api_key="sk-from-request"
    )
    assert from_request._provider_name == "deepseek"

    from_provider_options = admin_module._create_provider_model_client(backend)
    assert from_provider_options._provider_name == "deepseek"


class SecretIntegrationConfig(msgspec.Struct, forbid_unknown_fields=False):
    bot_token: Secret
    label: str = ""


class SecretIntegrationFactory:
    name = "secret-kind"
    description = "Secret config test integration."
    config_schema = SecretIntegrationConfig
    source_path_convention = ""

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

    def routes(self, integrations: object) -> list:
        return []


class FakeModelClient:
    async def list_models(self) -> list[yuullm.ProviderModel]:
        return [
            yuullm.ProviderModel(id="deepseek-chat", display_name="DeepSeek Chat"),
            yuullm.ProviderModel(id="deepseek-reasoner"),
        ]
