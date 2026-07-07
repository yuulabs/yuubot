"""Coding agent CLI integrations."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Literal, Protocol

import msgspec
from attrs import frozen

from ..util.time import utc_now_iso

CodingCliStatus = Literal["disabled", "checking", "ready", "needs_action", "degraded", "error"]


class CodingCliSettings(Protocol):
    @property
    def command(self) -> str: ...

    @property
    def probe_args(self) -> tuple[str, ...]: ...

    @property
    def login_command(self) -> str: ...

    @property
    def run_args_prefix(self) -> tuple[str, ...]: ...

    @property
    def timeout_s(self) -> float: ...


class CodexConfig(msgspec.Struct, frozen=True, kw_only=True):
    command: str = "codex"
    probe_args: tuple[str, ...] = ("login", "status")
    login_command: str = "codex login"
    run_args_prefix: tuple[str, ...] = ("exec",)
    timeout_s: float = 600.0


class OpenCodeConfig(msgspec.Struct, frozen=True, kw_only=True):
    command: str = "opencode"
    probe_args: tuple[str, ...] = ("providers", "list")
    login_command: str = "opencode providers login"
    run_args_prefix: tuple[str, ...] = ("run",)
    timeout_s: float = 600.0


class CodingCliState(msgspec.Struct, frozen=True, kw_only=True):
    status: CodingCliStatus
    reason: str = ""
    binary_path: str = ""
    action_hint: dict[str, object] | None = None
    last_checked_at: str | None = None


class CodingCliRunResult(msgspec.Struct, frozen=True, kw_only=True):
    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str


class CodingCliIntegration:
    name: str
    config: CodingCliSettings
    package_path: str
    env_prefix: str

    def session_context(self) -> dict[str, str]:
        return {
            f"{self.env_prefix}_COMMAND": self.config.command,
            f"{self.env_prefix}_PROBE_ARGS": msgspec.json.encode(self.config.probe_args).decode(),
            f"{self.env_prefix}_LOGIN_COMMAND": self.config.login_command,
            f"{self.env_prefix}_RUN_ARGS_PREFIX": msgspec.json.encode(self.config.run_args_prefix).decode(),
            f"{self.env_prefix}_TIMEOUT_S": str(self.config.timeout_s),
            f"{self.env_prefix}_PATH": os.pathsep.join(_candidate_path_entries()),
        }

    async def health_check(self) -> dict[str, object]:
        state = await probe_coding_cli(self.config)
        return {
            "status": state.status,
            "reason": state.reason,
            "details": {"binary_path": state.binary_path},
            "action_hint": state.action_hint,
        }

    async def close(self) -> None:
        return None

    def prompt_doc(self) -> str:
        return _coding_cli_prompt_doc(
            command=self.config.command,
            package_path=self.package_path,
            login_command=self.config.login_command,
        )


@frozen
class CodexIntegration(CodingCliIntegration):
    name: str
    config: CodexConfig
    package_path: str = "yext.codex"
    env_prefix: str = "YEXT_CODEX"


@frozen
class OpenCodeIntegration(CodingCliIntegration):
    name: str
    config: OpenCodeConfig
    package_path: str = "yext.opencode"
    env_prefix: str = "YEXT_OPENCODE"


def make_codex(name: str, config: msgspec.Struct, runtime: object) -> CodexIntegration:
    del runtime
    return CodexIntegration(name=name, config=msgspec.convert(config, CodexConfig))


def make_opencode(name: str, config: msgspec.Struct, runtime: object) -> OpenCodeIntegration:
    del runtime
    return OpenCodeIntegration(name=name, config=msgspec.convert(config, OpenCodeConfig))


async def probe_coding_cli(settings: CodingCliSettings) -> CodingCliState:
    now = utc_now_iso()
    env = coding_cli_env()
    binary = resolve_coding_cli_command(settings.command, env=env)
    if binary is None:
        return CodingCliState(
            status="error",
            reason=f"{settings.command} binary was not found on PATH",
            action_hint=_recovery_hint(settings),
            last_checked_at=now,
        )
    if not settings.probe_args:
        return CodingCliState(status="ready", binary_path=binary, last_checked_at=now)
    process = await asyncio.create_subprocess_exec(
        binary,
        *settings.probe_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await process.communicate()
    if process.returncode == 0:
        return CodingCliState(status="ready", binary_path=binary, last_checked_at=now)
    reason = (stderr or stdout).decode("utf-8", errors="replace").strip()
    return CodingCliState(
        status="needs_action",
        reason=reason or f"{settings.command} is not ready",
        binary_path=binary,
        action_hint=_recovery_hint(settings),
        last_checked_at=now,
    )


def inspect_coding_cli(settings: CodingCliSettings) -> CodingCliState:
    binary = resolve_coding_cli_command(settings.command)
    return CodingCliState(
        status="checking",
        binary_path=binary or "",
        last_checked_at=utc_now_iso(),
    )


async def run_coding_cli(
    settings: CodingCliSettings,
    *,
    prompt: str,
    extra_args: tuple[str, ...] = (),
    timeout_s: float | None = None,
) -> CodingCliRunResult:
    env = coding_cli_env()
    binary = resolve_coding_cli_command(settings.command, env=env)
    if binary is None:
        raise RuntimeError(f"{settings.command} binary was not found on PATH")
    command = (binary, *settings.run_args_prefix, *extra_args, prompt)
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    timeout = settings.timeout_s if timeout_s is None else timeout_s
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(f"{settings.command} timed out after {timeout:g}s") from None
    return CodingCliRunResult(
        command=command,
        exit_code=int(process.returncode or 0),
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


def resolve_coding_cli_command(command: str, *, env: dict[str, str] | None = None) -> str | None:
    resolved_env = env or coding_cli_env()
    expanded = os.path.expanduser(command)
    return shutil.which(expanded, path=resolved_env.get("PATH"))


def coding_cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join(_candidate_path_entries())
    return env


def _candidate_path_entries() -> list[str]:
    home = Path.home()
    entries = _dedupe_path(os.environ.get("PATH", ""))
    entries.extend(str(path) for path in _user_bin_candidates(home))
    entries.extend(str(path) for path in _nvm_bin_candidates(home))
    entries.extend(["/usr/local/bin", "/usr/bin", "/bin"])
    return _dedupe_path(os.pathsep.join(entries))


def _user_bin_candidates(home: Path) -> tuple[Path, ...]:
    return (
        home / ".local" / "bin",
        home / ".cargo" / "bin",
        home / ".opencode" / "bin",
        home / ".bun" / "bin",
        home / ".npm-global" / "bin",
    )


def _nvm_bin_candidates(home: Path) -> list[Path]:
    root = home / ".nvm" / "versions" / "node"
    if not root.is_dir():
        return []
    return sorted((path for path in root.glob("*/bin") if path.is_dir()), reverse=True)


def _dedupe_path(path: str) -> list[str]:
    seen: set[str] = set()
    entries: list[str] = []
    for item in path.split(os.pathsep):
        if not item or item in seen:
            continue
        seen.add(item)
        entries.append(item)
    return entries


def _recovery_hint(settings: CodingCliSettings) -> dict[str, object] | None:
    if not settings.login_command:
        return None
    return {
        "kind": "open_pty",
        "title": f"Check {settings.command}",
        "suggested_command": settings.login_command,
        "cwd": "~",
    }


def _coding_cli_prompt_doc(*, command: str, package_path: str, login_command: str) -> str:
    return "\n".join(
        [
            f"Thin wrapper over the official {command} CLI. Use through execute_python only.",
            "",
            f"Import:  import {package_path} as cli",
            "Ready:   await cli.status()",
            'Manual:  await cli.help() / await cli.help("debug")',
            'Run:     await cli.cli("debug", "config")  -> Result(stdout, stderr, exit_code)',
            'Task:    await cli.run("fix the bug")',
            "",
            "When unsure: help() -> cli(subcommand, ...) -> read result.stdout. Do not re-run via bash.",
            "",
            f"Do NOT: invoke {command} via bash; read auth.json or credential stores;",
            f"        interactive login ({login_command}) — ask admin to use Admin Terminal.",
            "Output is redacted; *** means secret. needs_action -> tell admin the login command.",
        ]
    )
