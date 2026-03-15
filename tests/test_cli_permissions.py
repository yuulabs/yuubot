import attrs
from click.testing import CliRunner

from yuubot.cli import cli, hhsh, im, mem, schedule, vision, web


def test_capability_cli_executes_via_generic_runner(monkeypatch):
    captured = {}

    async def fake_execute(command, *, context=None):
        captured["command"] = command
        captured["context"] = context
        return [{"type": "text", "text": "ok"}]

    monkeypatch.setattr("yuubot.capabilities.execute", fake_execute)
    monkeypatch.setattr(
        "yuubot.config.load_config",
        lambda _path: attrs.make_class(
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
        )(),
    )

    async def fake_init_db(*_args, **_kwargs):
        return None

    async def fake_close_db():
        return None

    monkeypatch.setattr("yuubot.core.db.init_db", fake_init_db)
    monkeypatch.setattr("yuubot.core.db.close_db", fake_close_db)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["im", "send", "--ctx", "3", "--", '[{"type":"text","data":{"text":"hi"}}]'],
    )

    assert result.exit_code == 0
    assert captured["command"] == 'im send --ctx 3 -- [{"type":"text","data":{"text":"hi"}}]'


def test_capability_cli_rejects_missing_explicit_payload(monkeypatch):
    monkeypatch.setattr(
        "yuubot.config.load_config",
        lambda _path: attrs.make_class(
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
        )(),
    )

    async def fake_init_db(*_args, **_kwargs):
        return None

    async def fake_close_db():
        return None

    monkeypatch.setattr("yuubot.core.db.init_db", fake_init_db)
    monkeypatch.setattr("yuubot.core.db.close_db", fake_close_db)

    runner = CliRunner()
    result = runner.invoke(cli, ["im", "send", "--ctx", "3"])

    assert result.exit_code != 0
    assert "requires JSON payload after '--'" in result.output


def test_capability_cli_commands_are_auto_registered():
    assert "restore" in mem.commands
    assert "list" in mem.commands
    assert "search" in web.commands
    assert "send" in im.commands
    assert "guess" in hhsh.commands
    assert "create" in schedule.commands
    assert "describe" in vision.commands


def test_manual_capability_cli_extensions_are_kept():
    assert "login" in web.commands
    assert "login" in im.commands


def test_capability_cli_uses_contract_action_names():
    assert sorted(mem.commands) == ["config", "delete", "list", "recall", "restore", "save", "show"]


def test_mem_list_uses_pager_and_is_hidden_in_bot(monkeypatch):
    monkeypatch.setattr(
        "yuubot.config.load_config",
        lambda _path: attrs.make_class(
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
        )(),
    )

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
    monkeypatch.setattr(
        "yuubot.config.load_config",
        lambda _path: attrs.make_class(
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
        )(),
    )

    async def fake_init_db(*_args, **_kwargs):
        return None

    async def fake_close_db():
        return None

    restored = {}

    async def fake_restore(ids):
        restored["ids"] = ids
        return len(ids)

    monkeypatch.setattr("yuubot.core.db.init_db", fake_init_db)
    monkeypatch.setattr("yuubot.core.db.close_db", fake_close_db)
    monkeypatch.setattr("yuubot.capabilities.mem.store.restore", fake_restore)

    runner = CliRunner()
    result = runner.invoke(cli, ["mem", "restore", "1", "2,3"])

    assert result.exit_code == 0
    assert restored["ids"] == [1, 2, 3]
    assert "已恢复 3 条记忆 (ID: 1, 2, 3)" in result.output
