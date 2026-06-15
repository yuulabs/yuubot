"""Test fixtures for yuubot v2."""

from __future__ import annotations

import pytest

from yuubot.bootstrap.config import BootstrapConfig
from yuubot.process import open_resources
from yuubot.resources.store.resource import Store


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
