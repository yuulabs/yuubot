from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from support.api import base_url, running_server
from yuubot import Yuubot
from yuubot.actor.prompt import developer_prompt
from yuubot.domain.records import ActorRecord
from yuubot.runtime.skills import SkillCreateInput, SkillRecord, _package_command, search_skills, set_workspace_skill_loaded, workspace_skills


@pytest.mark.asyncio
async def test_global_skill_is_not_added_to_actor_prompt(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    record = await app.create_skill(
        SkillCreateInput(
            "research-plan",
            "Research Plan",
            "Plan research work.",
            "# Research Plan\nNever put this full private instruction in the prompt.",
        )
    )

    prompt = developer_prompt("", tmp_path, [], has_python=True)

    assert record.id not in prompt
    assert "Plan research work." not in prompt
    assert "Never put this full private instruction" not in prompt


@pytest.mark.asyncio
async def test_yb_skills_facade_reads_full_body_on_demand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import yb.skills

    app = await Yuubot.create(tmp_path / "data")
    await app.create_skill(
        SkillCreateInput(
            "ops",
            "Ops",
            "Operational procedure.",
            "# Ops\nFollow the detailed steps.",
        )
    )

    async with running_server(app) as server:
        monkeypatch.setenv("YUUBOT_DAEMON_URL", base_url(server))
        summaries = await yb.skills.list_skills()
        body = await yb.skills.read("ops")

    assert ("ops", "Ops") in [(item.id, item.name) for item in summaries]
    assert {item.id for item in summaries} >= {"artifact-web", "explain"}
    assert body == "# Ops\nFollow the detailed steps."


@pytest.mark.asyncio
async def test_skill_records_persist_across_app_reload(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    app = await Yuubot.create(data_dir)
    await app.create_skill(SkillCreateInput("persisted", "Persisted", body="Persist me."))
    await app.shutdown()

    reloaded = await Yuubot.create(data_dir)

    assert "persisted" in {item.id for item in reloaded.skill_summaries()}
    assert reloaded.runtime.skills["persisted"].body == "Persist me."


@pytest.mark.asyncio
async def test_builtin_skills_are_available_and_deletion_persists(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    app = await Yuubot.create(data_dir)

    assert {"artifact-web", "explain"} <= set(app.runtime.skills)
    assert await app.delete_skill("artifact-web")
    await app.shutdown()

    reloaded = await Yuubot.create(data_dir)
    assert "artifact-web" not in reloaded.runtime.skills
    assert "explain" in reloaded.runtime.skills


@pytest.mark.asyncio
async def test_skill_copy_copies_directory_then_conflicts_and_replaces(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    app.actor_records["amy"] = ActorRecord(
        id="amy",
        name="Amy",
        workspace=str(workspace),
        model="fake",
    )

    preview = app.skill_copy_preview("explain", "amy")
    assert preview.path == ".agents/skills/explain"
    assert not preview.exists

    copied = app.copy_skill("explain", "amy", False)
    target = workspace / copied.path / "SKILL.md"
    assert copied.exists
    assert "name: explain" in target.read_text(encoding="utf-8")

    target.write_text(target.read_text(encoding="utf-8") + "\nLocal change.\n", encoding="utf-8")
    conflict = app.skill_copy_preview("explain", "amy")
    assert conflict.conflict
    assert "Local change." in next(item.diff for item in conflict.files if item.path == "SKILL.md")
    with pytest.raises(FileExistsError):
        app.copy_skill("explain", "amy", False)

    app.copy_skill("explain", "amy", True)
    assert "Local change." not in target.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_deleting_global_skill_preserves_workspace_copy(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    app.actor_records["amy"] = ActorRecord(
        id="amy",
        name="Amy",
        workspace=str(workspace),
        model="fake",
    )
    copied = app.copy_skill("explain", "amy", False)
    target = workspace / copied.path

    assert await app.delete_skill("explain")
    assert target.exists()


@pytest.mark.asyncio
async def test_package_skill_is_not_in_prompt_and_is_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import yb.skills

    app = await Yuubot.create(tmp_path / "data")
    root = tmp_path / "package-skill"
    root.mkdir()
    body = "---\nname: Package Ops\ndescription: Package procedure.\n---\n# Package Ops\nFull steps.\n"
    (root / "SKILL.md").write_text(body, encoding="utf-8")
    app.runtime.package_skills = [
        SkillRecord("package-ops", "Package Ops", "Package procedure.", body, source="package", source_path=str(root))
    ]

    prompt = developer_prompt("", tmp_path, [], has_python=True)
    async with running_server(app) as server:
        monkeypatch.setenv("YUUBOT_DAEMON_URL", base_url(server))
        loaded = await yb.skills.read("package-ops")

    assert "package-ops" not in prompt
    assert loaded == body


@pytest.mark.asyncio
async def test_duplicate_id_is_visible_but_excluded_from_prompt_and_read(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    custom = await app.create_skill(SkillCreateInput("duplicate", "Custom duplicate", body="Custom"))
    app.runtime.package_skills = [
        SkillRecord("duplicate", "Package duplicate", body="Package", source="package", source_path=str(tmp_path))
    ]

    entries = [item for item in app.skill_catalog() if item.id == "duplicate"]
    prompt = developer_prompt("", tmp_path, [], has_python=True)

    assert custom.id == "duplicate"
    assert len(entries) == 2
    assert all(item.error and not item.can_copy for item in entries)
    assert "Custom duplicate" not in prompt
    assert "Package duplicate" not in prompt
    with pytest.raises(KeyError):
        app.runtime.skill_record("duplicate")


def test_workspace_skill_loaded_state_preserves_document(tmp_path: Path) -> None:
    path = tmp_path / ".agents/skills/explain/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: Explain\ndescription: Keep this.\ncustom: value\n---\n# Body\nSteps.\n")

    assert "Keep this." in developer_prompt("", tmp_path, [], has_python=True)
    set_workspace_skill_loaded(tmp_path, "explain", False)
    assert "Keep this." not in developer_prompt("", tmp_path, [], has_python=True)
    assert "custom: value" in path.read_text()
    assert "# Body\nSteps." in path.read_text()
    set_workspace_skill_loaded(tmp_path, "explain", True)
    assert workspace_skills(tmp_path)[0].loaded is True


def test_skill_search_includes_global_and_banned_workspace_without_bodies(tmp_path: Path) -> None:
    path = tmp_path / ".agents/skills/local/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: Local Research\ndescription: Local summary.\nloaded: false\n---\nSECRET BODY research\n")
    global_record = SkillRecord("global-research", "Global Research", "Global summary.", "SECRET global research")

    results = search_skills("research", 5, [global_record], tmp_path)

    assert {item.source for item in results} == {"global", "workspace"}
    assert next(item for item in results if item.source == "workspace").loaded is False
    assert all("SECRET" not in repr(item) for item in results)


@pytest.mark.asyncio
async def test_copy_includes_resources_and_rejects_escaping_symlink(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    source = tmp_path / "package"
    (source / "references").mkdir(parents=True)
    (source / "SKILL.md").write_text("# Package", encoding="utf-8")
    (source / "references" / "guide.txt").write_text("guide", encoding="utf-8")
    app.runtime.package_skills = [
        SkillRecord("package", "Package", body="# Package", source="package", source_path=str(source))
    ]
    workspace = tmp_path / "workspace"
    app.actor_records["amy"] = ActorRecord(id="amy", name="Amy", workspace=str(workspace), model="fake")

    app.copy_skill("package", "amy", False)
    assert (workspace / ".agents/skills/package/references/guide.txt").read_text() == "guide"

    (tmp_path / "outside").write_text("outside", encoding="utf-8")
    (source / "unsafe").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="escapes root"):
        app.skill_copy_preview("package", "amy")


@pytest.mark.asyncio
async def test_edited_builtin_body_is_used_when_copying(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    original = app.runtime.skills["explain"]
    await app.put_skill(SkillRecord("explain", "Edited explain", "Edited", "# Edited body"))
    workspace = tmp_path / "workspace"
    app.actor_records["amy"] = ActorRecord(id="amy", name="Amy", workspace=str(workspace), model="fake")

    app.copy_skill("explain", "amy", False)

    assert original.source == "builtin"
    copied = (workspace / ".agents/skills/explain/SKILL.md").read_text()
    assert "loaded: true" in copied
    assert copied.endswith("# Edited body\n")


def test_package_add_command_has_global_yes_and_options() -> None:
    from yuubot.runtime.skills import SkillPackageBody

    command = _package_command(
        "add",
        "repo",
        SkillPackageBody("org/repo", ("one",), ("codex",), True),
    )

    assert command == (
        "npx", "-y", "skills", "add", "org/repo", "--skill", "one",
        "--agent", "codex", "--copy", "--global", "--yes",
    )
    assert _package_command("update", "one", None) == (
        "npx", "-y", "skills", "update", "one", "--global", "--yes",
    )
    assert _package_command("update", "", None) == (
        "npx", "-y", "skills", "update", "--global", "--yes",
    )
    assert _package_command("remove", "one", None) == (
        "npx", "-y", "skills", "remove", "one", "--global", "--yes",
    )


@pytest.mark.asyncio
async def test_discovery_failure_keeps_previous_package_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = await Yuubot.create(tmp_path / "data")
    previous = SkillRecord("kept", "Kept", body="Keep", source="package", source_path=str(tmp_path))
    app.runtime.package_skills = [previous]

    async def failed(_force: bool = True):
        return None, "discovery unavailable"

    monkeypatch.setattr("yuubot.app.service.discover_package_skills", failed)
    warning = await app.refresh_package_skills()

    assert app.runtime.package_skills == [previous]
    assert warning == "discovery unavailable"


@pytest.mark.asyncio
async def test_package_delete_runs_remove_and_refreshes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from yuubot.runtime.skills import SkillPackageResult

    app = await Yuubot.create(tmp_path / "data")
    package = SkillRecord("remove-me", "Remove me", body="Package", source="package", source_path=str(tmp_path))
    app.runtime.package_skills = [package]
    calls: list[tuple[str, str]] = []

    async def command(action, target="", body=None):
        calls.append((action, target))
        return SkillPackageResult(action, target, ("npx", "skills"), 0)

    async def discover(_force: bool = True):
        return [], ""

    monkeypatch.setattr("yuubot.app.service.run_package_command", command)
    monkeypatch.setattr("yuubot.app.service.discover_package_skills", discover)

    assert await app.delete_skill("remove-me", "package")
    assert calls == [("remove", "remove-me")]
    assert app.runtime.package_skills == []


@pytest.mark.asyncio
async def test_skill_crud_and_copy_api(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    workspace = tmp_path / "workspace"
    app.actor_records["amy"] = ActorRecord(id="amy", name="Amy", workspace=str(workspace), model="fake")

    async with running_server(app) as server:
        async with httpx.AsyncClient(base_url=base_url(server)) as client:
            created = await client.post("/api/skills", json={"id": "api-skill", "name": "API Skill", "body": "# API"})
            listed = await client.get("/api/skills")
            edited = await client.put("/api/skills/api-skill", json={"name": "Edited", "body": "# Edited"})
            preview = await client.get("/api/skills/api-skill/copy-preview", params={"actor_id": "amy"})
            copied = await client.post("/api/skills/api-skill/copy", json={"actor_id": "amy", "replace": False})
            deleted = await client.delete("/api/skills/api-skill", params={"source": "custom"})

    assert created.status_code == 201
    assert any(item["id"] == "api-skill" for item in listed.json()["items"])
    assert edited.json()["record"]["name"] == "Edited"
    assert preview.json()["path"] == ".agents/skills/api-skill"
    assert copied.json()["up_to_date"]
    assert deleted.json()["deleted"] is True


@pytest.mark.asyncio
async def test_package_add_api_uses_structured_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from yuubot.runtime.skills import SkillPackageResult

    app = await Yuubot.create(tmp_path / "data")
    captured = None

    async def add_package(_self: Yuubot, body):
        nonlocal captured
        captured = body
        return SkillPackageResult("add", body.source, ("npx", "skills"), 0)

    monkeypatch.setattr(Yuubot, "add_skill_package", add_package)
    async with running_server(app) as server:
        async with httpx.AsyncClient(base_url=base_url(server)) as client:
            response = await client.post(
                "/api/skills/packages",
                json={"source": "org/repo", "skills": ["one"], "agents": ["codex"], "copy": True},
            )

    assert response.status_code == 200
    assert captured.source == "org/repo"
    assert captured.skills == ("one",)
    assert captured.copy is True
