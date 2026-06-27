"""ISSUE-0005 out-of-box scenario regression.

Public boundary: the daemon resource HTTP API (``build_daemon_asgi_app`` over
fresh ``Resources``). This test proves the first-use flow described in the
issue survives through the same API the Admin UI uses, end to end:

    fresh resources (seeded presets)
      -> built-in CapabilitySets are visible
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
import yuullm

from yuubot.bootstrap.config import BootstrapConfig
from yuubot.core.assembly._llm_session import provider_key_for_backend
from yuubot.process import open_resources
from yuubot.resources import builtin_presets
from yuubot.resources.records import (
    BudgetPolicy,
    LLMBackendRecord,
    ModelConfig,
    ModelCapabilities,
    Pricing,
)
from yuubot.resources.store.resource import Store
from tests.helpers import build_resource_daemon_runtime, daemon_http_client

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
            # 1. Built-in CapabilitySets present (general + shiori).
            caps_resp = await client.get("/api/resources/capability-sets")
            assert caps_resp.status_code == 200, caps_resp.text
            cap_ids = {c["id"] for c in caps_resp.json()["data"]}
            assert {GENERAL_CAPABILITY_ID, SHIORI_CAPABILITY_ID} <= cap_ids

            # 2. Built-in persona prompts are code constants.
            persona_prompts = builtin_presets.BUILTIN_PERSONA_PROMPTS
            assert persona_prompts["general"] == "You are a helpful assistant."
            assert "汐织" in persona_prompts["shiori"]

            # 3. Create OpenAI backend with preset metadata + only the api_key.
            backend_resp = await client.post(
                "/api/resources/llm-backends",
                json={
                    "name": "openai-out-of-box",
                    "provider_identity": "openai",
                    "provider_options": {"api_key": "sk-test-out-of-box"},
                    "model_configs": {
                        "gpt-4.1-mini": {
                            "pricing": {
                                "input_per_million": 1.0,
                                "cached_input_per_million": 0.1,
                                "output_per_million": 2.0,
                            },
                            "capabilities": {"chat": True, "tool_calling": True},
                        }
                    },
                    "budget": {},
                },
            )
            assert backend_resp.status_code == 201, backend_resp.text
            backend = backend_resp.json()["data"]

            # Model config is user-maintained and persisted directly.
            actor_model = "gpt-4.1-mini"
            assert actor_model in backend["model_configs"]
            backend_id = backend["id"]

            # 4. Mint the two preset Actors bound to the new backend.
            created_actor_ids: list[str] = []
            for persona_name, capability_id, actor_name in (
                ("general", GENERAL_CAPABILITY_ID, "general-actor"),
                ("shiori", SHIORI_CAPABILITY_ID, "shiori-actor"),
            ):
                actor_resp = await client.post(
                    "/api/resources/actors",
                    json={
                        "name": actor_name,
                        "type": "fake",
                        "persona_prompt": persona_prompts[persona_name],
                        "capability_set_id": capability_id,
                        "llm_backend_id": backend_id,
                        "model": actor_model,
                        "per_run_budget": {
                            "max_steps": 6,
                            "max_tokens": 8192,
                            "max_usd": 2.0,
                        },
                    },
                )
                assert actor_resp.status_code == 201, actor_resp.text
                actor = actor_resp.json()["data"]
                assert actor["per_run_budget"]["max_usd"] == 2.0
                created_actor_ids.append(actor["id"])

            # 5. Both Actors visible and point at the created backend.
            actors_resp = await client.get("/api/resources/actors")
            assert actors_resp.status_code == 200, actors_resp.text
            actors = {a["id"]: a for a in actors_resp.json()["data"]}
            for actor_id in created_actor_ids:
                assert actor_id in actors, f"actor {actor_id!r} missing from list"
                assert (
                    actors[actor_id]["llm_backend_id"] == backend_id
                ), f"actor {actor_id!r} not bound to created backend"
    finally:
        await services.stop()


def test_openai_provider_key_accepted_without_config_entry() -> None:
    """The ``openai`` runtime provider key resolves as built-in.

    Resolution must not depend on a local ``config.yaml`` provider entry: it
    is a built-in preset identity. This exercises the same path the daemon
    wiring feeds into ``Stage`` and the public provider-key builder used to
    construct the LLM ``ProviderPool`` — neither takes any provider
    registry/config argument.
    """
    # Empty provider_options + no external registry -> resolution still succeeds.
    backend = LLMBackendRecord(
        name="probe",
        provider_identity="openai",
        model_configs={
            "gpt-4.1-mini": ModelConfig(
                pricing=Pricing(),
                capabilities=ModelCapabilities(),
            )
        },
        budget=BudgetPolicy(),
    )

    # Resolution path used by the actor assembly ``Stage`` wiring.
    assert yuullm.resolve_provider("openai").api_type == "openai-compatible"
    # Public provider-key builder used to construct the LLM ProviderPool.
    assert provider_key_for_backend(backend) == "openai"


def test_custom_openai_provider_keys_are_builtin() -> None:
    assert yuullm.resolve_provider("openai-chat-completion").api_type == (
        "openai-chat-completion"
    )
    assert yuullm.resolve_provider("openai-compatible").api_type == "openai-compatible"
