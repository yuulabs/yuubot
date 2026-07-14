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

CodingCliStatus = Literal[
    "disabled", "checking", "ready", "needs_action", "degraded", "error"
]


class CodingCliSettings(Protocol):
    @property
    def command(self) -> str: ...

    @property
    def probe_args(self) -> tuple[str, ...]: ...

    @property
    def login_command(self) -> str: ...

    @property
    def timeout_s(self) -> float: ...


class CodexConfig(msgspec.Struct, frozen=True):
    command: str = "codex"
    probe_args: tuple[str, ...] = ("login", "status")
    login_command: str = "codex login"
    timeout_s: float = 600.0


class OpenCodeConfig(msgspec.Struct, frozen=True):
    command: str = "opencode"
    probe_args: tuple[str, ...] = ("providers", "list")
    login_command: str = "opencode providers login"
    run_args_prefix: tuple[str, ...] = ("run",)
    timeout_s: float = 600.0


class CodingCliState(msgspec.Struct, frozen=True):
    status: CodingCliStatus
    reason: str = ""
    binary_path: str = ""
    action_hint: dict[str, object] | None = None
    last_checked_at: str | None = None


class CodingCliRunResult(msgspec.Struct, frozen=True):
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
        context = {
            f"{self.env_prefix}_COMMAND": self.config.command,
            f"{self.env_prefix}_PROBE_ARGS": msgspec.json.encode(
                self.config.probe_args
            ).decode(),
            f"{self.env_prefix}_LOGIN_COMMAND": self.config.login_command,
            f"{self.env_prefix}_TIMEOUT_S": str(self.config.timeout_s),
            f"{self.env_prefix}_PATH": os.pathsep.join(_candidate_path_entries()),
        }
        if isinstance(self.config, OpenCodeConfig):
            context[f"{self.env_prefix}_RUN_ARGS_PREFIX"] = msgspec.json.encode(
                self.config.run_args_prefix
            ).decode()
        return context

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
            self.config.command,
            self.package_path,
            self.config.login_command,
        )


@frozen
class CodexIntegration(CodingCliIntegration):
    name: str
    config: CodexConfig
    package_path: str = "yext.codex"
    env_prefix: str = "YEXT_CODEX"

    def prompt_doc(self) -> str:
        return _codex_prompt_doc(self.config.login_command)


@frozen
class OpenCodeIntegration(CodingCliIntegration):
    name: str
    config: OpenCodeConfig
    package_path: str = "yext.opencode"
    env_prefix: str = "YEXT_OPENCODE"


def make_codex(name: str, config: msgspec.Struct, runtime: object) -> CodexIntegration:
    del runtime
    return CodexIntegration(name, msgspec.convert(config, CodexConfig))


def make_opencode(
    name: str, config: msgspec.Struct, runtime: object
) -> OpenCodeIntegration:
    del runtime
    return OpenCodeIntegration(name, msgspec.convert(config, OpenCodeConfig))


async def probe_coding_cli(settings: CodingCliSettings) -> CodingCliState:
    now = utc_now_iso()
    env = coding_cli_env()
    binary = resolve_coding_cli_command(settings.command, env)
    if binary is None:
        return CodingCliState(
            "error",
            f"{settings.command} binary was not found on PATH",
            action_hint=_recovery_hint(settings),
            last_checked_at=now,
        )
    if not settings.probe_args:
        return CodingCliState("ready", binary_path=binary, last_checked_at=now)
    process = await asyncio.create_subprocess_exec(
        binary,
        *settings.probe_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await process.communicate()
    if process.returncode == 0:
        return CodingCliState("ready", binary_path=binary, last_checked_at=now)
    reason = (stderr or stdout).decode("utf-8", errors="replace").strip()
    return CodingCliState(
        "needs_action",
        reason or f"{settings.command} is not ready",
        binary,
        _recovery_hint(settings),
        now,
    )


def inspect_coding_cli(settings: CodingCliSettings) -> CodingCliState:
    binary = resolve_coding_cli_command(settings.command)
    return CodingCliState(
        "checking",
        binary_path=binary or "",
        last_checked_at=utc_now_iso(),
    )


async def run_coding_cli(
    settings: OpenCodeConfig,
    prompt: str,
    extra_args: tuple[str, ...] = (),
    timeout_s: float | None = None,
) -> CodingCliRunResult:
    env = coding_cli_env()
    binary = resolve_coding_cli_command(settings.command, env)
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
        command,
        int(process.returncode or 0),
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def resolve_coding_cli_command(
    command: str, env: dict[str, str] | None = None
) -> str | None:
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


def _coding_cli_prompt_doc(command: str, package_path: str, login_command: str) -> str:
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
            "When unsure: help() -> cli(subcommand, ...) -> read result.stdout.",
            f"Auth is redacted (***). If needs_action, tell admin to run `{login_command}` in Admin Terminal.",
            f"Do not invoke {command} via bash or read credential files.",
        ]
    )


def _codex_prompt_doc(login_command: str) -> str:
    return "\n".join(
        [
            "Work with Codex through execute_python. Each ask is a single-use async stream of raw Codex JSON events.",
            "",
            "Import:   import json; import yext.codex as codex",
            "Models:   await codex.models()",
            'Start:    session = codex.open_session(model="gpt-5.6-sol", reasoning="high", profile="lean", cwd="/workspace", sandbox="read-only", skip_git_repo_check=True)',
            'Ask:      async for event in session.ask("complete task context"): print(json.dumps(event, ensure_ascii=False), flush=True)',
            'Continue: async for event in session.ask("continue with the remaining work"): print(event)',
            'Resume:   session = codex.resume_session(session_id, profile="lean", cwd="/workspace", sandbox="read-only")',
            "",
            "Profile is optional; set it only to a configured Codex profile name. The same profile is used for every ask and resume on that session.",
            'Final text: on item.completed, if event["item"]["type"] == "agent_message", save event["item"]["text"].',
            'Terminal events are turn.completed, turn.failed, and error. Failed events are yielded before ask raises RuntimeError.',
            "Consume each ask stream once and to completion (or explicitly close it).",
            "",
            "In the first ask, provide the complete task, relevant paths and context, constraints, expected deliverables, and verification.",
            "Tell Codex to make reasonable decisions for non-critical ambiguity and complete the current turn without asking follow-up questions.",
            "Keep session.id if work may continue in another execute_python kernel. Calls on one session are serialized.",
            f"If authentication is required, tell the admin to run `{login_command}` in Admin Terminal.",
            "Do not invoke Codex via bash or read credential files.",
        ]
    )
