"""OpenCode CLI integration facade."""

from __future__ import annotations

from ._coding_cli import Result, Settings, Status, cli as _cli, help as _help, run as _run, settings as _settings, status as _status


def _config() -> Settings:
    return _settings(
        "YEXT_OPENCODE",
        default_command="opencode",
        default_probe_args=("providers", "list"),
        default_run_args=("run",),
        default_login_command="opencode providers login",
    )


async def status() -> Status:
    return await _status(_config())


async def run(prompt: str, extra_args: tuple[str, ...] = (), timeout_s: float | None = None) -> Result:
    return await _run(_config(), prompt, extra_args=extra_args, timeout_s=timeout_s)


async def cli(*args: str, timeout_s: float | None = None) -> Result:
    return await _cli(_config(), args, timeout_s=timeout_s)


async def help(*topics: str, timeout_s: float | None = None) -> Result:
    return await _help(_config(), *topics, timeout_s=timeout_s)
