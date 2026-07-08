from __future__ import annotations

from pathlib import Path

import pytest

from yuubot.actor.prompt import developer_prompt
from yuubot import Yuubot
from yuubot.integrations import IntegrationRecord
from yuubot.integrations.coding_cli import CodexConfig, CodexIntegration, OpenCodeConfig, OpenCodeIntegration, probe_coding_cli
from yuubot.integrations.registry import default_registry


@pytest.mark.asyncio
async def test_coding_cli_missing_binary_returns_recovery_action() -> None:
    state = await probe_coding_cli(
        CodexConfig(
            command="definitely-not-installed-yuubot-cli",
            probe_args=("auth", "status"),
            login_command="missing login",
        )
    )

    assert state.status == "error"
    assert state.action_hint == {
        "kind": "open_pty",
        "title": "Check definitely-not-installed-yuubot-cli",
        "suggested_command": "missing login",
        "cwd": "~",
    }


@pytest.mark.asyncio
async def test_coding_cli_probe_uses_user_bin_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / ".nvm" / "versions" / "node" / "v99.0.0" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "fake-runtime").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_dir / "fake-cli").write_text("#!/usr/bin/env fake-runtime\n", encoding="utf-8")
    (bin_dir / "fake-runtime").chmod(0o755)
    (bin_dir / "fake-cli").chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    state = await probe_coding_cli(CodexConfig(command="fake-cli", probe_args=("health",)))

    assert state.status == "ready"
    assert state.binary_path == str(bin_dir / "fake-cli")


def test_coding_clis_are_registered_as_integrations() -> None:
    specs = default_registry().specs()

    assert specs["codex"].package_path == "yext.codex"
    assert specs["codex"].config_type is CodexConfig
    assert specs["opencode"].package_path == "yext.opencode"


