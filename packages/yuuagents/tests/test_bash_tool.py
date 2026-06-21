from __future__ import annotations

from pathlib import Path

import pytest

from yuuagents.core.task import Owner, OwnerType
from yuuagents.obs.entitylog import EntityLog
from yuuagents.tool.bash import BashParams, BashRunner, BashTool, BashToolConfig
from yuuagents.tool.primitives import (
    ToolCallParams,
    ToolCallTask,
    ToolContext,
    resolve_tool_type,
)


@pytest.mark.asyncio
async def test_bash_runs_initialized_shell_from_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text(
        "export YUU_BASH_INIT=loaded\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))

    result = await _run_bash(
        tmp_path, command='printf \'%s:%s\' "$PWD" "$YUU_BASH_INIT"'
    )

    assert result.startswith(f"{tmp_path}:loaded")
    assert "[exit_code=0]" in result


@pytest.mark.asyncio
async def test_bash_forwards_stdout_to_entity_log(tmp_path: Path) -> None:
    entity_log = EntityLog()

    result = await _run_bash(
        tmp_path,
        command="printf alpha; printf beta",
        entity_log=entity_log,
    )

    assert result.startswith("alphabeta")
    assert entity_log.tail() == "alphabeta"


@pytest.mark.asyncio
async def test_bash_returns_small_stdout_inline(tmp_path: Path) -> None:
    result = await _run_bash(tmp_path, command="printf small")

    assert result.startswith("small")
    assert "stdout truncated" not in result


@pytest.mark.asyncio
async def test_bash_captures_large_stdout_to_tmp_file(tmp_path: Path) -> None:
    stdout = "a" * 500 + "b" * 1501

    result = await _run_bash(tmp_path, command=f"printf '{stdout}'")

    assert result.startswith("a" * 500 + "b")
    assert "b" * 1500 in result
    capture_path = _capture_path_from_result(result)
    assert capture_path.read_text(encoding="utf-8") == stdout


@pytest.mark.asyncio
async def test_bash_uses_relative_cwd_under_workspace(tmp_path: Path) -> None:
    (tmp_path / "apps" / "yuubot").mkdir(parents=True)

    result = await _run_bash(tmp_path, command="pwd", cwd="apps/yuubot")

    assert result.startswith(str(tmp_path / "apps" / "yuubot"))


@pytest.mark.asyncio
async def test_bash_rejects_cwd_escape_attempts(tmp_path: Path) -> None:
    tool = BashRunner.from_config(BashToolConfig(workspace_root=str(tmp_path)))

    with pytest.raises(ValueError, match="relative"):
        tool.resolve_cwd(str(tmp_path))
    with pytest.raises(ValueError, match="must not contain"):
        tool.resolve_cwd("../outside")


@pytest.mark.asyncio
async def test_bash_timeout_is_enforced_and_reported(tmp_path: Path) -> None:
    result = await _run_bash(
        tmp_path,
        command="printf before; sleep 5; printf after",
        timeout_s=0.1,
    )

    assert "after" not in result
    assert "[timed_out=true]" in result


@pytest.mark.asyncio
async def test_bash_truncates_stderr_with_note(tmp_path: Path) -> None:
    result = await _run_bash(
        tmp_path,
        command="python -c 'import sys; sys.stderr.write(\"e\" * 120)'",
        max_stderr_chars=20,
    )

    assert "[stderr]" in result
    assert "[stderr truncated]" in result
    assert "e" * 10 in result


def test_bash_tool_is_registered_by_name() -> None:
    assert resolve_tool_type("bash") is BashTool


async def _run_bash(
    workspace_root: Path,
    *,
    command: str,
    cwd: str | None = None,
    timeout_s: float | None = None,
    entity_log: EntityLog | None = None,
    max_stderr_chars: int = 4000,
) -> str:
    tool = BashTool.from_startup(
        None,
        BashToolConfig(
            workspace_root=str(workspace_root),
            timeout_s=1.0,
            max_timeout_s=10.0,
            max_stderr_chars=max_stderr_chars,
        ),
    )
    params = BashParams(command=command, cwd=cwd, timeout_s=timeout_s)
    return await tool.create_coro(
        ToolCallTask(
            id="task-1",
            owner=Owner(type=OwnerType.AGENT, id="agent-1"),
            coro=None,
            tool_call_params=ToolCallParams(
                tool_call_id="call-1",
                tool_name="bash",
                params=params,
            ),
        ),
        ToolContext(
            agent_id="agent-1",
            tool_call_id="call-1",
            eventbus=None,
            entity_log=entity_log or EntityLog(),
        ),
    )


def _capture_path_from_result(result: str) -> Path:
    marker = "full output captured at "
    start = result.index(marker) + len(marker)
    end = result.index("]", start)
    return Path(result[start:end])
