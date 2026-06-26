"""Install-time seed behavior for built-in preset records.

Public boundary: ``yuubot.process.open_resources`` seeds stable preset
``Character`` and ``CapabilitySet`` records idempotently into whatever store
it is handed. These tests prove the seed runs on a fresh store, is idempotent
on reopen, and does not overwrite user edits to seeded records.
"""

from __future__ import annotations

from yuubot.bootstrap.config import BootstrapConfig
from yuubot.process import open_resources
from yuubot.resources.store.models import ActorORM, CapabilitySetORM, CharacterORM
from yuubot.resources.store.resource import Store

GENERAL_CHARACTER_ID = "builtin-character-general"
SHIORI_CHARACTER_ID = "builtin-character-shiori"
GENERAL_CAPABILITY_ID = "builtin-capability-general"
SHIORI_CAPABILITY_ID = "builtin-capability-shiori"


async def _open(store: Store, config: BootstrapConfig):
    async def _make_store(_):
        return store

    return await open_resources(config, create_store=_make_store)


async def test_open_resources_seeds_preset_characters_and_capability_sets(
    db: Store, yuubot_config: BootstrapConfig
) -> None:
    resources = await _open(db, yuubot_config)

    characters = await resources.repository.list(CharacterORM)
    by_id = {c.id: c for c in characters}
    assert set(by_id) >= {GENERAL_CHARACTER_ID, SHIORI_CHARACTER_ID}

    general_char = by_id[GENERAL_CHARACTER_ID]
    assert general_char.name == "general"
    assert general_char.system_prompt == "You are a helpful assistant."
    assert general_char.is_builtin is True
    assert general_char.facade_module == "yb"

    shiori_char = by_id[SHIORI_CHARACTER_ID]
    assert shiori_char.name == "shiori"
    # Shiori persona markers from the locked character prompt.
    assert "汐织" in shiori_char.system_prompt
    assert "Scenario Communication" in shiori_char.system_prompt
    assert shiori_char.is_builtin is True

    capability_sets = await resources.repository.list(CapabilitySetORM)
    caps_by_id = {c.id: c for c in capability_sets}
    assert set(caps_by_id) >= {GENERAL_CAPABILITY_ID, SHIORI_CAPABILITY_ID}

    general_cap = caps_by_id[GENERAL_CAPABILITY_ID]
    assert general_cap.name == "general"
    assert general_cap.workspace_path == "general"

    shiori_cap = caps_by_id[SHIORI_CAPABILITY_ID]
    assert shiori_cap.name == "shiori"
    assert shiori_cap.workspace_path == "shiori"


async def test_open_resources_seeds_exactly_two_preset_records_per_type(
    db: Store, yuubot_config: BootstrapConfig
) -> None:
    """Fresh seed produces exactly the two built-in presets, no duplicates."""
    resources = await _open(db, yuubot_config)

    characters = await resources.repository.list(CharacterORM)
    builtin_characters = [c for c in characters if c.is_builtin]
    assert len(builtin_characters) == 2
    assert {c.name for c in builtin_characters} == {"general", "shiori"}

    capability_sets = await resources.repository.list(CapabilitySetORM)
    builtin_caps = [c for c in capability_sets if c.id.startswith("builtin-capability-")]
    assert len(builtin_caps) == 2
    assert {c.name for c in builtin_caps} == {"general", "shiori"}


async def test_reopen_resources_is_idempotent_and_preserves_user_edits(
    db: Store, yuubot_config: BootstrapConfig
) -> None:
    """A second open_resources over the same store must not duplicate records
    and must not overwrite content the user edited."""
    first = await _open(db, yuubot_config)
    edited_prompt = "edited by user — should survive reopen"
    updated = await first.repository.update(
        CharacterORM,
        GENERAL_CHARACTER_ID,
        system_prompt=edited_prompt,
        description="user edited description",
    )
    assert updated is not None
    assert updated.system_prompt == edited_prompt

    # Reopen over the same store; seeding must not clobber the edit or duplicate.
    second = await _open(db, yuubot_config)
    characters = await second.repository.list(CharacterORM)
    general_characters = [c for c in characters if c.name == "general"]
    assert len(general_characters) == 1
    assert general_characters[0].id == GENERAL_CHARACTER_ID
    assert general_characters[0].system_prompt == edited_prompt

    capability_sets = await second.repository.list(CapabilitySetORM)
    assert len([c for c in capability_sets if c.name == "shiori"]) == 1
    assert len([c for c in capability_sets if c.name == "general"]) == 1


async def test_open_resources_does_not_seed_actors(
    db: Store, yuubot_config: BootstrapConfig
) -> None:
    """Seeding must not create Actor records (preset Actors need a backend)."""
    resources = await _open(db, yuubot_config)
    actors = await resources.repository.list(ActorORM)
    assert actors == ()


async def test_version_bump_refreshes_builtin_character_content(
    db: Store, yuubot_config: BootstrapConfig
) -> None:
    """When the source preset version advances, the next seed refreshes the
    existing builtin Character's content fields to source.

    Simulates a future maintainer bumping a preset version: the persisted
    builtin Character carries an older version + stale content; reopening must
    overwrite the managed content fields (not actor-bound / user-cloned data).
    User-cloned (is_builtin=false) Characters are never touched.
    """
    from yuubot.resources import builtin_presets

    first = await _open(db, yuubot_config)
    # Simulate a stale install: old version + old content persisted.
    await first.repository.update(
        CharacterORM,
        GENERAL_CHARACTER_ID,
        system_prompt="stale old prompt",
        description="stale description",
        builtin_version="general-v0",
    )
    # A user-cloned character sharing the builtin id namespace must be left
    # alone even if its version looks stale — only is_builtin records update.
    await first.repository.update(
        CharacterORM,
        SHIORI_CHARACTER_ID,
        is_builtin=False,
        builtin_version="ancient",
        system_prompt="user-customized shiori prompt",
    )

    second = await _open(db, yuubot_config)
    characters = {c.id: c for c in await second.repository.list(CharacterORM)}

    # Stale builtin general refreshed to source content + current version.
    general = characters[GENERAL_CHARACTER_ID]
    assert general.is_builtin is True
    assert general.system_prompt == "You are a helpful assistant."
    assert general.description == "Preset general assistant"
    assert general.builtin_version == builtin_presets.GENERAL_PRESET_VERSION

    # User-cloned (is_builtin=false) shiori left untouched.
    shiori = characters[SHIORI_CHARACTER_ID]
    assert shiori.is_builtin is False
    assert shiori.system_prompt == "user-customized shiori prompt"
    assert shiori.builtin_version == "ancient"
