from __future__ import annotations

import subprocess

import pytest

import yb


@pytest.mark.asyncio
async def test_yb_bash_runs_command_and_cleans_interactive_noise() -> None:
    result = await yb.bash(
        "printf '%s' \"$YB_BASH_ENV_VALUE\"",
        env={"YB_BASH_ENV_VALUE": "ok"},
    )

    assert result.returncode == 0
    assert result.stdout == "ok"
    assert result.stderr == ""


@pytest.mark.asyncio
async def test_yb_bash_check_raises_on_nonzero_exit() -> None:
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        await yb.bash("printf fail >&2\nexit 7", check=True)

    assert exc_info.value.returncode == 7
    assert "fail" in exc_info.value.stderr
