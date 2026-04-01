"""Public CLI behavior tests."""

from __future__ import annotations

import attrs
from click.testing import CliRunner

from yuubot.cli import cli


def _fake_config():
    return attrs.make_class(
        "Cfg",
        {
            "database": attrs.field(
                default=attrs.make_class(
                    "DbCfg",
                    {
                        "path": attrs.field(default=":memory:"),
                        "simple_ext": attrs.field(default=""),
                    },
                )()
            ),
        },
    )()


def test_capability_cli_executes_user_command(monkeypatch):
    captured = {}

    async def fake_execute(command, *, context=None):
        captured["command"] = command
        captured["context"] = context
        return [{"type": "text", "text": "ok"}]

    async def fake_init_db(*_args, **_kwargs):
        return None

    async def fake_close_db():
        return None

    monkeypatch.setattr("yuubot.capabilities.execute", fake_execute)
    monkeypatch.setattr("yuubot.config.load_config", lambda _path: _fake_config())
    monkeypatch.setattr("yuubot.core.db.init_db", fake_init_db)
    monkeypatch.setattr("yuubot.core.db.close_db", fake_close_db)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["im", "send", "--ctx", "3", "--", '[{"type":"text","data":{"text":"hi"}}]'],
    )

    assert result.exit_code == 0
    assert captured["command"] == 'im send --ctx 3 -- [{"type":"text","data":{"text":"hi"}}]'
    assert result.output.strip() == "ok"


def test_capability_cli_rejects_missing_payload(monkeypatch):
    async def fake_init_db(*_args, **_kwargs):
        return None

    async def fake_close_db():
        return None

    monkeypatch.setattr("yuubot.config.load_config", lambda _path: _fake_config())
    monkeypatch.setattr("yuubot.core.db.init_db", fake_init_db)
    monkeypatch.setattr("yuubot.core.db.close_db", fake_close_db)

    runner = CliRunner()
    result = runner.invoke(cli, ["im", "send", "--ctx", "3"])

    assert result.exit_code != 0
    assert "requires JSON payload after '--'" in result.output


def test_capability_help_exposes_human_facing_commands():
    runner = CliRunner()

    im_help = runner.invoke(cli, ["im", "--help"])
    web_help = runner.invoke(cli, ["web", "--help"])

    assert im_help.exit_code == 0
    assert "send" in im_help.output
    assert "login" in im_help.output
    assert web_help.exit_code == 0
    assert "search" in web_help.output
    assert "login" in web_help.output


def test_mem_list_is_hidden_in_bot_help_and_uses_pager(monkeypatch):
    async def fake_init_db(*_args, **_kwargs):
        return None

    async def fake_close_db():
        return None

    async def fake_list_memories(*_args, **kwargs):
        assert kwargs["show_all"] is True
        assert kwargs["trash"] is False
        return [{
            "id": 7,
            "scope": "public",
            "ctx_id": None,
            "tags": "ops",
            "content": "touch me",
            "created_at": "2026-03-15T00:00:00+00:00",
            "last_accessed": "2026-03-15T01:00:00+00:00",
            "trashed_at": "",
        }]

    paged = {}

    monkeypatch.setattr("yuubot.config.load_config", lambda _path: _fake_config())
    monkeypatch.setattr("yuubot.core.db.init_db", fake_init_db)
    monkeypatch.setattr("yuubot.core.db.close_db", fake_close_db)
    monkeypatch.setattr("yuubot.capabilities.mem.store.list_memories", fake_list_memories)
    monkeypatch.setattr("click.echo_via_pager", lambda text: paged.setdefault("text", text))

    runner = CliRunner()
    result = runner.invoke(cli, ["mem", "list"], env={"YUU_IN_BOT": "1"})
    help_result = runner.invoke(cli, ["mem", "--help"], env={"YUU_IN_BOT": "1"})

    assert result.exit_code == 0
    assert help_result.exit_code == 0
    assert "list" not in help_result.output
    assert "[mem 7]" in paged["text"]
    assert "touch me" in paged["text"]


def test_mem_restore_accepts_multiple_ids(monkeypatch):
    async def fake_init_db(*_args, **_kwargs):
        return None

    async def fake_close_db():
        return None

    restored = {}

    async def fake_restore(ids):
        restored["ids"] = ids
        return len(ids)

    monkeypatch.setattr("yuubot.config.load_config", lambda _path: _fake_config())
    monkeypatch.setattr("yuubot.core.db.init_db", fake_init_db)
    monkeypatch.setattr("yuubot.core.db.close_db", fake_close_db)
    monkeypatch.setattr("yuubot.capabilities.mem.store.restore", fake_restore)

    runner = CliRunner()
    result = runner.invoke(cli, ["mem", "restore", "1", "2,3"])

    assert result.exit_code == 0
    assert restored["ids"] == [1, 2, 3]
    assert "已恢复 3 条记忆 (ID: 1, 2, 3)" in result.output
