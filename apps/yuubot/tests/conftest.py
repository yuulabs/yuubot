"""Test fixtures for yuubot v2."""

from __future__ import annotations

import pytest

from yuubot.bootstrap.config import BootstrapConfig
from yuubot.core.assembly._tools import set_assembly_tool_registry
from yuubot.core.tools import default_tool_factories
from yuubot.process import open_resources
from yuubot.resources.store.resource import Store


@pytest.fixture(autouse=True)
def _assembly_tool_registry() -> None:
    """Populate the assembly ToolRegistry used by ``build_agent_definition``.

    Mirrors the daemon's startup wiring (``set_assembly_tool_registry``) so
    the tool compiler can resolve ``ToolFactory`` by name in unit tests that
    build agent definitions without spawning the full daemon.
    """
    set_assembly_tool_registry(default_tool_factories())


@pytest.fixture
async def db():
    """Temporary in-memory SQLite database."""
    store = await Store.open(":memory:")
    await store.migrate()
    yield store
    await store.close()


@pytest.fixture
def yuubot_config():
    """Programmatic test config."""
    return BootstrapConfig.for_tests()


@pytest.fixture
async def resources(db: Store, yuubot_config: BootstrapConfig):
    """Fully hydrated Resources tree with in-memory store."""

    async def _make_store(_):
        return db

    loaded = await open_resources(
        yuubot_config,
        create_store=_make_store,
    )
    await loaded.event_bus.start()
    yield loaded
    await loaded.event_bus.stop()
    await loaded.close()
