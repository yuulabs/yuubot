from __future__ import annotations

from pathlib import Path

import pytest

from support.api import base_url, running_server
from yuubot import Yuubot
from yuubot.actor.prompt import developer_prompt
from yuubot.runtime.skills import SkillRecord, _skill_cli_command


@pytest.mark.asyncio
async def test_global_skill_prompt_lists_summary_not_body(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    record = await app.put_skill(
        SkillRecord(
            id="research-plan",
            name="Research Plan",
            description="Plan research work.",
            body="# Research Plan\nNever put this full private instruction in the prompt.",
        )
    )

    prompt = developer_prompt(
        "",
        tmp_path,
        [],
        has_python=True,
        global_skills=app.runtime.skill_summaries(),
    )

    assert record.id in prompt
    assert "Plan research work." in prompt
    assert "await yb.skills.read('research-plan')" in prompt
    assert "Never put this full private instruction" not in prompt


@pytest.mark.asyncio
async def test_yb_skills_facade_reads_full_body_on_demand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import yb.skills

    app = await Yuubot.create(tmp_path / "data")
    await app.put_skill(
        SkillRecord(
            id="ops",
            name="Ops",
            description="Operational procedure.",
            body="# Ops\nFollow the detailed steps.",
        )
    )

    async with running_server(app) as server:
        monkeypatch.setenv("YUUBOT_DAEMON_URL", base_url(server))
        summaries = await yb.skills.list_skills()
        body = await yb.skills.read("ops")

    assert [(item.id, item.name) for item in summaries] == [("ops", "Ops")]
    assert body == "# Ops\nFollow the detailed steps."


@pytest.mark.asyncio
async def test_skill_records_persist_across_app_reload(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    app = await Yuubot.create(data_dir)
    await app.put_skill(SkillRecord(id="persisted", name="Persisted", body="Persist me."))
    await app.shutdown()

    reloaded = await Yuubot.create(data_dir)

    assert reloaded.skill_summaries()[0].id == "persisted"
    assert reloaded.runtime.skills["persisted"].body == "Persist me."


def test_skill_cli_commands_target_global_scope() -> None:
    assert _skill_cli_command("add", "vercel-labs/skills") == (
        "npx",
        "-y",
        "skills",
        "add",
        "-g",
        "-y",
        "vercel-labs/skills",
    )
    assert _skill_cli_command("remove", "frontend-design") == (
        "npx",
        "-y",
        "skills",
        "remove",
        "-g",
        "-y",
        "frontend-design",
    )
    assert _skill_cli_command("update", "frontend-design") == (
        "npx",
        "-y",
        "skills",
        "update",
        "-g",
        "frontend-design",
    )
