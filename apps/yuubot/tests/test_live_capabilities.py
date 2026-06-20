"""Tests for /api/live-capabilities endpoint and _visible_capabilities filtering."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from yuubot.bootstrap.config import BootstrapConfig
from yuubot.core.actors.impls.python_session import ActorPythonSessionFactory
from yuubot.core.facade import IntegrationInvokeBridge
from yuubot.core.facade.workspace import FacadeWorkspace
from yuubot.core.integrations import (
    IntegrationCore,
    default_integration_factories,
)
from yuubot.core.integrations.impls.github import (
    GITHUB_FILE_READ_CAPABILITY_ID,
    GITHUB_ISSUE_COMMENT_CAPABILITY_ID,
    GITHUB_ISSUE_CREATE_CAPABILITY_ID,
    GITHUB_ISSUE_LIST_CAPABILITY_ID,
    GITHUB_ISSUE_READ_CAPABILITY_ID,
)
from yuubot.core.secrets import Secret
from yuubot.resources.records import (
    CapabilitySetRecord,
    IntegrationRecord,
    ResourcePolicy,
    RuntimePolicy,
)
from yuubot.resources.root import Resources
from yuubot.resources.store.models import IntegrationORM
from yuubot.runtime.admin import DaemonClient, build_admin_asgi_app


# ---------------------------------------------------------------------------
# Test: existing_instance_capabilities
# ---------------------------------------------------------------------------


async def test_existing_instance_capabilities_includes_enabled_status(
    resources: Resources,
):
    """IntegrationCore.existing_instance_capabilities() returns capabilities
    with the correct enabled status from the integration record."""
    integrations = IntegrationCore(
        repository=resources.repository,
        factories=default_integration_factories(),
    )

    # Insert an enabled echo integration
    await resources.repository.insert(
        IntegrationORM,
        IntegrationRecord(
            id="echo-main",
            name="echo",
            config={"source_path": "channels/test"},
            enabled=True,
        ),
    )

    caps = await integrations.existing_instance_capabilities()

    # The enabled echo record exposes echo.echo and echo.reply
    cap_ids = {(c.capability_id, c.enabled, c.integration_id) for c in caps}
    assert ("echo.echo", True, "echo-main") in cap_ids
    assert ("echo.reply", True, "echo-main") in cap_ids

    # Check CapabilityInstanceInfo fields
    echo_cap = next(c for c in caps if c.capability_id == "echo.echo")
    assert echo_cap.capability_name == "Echo"
    assert echo_cap.description == "Returns the payload unchanged."
    assert echo_cap.namespace == "echo"
    assert echo_cap.integration_name == "echo"


async def test_existing_instance_capabilities_includes_github(
    resources: Resources,
):
    integrations = IntegrationCore(
        repository=resources.repository,
        factories=default_integration_factories(),
    )
    await resources.repository.insert(
        IntegrationORM,
        IntegrationRecord(
            id="github-main",
            name="github",
            config={
                "token": Secret("test-token"),
                "default_owner": "yuulabs",
                "default_repo": "yuubot",
            },
            enabled=True,
        ),
    )

    caps = await integrations.existing_instance_capabilities()

    cap_ids = {(c.capability_id, c.enabled, c.integration_id) for c in caps}
    assert (
        GITHUB_ISSUE_LIST_CAPABILITY_ID,
        True,
        "github-main",
    ) in cap_ids
    assert (
        GITHUB_ISSUE_READ_CAPABILITY_ID,
        True,
        "github-main",
    ) in cap_ids
    assert (
        GITHUB_ISSUE_CREATE_CAPABILITY_ID,
        True,
        "github-main",
    ) in cap_ids
    assert (
        GITHUB_ISSUE_COMMENT_CAPABILITY_ID,
        True,
        "github-main",
    ) in cap_ids
    assert (
        GITHUB_FILE_READ_CAPABILITY_ID,
        True,
        "github-main",
    ) in cap_ids

    github_cap = next(
        c for c in caps if c.capability_id == GITHUB_ISSUE_LIST_CAPABILITY_ID
    )
    assert github_cap.capability_name == "List GitHub issues"
    assert github_cap.namespace == "github"
    assert github_cap.integration_name == "github"


async def test_existing_instance_capabilities_shows_disabled_status(
    resources: Resources,
):
    """Disabled integration records report enabled=False."""
    integrations = IntegrationCore(
        repository=resources.repository,
        factories=default_integration_factories(),
    )

    # Insert a disabled echo integration
    await resources.repository.insert(
        IntegrationORM,
        IntegrationRecord(
            id="echo-off",
            name="echo",
            config={"source_path": "channels/off"},
            enabled=False,
        ),
    )

    caps = await integrations.existing_instance_capabilities()

    cap_ids = {(c.capability_id, c.enabled, c.integration_id) for c in caps}
    assert ("echo.echo", False, "echo-off") in cap_ids
    assert ("echo.reply", False, "echo-off") in cap_ids


async def test_existing_instance_capabilities_empty_when_no_records(
    resources: Resources,
):
    """Returns empty list when no integration records exist."""
    integrations = IntegrationCore(
        repository=resources.repository,
        factories=default_integration_factories(),
    )
    caps = await integrations.existing_instance_capabilities()
    assert caps == []


async def test_existing_instance_capabilities_skips_missing_factory(
    resources: Resources,
):
    """Silently skips records whose factory is not registered."""
    await resources.repository.insert(
        IntegrationORM,
        IntegrationRecord(
            id="orphan",
            name="nonexistent-kind",
            config={},
            enabled=True,
        ),
    )
    integrations = IntegrationCore(
        repository=resources.repository,
        factories=default_integration_factories(),
    )
    caps = await integrations.existing_instance_capabilities()
    # The "nonexistent-kind" factory doesn't exist -- record is skipped
    assert not any(c.integration_id == "orphan" for c in caps)


# ---------------------------------------------------------------------------
# Test: _visible_capabilities filters by existing instances
# ---------------------------------------------------------------------------


async def test_visible_capabilities_filters_by_existing_instances(
    resources: Resources,
    tmp_path: Path,
):
    """_visible_capabilities only shows capabilities that have an existing
    instance record, even if the CapabilitySet allows them."""
    # Insert an echo integration record
    await resources.repository.insert(
        IntegrationORM,
        IntegrationRecord(
            id="echo-main",
            name="echo",
            config={"source_path": "channels/test"},
            enabled=True,
        ),
    )

    integrations = IntegrationCore(
        repository=resources.repository,
        factories=default_integration_factories(),
    )
    workspace = FacadeWorkspace(tmp_path / "facades")
    bridge = IntegrationInvokeBridge(integrations)
    factory = ActorPythonSessionFactory(
        integrations=integrations,
        workspace=workspace,
        bridge=bridge,
    )

    # CapabilitySet allows echo.echo AND a non-existent capability
    cap_set = CapabilitySetRecord(
        id="test-cs",
        name="Test CS",
        integration_capability_ids=("echo.echo", "telegram.send"),
        runtime_policy=RuntimePolicy(),
        resource_policy=ResourcePolicy(workspace_access="read_write"),
    )

    class FakeBinding:
        capability_set = cap_set

    binding = FakeBinding()  # type: ignore[assignment]

    visible = await factory._visible_capabilities(binding)  # type: ignore[arg-type]
    visible_ids = {c.id for c in visible}

    assert "echo.echo" in visible_ids
    assert "telegram.send" not in visible_ids  # no telegram instance exists


async def test_visible_capabilities_returns_empty_when_no_instances_match(
    resources: Resources,
    tmp_path: Path,
):
    """Returns empty list when allowed capabilities have no existing instances."""
    integrations = IntegrationCore(
        repository=resources.repository,
        factories=default_integration_factories(),
    )
    workspace = FacadeWorkspace(tmp_path / "facades")
    bridge = IntegrationInvokeBridge(integrations)
    factory = ActorPythonSessionFactory(
        integrations=integrations,
        workspace=workspace,
        bridge=bridge,
    )

    cap_set = CapabilitySetRecord(
        id="test-cs",
        name="Test CS",
        integration_capability_ids=("echo.echo",),
        runtime_policy=RuntimePolicy(),
        resource_policy=ResourcePolicy(workspace_access="read_write"),
    )

    class FakeBinding:
        capability_set = cap_set

    binding = FakeBinding()  # type: ignore[assignment]

    visible = await factory._visible_capabilities(binding)  # type: ignore[arg-type]
    assert visible == []


# ---------------------------------------------------------------------------
# Test: /api/live-capabilities endpoint
# ---------------------------------------------------------------------------


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


async def test_live_capabilities_endpoint_returns_existing_instances(
    resources: Resources,
    admin_app,
):
    """GET /api/live-capabilities returns capabilities from integration records."""
    # Create an enabled echo instance
    await resources.repository.insert(
        IntegrationORM,
        IntegrationRecord(
            id="echo-main",
            name="echo",
            config={"source_path": "channels/test"},
            enabled=True,
        ),
    )

    async with _client(admin_app) as client:
        response = await client.get("/api/live-capabilities")

    assert response.status_code == 200
    payload = response.json()
    assert "capabilities" in payload

    caps = payload["capabilities"]
    builtin_ids = {c["capability_id"] for c in caps if c["integration_id"] == "builtin"}
    assert {"builtin.read", "builtin.edit", "builtin.write"} <= builtin_ids
    cap_ids = {(c["capability_id"], c["enabled"], c["integration_id"]) for c in caps}
    assert ("echo.echo", True, "echo-main") in cap_ids
    assert ("echo.reply", True, "echo-main") in cap_ids

    # Check response shape
    echo_cap = next(c for c in caps if c["capability_id"] == "echo.echo")
    assert echo_cap["capability_name"] == "Echo"
    assert echo_cap["description"] == "Returns the payload unchanged."
    assert echo_cap["namespace"] == "echo"
    assert echo_cap["integration_name"] == "echo"


async def test_live_capabilities_endpoint_returns_github_capabilities(
    resources: Resources,
    admin_app,
):
    await resources.repository.insert(
        IntegrationORM,
        IntegrationRecord(
            id="github-main",
            name="github",
            config={
                "token": Secret("test-token"),
                "default_owner": "yuulabs",
                "default_repo": "yuubot",
            },
            enabled=True,
        ),
    )

    async with _client(admin_app) as client:
        response = await client.get("/api/live-capabilities")

    assert response.status_code == 200
    caps = response.json()["capabilities"]
    cap_ids = {(c["capability_id"], c["enabled"], c["integration_id"]) for c in caps}
    assert (
        GITHUB_ISSUE_LIST_CAPABILITY_ID,
        True,
        "github-main",
    ) in cap_ids
    assert (
        GITHUB_ISSUE_READ_CAPABILITY_ID,
        True,
        "github-main",
    ) in cap_ids
    assert (
        GITHUB_ISSUE_CREATE_CAPABILITY_ID,
        True,
        "github-main",
    ) in cap_ids
    assert (
        GITHUB_ISSUE_COMMENT_CAPABILITY_ID,
        True,
        "github-main",
    ) in cap_ids
    assert (
        GITHUB_FILE_READ_CAPABILITY_ID,
        True,
        "github-main",
    ) in cap_ids

    github_cap = next(
        c for c in caps if c["capability_id"] == GITHUB_ISSUE_LIST_CAPABILITY_ID
    )
    assert github_cap["capability_name"] == "List GitHub issues"
    assert github_cap["namespace"] == "github"
    assert github_cap["integration_name"] == "github"


async def test_live_capabilities_endpoint_shows_disabled(
    resources: Resources,
    admin_app,
):
    """GET /api/live-capabilities shows disabled status from integration record."""
    await resources.repository.insert(
        IntegrationORM,
        IntegrationRecord(
            id="echo-off",
            name="echo",
            config={"source_path": "channels/off"},
            enabled=False,
        ),
    )

    async with _client(admin_app) as client:
        response = await client.get("/api/live-capabilities")

    assert response.status_code == 200
    caps = response.json()["capabilities"]
    cap_ids = {(c["capability_id"], c["enabled"], c["integration_id"]) for c in caps}
    assert ("echo.echo", False, "echo-off") in cap_ids
    assert ("echo.reply", False, "echo-off") in cap_ids


async def test_live_capabilities_endpoint_empty_when_no_instances(
    admin_app,
):
    """Returns built-in capabilities when no integration records exist."""
    async with _client(admin_app) as client:
        response = await client.get("/api/live-capabilities")

    assert response.status_code == 200
    cap_ids = {cap["capability_id"] for cap in response.json()["capabilities"]}
    assert cap_ids == {"builtin.read", "builtin.edit", "builtin.write"}
