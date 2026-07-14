from __future__ import annotations

from pathlib import Path
import asyncio

import pytest

from yuubot.actor.prompt import developer_prompt
from yuubot import Yuubot
from yuubot.integrations import IntegrationRecord
from yuubot.integrations.coding_cli import (
    CodexConfig,
    CodexIntegration,
    OpenCodeConfig,
    OpenCodeIntegration,
    probe_coding_cli,
)
from yuubot.integrations.registry import default_registry


@pytest.mark.asyncio
async def test_coding_cli_missing_binary_returns_recovery_action() -> None:
    state = await probe_coding_cli(
        CodexConfig(
            "definitely-not-installed-yuubot-cli",
            ("auth", "status"),
            "missing login",
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
async def test_coding_cli_probe_uses_user_bin_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / ".nvm" / "versions" / "node" / "v99.0.0" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "fake-runtime").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_dir / "fake-cli").write_text("#!/usr/bin/env fake-runtime\n", encoding="utf-8")
    (bin_dir / "fake-runtime").chmod(0o755)
    (bin_dir / "fake-cli").chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    state = await probe_coding_cli(CodexConfig("fake-cli", ("health",)))

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
async def test_required_config_integrations_still_require_saved_config(
    tmp_path: Path,
) -> None:
    app = await Yuubot.create(tmp_path / "data")

    integration = await app.enable_configured_integration("github")

    assert integration is None


@pytest.mark.asyncio
async def test_enable_records_missing_binary_health(tmp_path: Path) -> None:
    app = await Yuubot.create(tmp_path / "data")
    await app.configure_integration(
        IntegrationRecord(
            "codex",
            "codex",
            "codex",
            {
                "command": "definitely-not-installed-yuubot-cli",
                "login_command": "missing login",
            },
        )
    )

    integration = await app.enable_configured_integration("codex")
    snapshots = await app.integration_snapshots()
    codex = next(item for item in snapshots if item.type == "codex")

    assert integration is not None
    assert codex.enabled is True
    assert codex.health_status == "error"
    assert codex.last_error is not None
    assert (
        codex.last_error.message
        == "definitely-not-installed-yuubot-cli binary was not found on PATH"
    )
    assert codex.action_hint == {
        "kind": "open_pty",
        "title": "Check definitely-not-installed-yuubot-cli",
        "suggested_command": "missing login",
        "cwd": "~",
    }


