"""Shared helpers for coding CLI integration facades."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from yuubot.runtime.pty_display import filter_tool_output

_MIN_ENV_KEYS = ("HOME", "USER", "LANG", "LC_ALL", "TERM")
REDACTED = "***"
_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-or-v1-[a-zA-Z0-9_-]+"),
    re.compile(r"sk-kimi-[a-zA-Z0-9_-]+"),
    re.compile(r"sk-[a-zA-Z0-9_-]{8,}"),
    re.compile(r"rt\.[0-9]\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+"),
)


@dataclass(frozen=True, slots=True)
class Status:
    status: str
    reason: str = ""
    binary_path: str = ""
    action_hint: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class Result:
    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class Settings:
    command: str
    probe_args: tuple[str, ...]
    login_command: str
    run_args_prefix: tuple[str, ...]
    timeout_s: float
    path: str


def settings(prefix: str, default_command: str, default_probe_args: tuple[str, ...], default_run_args: tuple[str, ...], default_login_command: str) -> Settings:
    return Settings(
        os.getenv(f"{prefix}_COMMAND", default_command),
        _json_tuple(os.getenv(f"{prefix}_PROBE_ARGS"), default_probe_args),
        os.getenv(f"{prefix}_LOGIN_COMMAND", default_login_command),
        _json_tuple(os.getenv(f"{prefix}_RUN_ARGS_PREFIX"), default_run_args),
        float(os.getenv(f"{prefix}_TIMEOUT_S", "600")),
        os.getenv(f"{prefix}_PATH", ""),
    )


async def status(settings: Settings) -> Status:
    binary = resolve_command(settings)
    if binary is None:
        return Status(
            "error",
            f"{settings.command} binary was not found on PATH",
            action_hint=_recovery_hint(settings),
        )
    if not settings.probe_args:
        return Status("ready", binary_path=binary)
    process = await asyncio.create_subprocess_exec(
        binary,
        *settings.probe_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env(settings),
    )
    stdout, stderr = await process.communicate()
    if process.returncode == 0:
        return Status("ready", binary_path=binary)
    reason = _filter_text((stderr or stdout).decode("utf-8", errors="replace").strip())
    return Status(
        "needs_action",
        reason or f"{settings.command} is not ready",
        binary,
        _recovery_hint(settings),
    )


async def cli(settings: Settings, args: tuple[str, ...], timeout_s: float | None = None) -> Result:
    binary = resolve_command(settings)
    if binary is None:
        raise RuntimeError(f"{settings.command} binary was not found on PATH")
    command = (binary, *args)
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env(settings),
    )
    timeout = settings.timeout_s if timeout_s is None else timeout_s
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(f"{settings.command} timed out after {timeout:g}s") from None
    return redact_result(
        Result(
            command,
            int(process.returncode or 0),
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )
    )


async def help(settings: Settings, *topics: str, timeout_s: float | None = None) -> Result:
    return await cli(settings, ("help", *topics), timeout_s=timeout_s)


async def run(settings: Settings, prompt: str, extra_args: tuple[str, ...] = (), timeout_s: float | None = None) -> Result:
    checked = await status(settings)
    if checked.status != "ready":
        action = checked.action_hint or {}
        suggested = action.get("suggested_command")
        if isinstance(suggested, str) and suggested:
            raise RuntimeError(f"{settings.command} did not pass its health check. Open Terminal and run `{suggested}`.")
        raise RuntimeError(checked.reason or f"{settings.command} is not ready")
    return await cli(settings, (*settings.run_args_prefix, *extra_args, prompt), timeout_s=timeout_s)


def redact_result(result: Result) -> Result:
    return Result(
        result.command,
        result.exit_code,
        _filter_text(result.stdout),
        _filter_text(result.stderr),
    )


def _filter_text(text: str) -> str:
    filtered = filter_tool_output(text)
    for pattern in _SECRET_VALUE_PATTERNS:
        filtered = pattern.sub(REDACTED, filtered)
    return filtered


def resolve_command(settings: Settings) -> str | None:
    return shutil.which(os.path.expanduser(settings.command), path=env(settings).get("PATH"))


def env(settings: Settings) -> dict[str, str]:
    resolved = {key: os.environ[key] for key in _MIN_ENV_KEYS if key in os.environ}
    path = settings.path or os.environ.get("PATH", "")
    resolved["PATH"] = os.pathsep.join(_dedupe_path(path) + _default_path_entries())
    return resolved


def _json_tuple(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return default
    if not isinstance(parsed, list):
        return default
    return tuple(item for item in parsed if isinstance(item, str))


def _default_path_entries() -> list[str]:
    home = Path.home()
    entries = [
        home / ".local" / "bin",
        home / ".cargo" / "bin",
        home / ".opencode" / "bin",
        home / ".bun" / "bin",
        home / ".npm-global" / "bin",
        Path("/usr/local/bin"),
        Path("/usr/bin"),
        Path("/bin"),
    ]
    nvm = home / ".nvm" / "versions" / "node"
    if nvm.is_dir():
        entries.extend(sorted((path for path in nvm.glob("*/bin") if path.is_dir()), reverse=True))
    return [str(path) for path in entries]


def _dedupe_path(path: str) -> list[str]:
    seen: set[str] = set()
    entries: list[str] = []
    for item in path.split(os.pathsep):
        if not item or item in seen:
            continue
        seen.add(item)
        entries.append(item)
    return entries


def _recovery_hint(settings: Settings) -> dict[str, object] | None:
    if not settings.login_command:
        return None
    return {
        "kind": "open_pty",
        "title": f"Check {settings.command}",
        "suggested_command": settings.login_command,
        "cwd": "~",
    }