@pytest.mark.asyncio
async def test_codex_can_enable_without_saved_config(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")

    integration = await app.enable_configured_integration("codex")
    snapshots = await app.integration_snapshots()
    codex = next(item for item in snapshots if item.type == "codex")

    assert integration is not None
    assert integration.name == "codex"
    assert "codex" in app.runtime.integrations
    assert codex.configured is True
    assert codex.enabled is True
    assert codex.config["command"] == "codex"
    assert codex.health_status in {"ready", "error", "needs_action"}


@pytest.mark.asyncio
async def test_required_config_integrations_still_require_saved_config(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")

    integration = await app.enable_configured_integration("github")

    assert integration is None


@pytest.mark.asyncio
async def test_enable_records_missing_binary_health(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    await app.configure_integration(
        IntegrationRecord(
            id="codex",
            type="codex",
            name="codex",
            config={"command": "definitely-not-installed-yuubot-cli", "login_command": "missing login"},
        )
    )

    integration = await app.enable_configured_integration("codex")
    snapshots = await app.integration_snapshots()
    codex = next(item for item in snapshots if item.type == "codex")

    assert integration is not None
    assert codex.enabled is True
    assert codex.health_status == "error"
    assert codex.last_error is not None
    assert codex.last_error.message == "definitely-not-installed-yuubot-cli binary was not found on PATH"
    assert codex.action_hint == {
        "kind": "open_pty",
        "title": "Check definitely-not-installed-yuubot-cli",
        "suggested_command": "missing login",
        "cwd": "~",
    }


@pytest.mark.asyncio
async def test_enable_records_probe_auth_health(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli = bin_dir / "fake-cli"
    cli.write_text("#!/bin/sh\necho 'not logged in' >&2\nexit 1\n", encoding="utf-8")
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    app = await Yuubot.create(tmp_path / "data")
    await app.configure_integration(
        IntegrationRecord(
            id="codex",
            type="codex",
            name="codex",
            config={
                "command": "fake-cli",
                "probe_args": ["auth", "status"],
                "login_command": "fake-cli login",
                "run_args_prefix": [],
            },
        )
    )

    integration = await app.enable_configured_integration("codex")
    snapshots = await app.integration_snapshots()
    codex = next(item for item in snapshots if item.type == "codex")

    assert integration is not None
    assert codex.enabled is True
    assert codex.health_status == "needs_action"
    assert codex.health_reason == "not logged in"
    assert codex.health_details["binary_path"] == str(cli)
    assert codex.action_hint == {
        "kind": "open_pty",
        "title": "Check fake-cli",
        "suggested_command": "fake-cli login",
        "cwd": "~",
    }


@pytest.mark.asyncio
async def test_yext_codex_facade_runs_from_integration_context(monkeypatch: pytest.MonkeyPatch) -> None:
    import yext.codex

    integration = CodexIntegration(
        name="codex",
        config=CodexConfig(command="printf", probe_args=(), run_args_prefix=()),
    )
    for key, value in integration.session_context().items():
        monkeypatch.setenv(key, value)

    state = await yext.codex.status()
    result = await yext.codex.run("coding-ok")

    assert state.status == "ready"
    assert result.exit_code == 0
    assert result.stdout == "coding-ok"


@pytest.mark.asyncio
async def test_yext_opencode_cli_forwards_help_and_subcommands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import yext.opencode

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli = bin_dir / "fake-opencode"
    cli.write_text(
        """#!/bin/sh
if [ "$1" = "help" ]; then
  echo "opencode help manual"
  exit 0
fi
if [ "$1" = "debug" ] && [ "$2" = "config" ]; then
  echo '{"model":"test-model","key":"sk-fake1234567890abcdef"}'
  exit 0
fi
echo "unexpected:$*"
exit 1
""",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("YEXT_OPENCODE_COMMAND", "fake-opencode")
    monkeypatch.setenv("YEXT_OPENCODE_PROBE_ARGS", "[]")

    help_result = await yext.opencode.help()
    config_result = await yext.opencode.cli("debug", "config")

    assert help_result.exit_code == 0
    assert "opencode help manual" in help_result.stdout
    assert config_result.exit_code == 0
    assert "test-model" in config_result.stdout
    assert "sk-fake" not in config_result.stdout
    assert "***" in config_result.stdout


@pytest.mark.asyncio
async def test_yext_opencode_facade_filters_control_sequences(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import yext.opencode

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli = bin_dir / "fake-opencode"
    cli.write_text(
        """#!/bin/sh
if [ "$1" = "providers" ]; then
  printf '\\033[31mneeds login\\033[0m\\n' >&2
  exit 1
fi
printf '\\033[32mok\\033[0m\\n'
printf '\\033]0;ignored title\\007err\\033[0m\\n' >&2
""",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("YEXT_OPENCODE_COMMAND", "fake-opencode")
    monkeypatch.setenv("YEXT_OPENCODE_PROBE_ARGS", '["providers", "list"]')

    state = await yext.opencode.status()
    result = await yext.opencode.cli("run", "hello")

    assert state.status == "needs_action"
    assert state.reason == "needs login"
    assert result.stdout == "ok\n"
    assert result.stderr == "err\n"


def test_yext_coding_cli_facade_does_not_import_daemon_package() -> None:
    import yext._coding_cli

    source = Path(yext._coding_cli.__file__).read_text(encoding="utf-8")

    assert "from yuubot" not in source
    assert "import yuubot" not in source


@pytest.mark.asyncio
async def test_yext_codex_help_forwards_to_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import yext.codex

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli = bin_dir / "fake-codex"
    cli.write_text(
        """#!/bin/sh
if [ "$1" = "help" ] && [ "$2" = "debug" ]; then
  echo "codex debug help"
  exit 0
fi
echo "unexpected:$*"
exit 1
""",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("YEXT_CODEX_COMMAND", "fake-codex")
    monkeypatch.setenv("YEXT_CODEX_PROBE_ARGS", "[]")

    result = await yext.codex.help("debug")

    assert result.exit_code == 0
    assert result.stdout.strip() == "codex debug help"


def test_coding_cli_prompt_doc_contains_usage_guidance() -> None:
    integration = OpenCodeIntegration(name="opencode", config=OpenCodeConfig())
    doc = integration.prompt_doc()

    assert "await cli.help()" in doc
    assert 'await cli.cli("debug", "config")' in doc
    assert "credential files" in doc
    assert "Do not invoke" in doc
    assert "opencode providers login" in doc


def test_coding_cli_prompt_docs_arrive_through_integration_docs(tmp_path: Path) -> None:
    integration = CodexIntegration(name="codex", config=CodexConfig())
    prompt = developer_prompt("", tmp_path, [integration], has_python=True)

    assert "yext.codex:\nThin wrapper over the official codex CLI." in prompt
    assert "await cli.help()" in prompt
    assert 'await cli.cli("debug", "config")' in prompt
    assert "credential files" in prompt
    assert "# Coding CLIs" not in prompt


def test_developer_prompt_without_coding_cli_integration_omits_prompt_doc(tmp_path: Path) -> None:
    prompt = developer_prompt("", tmp_path, [], has_python=True)

    assert "yext.codex:" not in prompt
    assert "yext.opencode:" not in prompt


def test_yext_namespace_lazy_loads_submodules() -> None:
    import yext

    assert yext.opencode.__name__ == "yext.opencode"
    assert yext.codex.__name__ == "yext.codex"
