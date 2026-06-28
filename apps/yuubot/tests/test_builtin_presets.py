"""Install-time seed behavior for built-in preset records."""

from __future__ import annotations

import httpx
from starlette.applications import Starlette
from starlette.routing import Route

from yuubot.bootstrap.config import BootstrapConfig
from yuubot.process import open_resources
from yuubot.resources import builtin_presets
from yuubot.resources.store.models import ActorORM, CapabilitySetORM
from yuubot.resources.store.resource import Store
from yuubot.runtime.daemon.handlers import make_preset_actors_handler

GENERAL_CAPABILITY_ID = "builtin-capability-general"
SHIORI_CAPABILITY_ID = "builtin-capability-shiori"


async def _open(store: Store, config: BootstrapConfig):
    async def _make_store(_):
        return store

    return await open_resources(config, create_store=_make_store)


async def test_open_resources_seeds_preset_capability_sets(
    db: Store, yuubot_config: BootstrapConfig
) -> None:
    resources = await _open(db, yuubot_config)

    capability_sets = await resources.repository.list(CapabilitySetORM)
    by_id = {c.id: c for c in capability_sets}
    assert set(by_id) >= {GENERAL_CAPABILITY_ID, SHIORI_CAPABILITY_ID}

    general = by_id[GENERAL_CAPABILITY_ID]
    assert general.name == "general"
    assert general.workspace_path == "general"
    assert general.integration_ids == (builtin_presets.ALL_INTEGRATIONS_SENTINEL,)

    shiori = by_id[SHIORI_CAPABILITY_ID]
    assert shiori.name == "shiori"
    assert shiori.workspace_path == "shiori"
    assert shiori.integration_ids == (builtin_presets.ALL_INTEGRATIONS_SENTINEL,)


async def test_open_resources_seeds_exactly_two_preset_capability_sets(
    db: Store, yuubot_config: BootstrapConfig
) -> None:
    resources = await _open(db, yuubot_config)

    capability_sets = await resources.repository.list(CapabilitySetORM)
    builtin_caps = [c for c in capability_sets if c.id.startswith("builtin-capability-")]
    assert len(builtin_caps) == 2
    assert {c.name for c in builtin_caps} == {"general", "shiori"}


async def test_reopen_resources_is_idempotent_and_preserves_user_edits(
    db: Store, yuubot_config: BootstrapConfig
) -> None:
    first = await _open(db, yuubot_config)
    updated = await first.repository.update(
        CapabilitySetORM,
        GENERAL_CAPABILITY_ID,
        description="user edited description",
    )
    assert updated is not None
    assert updated.description == "user edited description"

    second = await _open(db, yuubot_config)
    capability_sets = await second.repository.list(CapabilitySetORM)
    general_capabilities = [c for c in capability_sets if c.name == "general"]
    assert len(general_capabilities) == 1
    assert general_capabilities[0].id == GENERAL_CAPABILITY_ID
    assert general_capabilities[0].description == "user edited description"
    assert len([c for c in capability_sets if c.name == "shiori"]) == 1


async def test_open_resources_does_not_seed_actors(
    db: Store, yuubot_config: BootstrapConfig
) -> None:
    resources = await _open(db, yuubot_config)
    actors = await resources.repository.list(ActorORM)
    assert actors == ()


def test_builtin_persona_prompts_are_loaded_from_package_files() -> None:
    prompts = builtin_presets.BUILTIN_PERSONA_PROMPTS

    assert prompts["general"] == builtin_presets.GENERAL_PERSONA_PROMPT
    assert prompts["shiori"] == builtin_presets.SHIORI_PERSONA_PROMPT
    assert prompts["general"] == "You are a helpful assistant."
    assert "汐织" in prompts["shiori"]
    assert "Scenario Communication" in prompts["shiori"]


async def test_preset_actors_endpoint_returns_named_file_backed_prompts() -> None:
    app = Starlette(
        routes=[
            Route("/api/preset-actors", make_preset_actors_handler(), methods=("GET",))
        ]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/preset-actors")

    assert response.status_code == 200
    data = response.json()["data"]
    assert [item["actor_name"] for item in data] == ["General", "Shiori"]
    assert [item["capability_set_id"] for item in data] == [
        GENERAL_CAPABILITY_ID,
        SHIORI_CAPABILITY_ID,
    ]
    assert data[0]["persona_prompt"] == builtin_presets.GENERAL_PERSONA_PROMPT
    assert data[1]["persona_prompt"] == builtin_presets.SHIORI_PERSONA_PROMPT
