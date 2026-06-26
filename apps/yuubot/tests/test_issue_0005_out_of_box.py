"""ISSUE-0005 out-of-box scenario regression.

Public boundary: the daemon resource HTTP API (``build_daemon_asgi_app`` over
fresh ``Resources``). This test proves the first-use flow described in the
issue survives through the same API the Admin UI uses, end to end:

    fresh resources (seeded presets)
      -> built-in Characters and CapabilitySets are visible
      -> Admin creates an OpenAI LLMBackend with only preset metadata + api_key
        -> daemon fills the built-in model catalogue and pricing
      -> Admin mints preset Actors (general, shiori) bound to that backend
        -> Actor creation passes USD pricing validation (max_usd == 2.0)
      -> both Actors are visible and point at the created backend

The OpenAI runtime provider key (``"openai"``) is the design-flagged risk: it
must be accepted by the runtime provider-resolution path without a local
``config.yaml`` provider entry. That is proven through the same resolution
function the daemon wiring feeds into ``Stage``/``ProviderPool``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yuubot.bootstrap.config import BootstrapConfig
from yuubot.core.assembly._constants import _resolve_yuuagents_provider
from yuubot.core.assembly._llm_session import provider_key_for_backend
from yuubot.process import open_resources
from yuubot.resources.records import (
    BudgetPolicy,
    LLMBackendRecord,
    ModelCapabilities,
    ModelCatalog,
    PricingTable,
)
from yuubot.resources.store.resource import Store
from tests.helpers import build_resource_daemon_runtime, daemon_http_client

GENERAL_CHARACTER_ID = "builtin-character-general"
SHIORI_CHARACTER_ID = "builtin-character-shiori"
GENERAL_CAPABILITY_ID = "builtin-capability-general"
SHIORI_CAPABILITY_ID = "builtin-capability-shiori"


@pytest.fixture
async def scenario_resources(db: Store, yuubot_config: BootstrapConfig):
    """Fresh ``Resources`` over an in-memory store, seeded by ``open_resources``."""

    async def _make_store(_):
        return db

    loaded = await open_resources(yuubot_config, create_store=_make_store)
    await loaded.event_bus.start()
    yield loaded
    await loaded.event_bus.stop()
    await loaded.close()


async def test_openai_out_of_box_scenario(scenario_resources, tmp_path: Path) -> None:
    resources = scenario_resources
    app, services = build_resource_daemon_runtime(resources, tmp_path)
    await services.start()
    try:
        async with daemon_http_client(app) as client:
            # 1. Built-in Characters present (general + shiori).
            chars_resp = await client.get("/api/resources/characters")
            assert chars_resp.status_code == 200, chars_resp.text
            char_ids = {c["id"] for c in chars_resp.json()["data"]}
            assert {GENERAL_CHARACTER_ID, SHIORI_CHARACTER_ID} <= char_ids

            # 2. Built-in CapabilitySets present (general + shiori).
            caps_resp = await client.get("/api/resources/capability-sets")
            assert caps_resp.status_code == 200, caps_resp.text
            cap_ids = {c["id"] for c in caps_resp.json()["data"]}
            assert {GENERAL_CAPABILITY_ID, SHIORI_CAPABILITY_ID} <= cap_ids

            # 3. Create OpenAI backend with preset metadata + only the api_key.
            backend_resp = await client.post(
                "/api/resources/llm-backends",
                json={
                    "name": "openai-out-of-box",
                    "yuuagents_provider": "openai",
                    "provider_options": {
                        "provider_name": "openai",
                        "api_key": "sk-test-out-of-box",
                    },
                    "model_capabilities": {"chat": True},
                    "models": {"names": []},
                    "pricing": {"entries": []},
                    "budget": {},
                    "default_model": "",
                },
            )
            assert backend_resp.status_code == 201, backend_resp.text
            backend = backend_resp.json()["data"]

            # Catalogue default filled in; pricing has an entry for it.
            default_model = backend["default_model"]
            assert default_model, "backend default_model must be filled by catalogue"
            pricing_models = {
                entry["model"] for entry in backend["pricing"]["entries"]
            }
            assert default_model in pricing_models, (
                f"pricing missing entry for default model {default_model!r}"
            )
            backend_id = backend["id"]

            # 4. Mint the two preset Actors bound to the new backend.
            created_actor_ids: list[str] = []
            for character_id, capability_id, actor_name in (
                (GENERAL_CHARACTER_ID, GENERAL_CAPABILITY_ID, "general-actor"),
                (SHIORI_CHARACTER_ID, SHIORI_CAPABILITY_ID, "shiori-actor"),
            ):
                actor_resp = await client.post(
                    "/api/resources/actors",
                    json={
                        "name": actor_name,
                        "type": "fake",
                        "default_character_id": character_id,
                        "capability_set_id": capability_id,
                        "default_llm_backend_id": backend_id,
                        "default_model": default_model,
                        "default_budget": {
                            "max_steps": 6,
                            "max_tokens": 8192,
                            "max_usd": 2.0,
                        },
                    },
                )
                assert actor_resp.status_code == 201, actor_resp.text
                actor = actor_resp.json()["data"]
                assert actor["default_budget"]["max_usd"] == 2.0
                created_actor_ids.append(actor["id"])

            # 5. Both Actors visible and point at the created backend.
            actors_resp = await client.get("/api/resources/actors")
            assert actors_resp.status_code == 200, actors_resp.text
            actors = {a["id"]: a for a in actors_resp.json()["data"]}
            for actor_id in created_actor_ids:
                assert actor_id in actors, f"actor {actor_id!r} missing from list"
                assert (
                    actors[actor_id]["default_llm_backend"]["id"] == backend_id
                ), f"actor {actor_id!r} not bound to created backend"
    finally:
        await services.stop()


def test_openai_provider_key_accepted_without_config_entry() -> None:
    """The ``openai`` runtime provider key resolves as built-in.

    Resolution must not depend on a local ``config.yaml`` provider entry: it
    is a built-in accepted factory name. This exercises the same path the
    daemon wiring feeds into ``Stage`` (``_resolve_yuuagents_provider``) and
    the public ``provider_key_for_backend`` that builds the LLM
    ``ProviderPool`` — neither takes any provider registry/config argument.
    """
    # Empty provider_options + no external registry -> resolution still succeeds.
    backend = LLMBackendRecord(
        name="probe",
        yuuagents_provider="openai",
        model_capabilities=ModelCapabilities(),
        models=ModelCatalog(),
        pricing=PricingTable(),
        budget=BudgetPolicy(),
    )

    # Resolution path used by the actor assembly ``Stage`` wiring.
    assert _resolve_yuuagents_provider("openai") == "openai"
    # Public provider-key builder used to construct the LLM ProviderPool.
    assert provider_key_for_backend(backend) == "openai"