@pytest.mark.asyncio
async def test_enable_records_probe_auth_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli = bin_dir / "fake-cli"
    cli.write_text("#!/bin/sh\necho 'not logged in' >&2\nexit 1\n", encoding="utf-8")
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    app = await Yuubot.create(tmp_path / "data")
    await app.configure_integration(
        IntegrationRecord(
            "codex",
            "codex",
            "codex",
            {
                "command": "fake-cli",
                "probe_args": ["auth", "status"],
                "login_command": "fake-cli login",
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
async def test_yext_codex_session_streams_raw_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yext.codex

    cli = tmp_path / "fake-codex"
    cli.write_text(
        """#!/usr/bin/env python3
import json, sys
print(json.dumps({"type":"thread.started","thread_id":"thread-1"}))
print(json.dumps({"type":"item.completed","item":{"type":"reasoning","text":"secret thought"}}))
print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"draft"}}))
print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"final answer"}}))
print(json.dumps({"type":"turn.completed","usage":{"input_tokens":10}}))
print("startup warning", file=sys.stderr)
""",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    integration = CodexIntegration(
        "codex",
        CodexConfig(str(cli), ()),
    )
    for key, value in integration.session_context().items():
        monkeypatch.setenv(key, value)

    session = yext.codex.open_session(cwd=tmp_path, skip_git_repo_check=True)
    events = [event async for event in session.ask("coding-ok")]

    assert [event["type"] for event in events] == [
        "thread.started",
        "item.completed",
        "item.completed",
        "item.completed",
        "turn.completed",
    ]
    assert events[-1] == {"type": "turn.completed", "usage": {"input_tokens": 10}}
    assert session.id == "thread-1"
    assert not hasattr(yext.codex, "run")
    assert not hasattr(yext.codex, "cli")
    assert not hasattr(yext.codex, "help")


@pytest.mark.asyncio
async def test_yext_codex_session_yields_before_process_exits_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yext.codex

    cli = tmp_path / "fake-codex"
    cli.write_text(
        """#!/usr/bin/env python3
import json, sys, time
args = sys.argv
thread_id = args[args.index("resume") + 1] if "resume" in args else "live-thread"
print(json.dumps({"type":"thread.started","thread_id":thread_id,"raw":{"kept":True}}), flush=True)
time.sleep(.3)
print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"done"}}), flush=True)
print(json.dumps({"type":"turn.completed"}), flush=True)
""",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    integration = CodexIntegration("codex", CodexConfig(str(cli), ()))
    for key, value in integration.session_context().items():
        monkeypatch.setenv(key, value)

    session = yext.codex.open_session(cwd=tmp_path, skip_git_repo_check=True)
    stream = session.ask("first")
    first = await asyncio.wait_for(anext(stream), 0.2)
    assert first == {
        "type": "thread.started",
        "thread_id": "live-thread",
        "raw": {"kept": True},
    }
    assert session.id == "live-thread"
    assert [event["type"] async for event in stream] == [
        "item.completed",
        "turn.completed",
    ]
    assert [event["type"] async for event in session.ask("second")] == [
        "thread.started",
        "item.completed",
        "turn.completed",
    ]


@pytest.mark.asyncio
async def test_yext_codex_failed_event_is_visible_before_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yext.codex

    cli = tmp_path / "fake-codex"
    cli.write_text(
        """#!/usr/bin/env python3
import json
print(json.dumps({"type":"turn.failed","error":{"message":"bad token secret"}}), flush=True)
""",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    integration = CodexIntegration("codex", CodexConfig(str(cli), ()))
    for key, value in integration.session_context().items():
        monkeypatch.setenv(key, value)

    stream = yext.codex.open_session().ask("fail")
    assert (await anext(stream))["type"] == "turn.failed"
    with pytest.raises(RuntimeError, match="codex turn failed"):
        await anext(stream)


@pytest.mark.asyncio
async def test_yext_codex_discovers_paginated_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yext.codex

    cli = tmp_path / "fake-codex"
    cli.write_text(
        """#!/usr/bin/env python3
import json, sys
def model(id, hidden=False):
    return {"id":id,"model":id,"displayName":id.upper(),"description":"desc","hidden":hidden,
      "isDefault":id=="one","defaultReasoningEffort":"medium",
      "supportedReasoningEfforts":[{"reasoningEffort":"medium","description":"balanced"}],
      "inputModalities":["text","image"],
      "serviceTiers":[{"id":"fast","name":"Fast","description":"Lower latency"}],
      "defaultServiceTier":"fast"}
for line in sys.stdin:
    request=json.loads(line)
    if request.get("method")=="initialize":
        print(json.dumps({"id":request["id"],"result":{}}), flush=True)
    elif request.get("method")=="model/list":
        cursor=request["params"].get("cursor")
        result={"data":[model("one"),model("hidden",True)],"nextCursor":"page-2"} if cursor is None else {"data":[model("two")],"nextCursor":None}
        print(json.dumps({"id":request["id"],"result":result}), flush=True)
""",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    integration = CodexIntegration("codex", CodexConfig(str(cli), ()))
    for key, value in integration.session_context().items():
        monkeypatch.setenv(key, value)

    found = await yext.codex.models()

    assert [model.id for model in found] == ["one", "two"]
    assert found[0].is_default is True
    assert found[0].supported_reasoning_efforts[0].effort == "medium"
    assert found[0].input_modalities == ("text", "image")
    assert found[0].service_tiers[0].id == "fast"


@pytest.mark.asyncio
async def test_yext_opencode_facade_filters_control_sequences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_coding_cli_prompt_doc_contains_usage_guidance() -> None:
    integration = OpenCodeIntegration("opencode", OpenCodeConfig())
    doc = integration.prompt_doc()

    assert "await cli.help()" in doc
    assert 'await cli.cli("debug", "config")' in doc
    assert "credential files" in doc
    assert "Do not invoke" in doc
    assert "opencode providers login" in doc


def test_coding_cli_prompt_docs_arrive_through_integration_docs(tmp_path: Path) -> None:
    integration = CodexIntegration("codex", CodexConfig())
    prompt = developer_prompt("", tmp_path, [integration], has_python=True)

    assert "yext.codex:\nWork with Codex through execute_python." in prompt
    assert "await codex.models()" in prompt
    assert "codex.open_session" in prompt
    assert "codex.resume_session" in prompt
    assert "async for event in session.ask" in prompt
    assert "item.completed" in prompt
    assert "turn.failed" in prompt
    assert "complete task, relevant paths and context" in prompt
    assert "without asking follow-up questions" in prompt
    assert "await cli.help()" not in prompt
    assert "await cli.cli(" not in prompt
    assert "await cli.run(" not in prompt
    assert "# Coding CLIs" not in prompt


def test_developer_prompt_without_coding_cli_integration_omits_prompt_doc(
    tmp_path: Path,
) -> None:
    prompt = developer_prompt("", tmp_path, [], has_python=True)

    assert "yext.codex:" not in prompt
    assert "yext.opencode:" not in prompt
