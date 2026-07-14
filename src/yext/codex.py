"""Use Codex models through short-lived, resumable sessions."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from ._coding_cli import Settings, Status, env, resolve_command, settings as _settings
from ._coding_cli import status as _status
from ._coding_cli import _filter_text as _redact


@dataclass(frozen=True, slots=True)
class ReasoningEffortInfo:
    effort: str
    description: str


@dataclass(frozen=True, slots=True)
class ServiceTierInfo:
    id: str
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class ModelInfo:
    id: str
    display_name: str
    description: str
    is_default: bool
    default_reasoning_effort: str
    supported_reasoning_efforts: tuple[ReasoningEffortInfo, ...]
    input_modalities: tuple[str, ...]
    service_tiers: tuple[ServiceTierInfo, ...]
    default_service_tier: str | None = None


def _config() -> Settings:
    return _settings("YEXT_CODEX", "codex", ("login", "status"), (), "codex login")


async def status() -> Status:
    return await _status(_config())


async def models(
    include_hidden: bool = False, timeout_s: float | None = None
) -> tuple[ModelInfo, ...]:
    """Return models available to the currently authenticated Codex account."""
    settings = _config()
    process = await asyncio.create_subprocess_exec(
        _binary(settings),
        "app-server",
        stdout=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env(settings),
    )
    timeout = settings.timeout_s if timeout_s is None else timeout_s
    try:
        return await asyncio.wait_for(_list_models(process, include_hidden), timeout)
    except TimeoutError:
        raise RuntimeError(
            f"codex model discovery timed out after {timeout:g}s"
        ) from None
    finally:
        await _stop(process)


async def _list_models(
    process: asyncio.subprocess.Process, include_hidden: bool
) -> tuple[ModelInfo, ...]:
    await _send(
        process,
        {
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "yuubot", "version": "1"}},
        },
    )
    await _response(process, 1, "initialize")
    await _send(process, {"method": "initialized"})
    found: list[ModelInfo] = []
    cursor: str | None = None
    request_id = 2
    while True:
        params: dict[str, object] = {"includeHidden": include_hidden}
        if cursor is not None:
            params["cursor"] = cursor
        await _send(
            process, {"id": request_id, "method": "model/list", "params": params}
        )
        result = await _response(process, request_id, "model/list")
        data = result.get("data")
        if not isinstance(data, list):
            raise RuntimeError("codex returned an invalid model list")
        for item in data:
            if isinstance(item, dict) and (
                include_hidden or not item.get("hidden", False)
            ):
                found.append(_model_info(item))
        next_cursor = result.get("nextCursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            return tuple(found)
        cursor = next_cursor
        request_id += 1


def open_session(
    model: str | None = None,
    reasoning: str | None = None,
    profile: str | None = None,
    cwd: str | Path | None = None,
    sandbox: str = "read-only",
    skip_git_repo_check: bool = False,
    timeout_s: float | None = None,
) -> Session:
    return Session(
        None,
        model,
        reasoning,
        profile,
        str(cwd) if cwd is not None else None,
        sandbox,
        skip_git_repo_check,
        timeout_s,
    )


def resume_session(
    session_id: str,
    model: str | None = None,
    reasoning: str | None = None,
    profile: str | None = None,
    cwd: str | Path | None = None,
    sandbox: str = "read-only",
    skip_git_repo_check: bool = False,
    timeout_s: float | None = None,
) -> Session:
    if not session_id.strip():
        raise ValueError("session_id must not be empty")
    return Session(
        session_id,
        model,
        reasoning,
        profile,
        str(cwd) if cwd is not None else None,
        sandbox,
        skip_git_repo_check,
        timeout_s,
    )


@dataclass(slots=True)
class Session:
    _id: str | None
    model: str | None
    reasoning: str | None
    profile: str | None
    cwd: str | None
    sandbox: str
    skip_git_repo_check: bool
    timeout_s: float | None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    @property
    def id(self) -> str | None:
        return self._id

    async def ask(
        self, prompt: str, timeout_s: float | None = None
    ) -> AsyncIterator[dict[str, object]]:
        """Stream one Codex turn as its original JSON events."""
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        async with self._lock:
            async for event in self._ask(prompt, timeout_s):
                yield event

    async def _ask(
        self, prompt: str, timeout_s: float | None
    ) -> AsyncIterator[dict[str, object]]:
        settings = _config()
        process = await asyncio.create_subprocess_exec(
            *self._command(_binary(settings), prompt),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env(settings),
        )
        timeout = timeout_s if timeout_s is not None else self.timeout_s
        if timeout is None:
            timeout = settings.timeout_s
        stderr_tail: deque[bytes] = deque()
        stderr_task = asyncio.create_task(_drain_stderr(process, stderr_tail))
        deadline = asyncio.get_running_loop().time() + timeout
        try:
            if process.stdout is None:
                raise RuntimeError("codex output is unavailable")
            final_message = False
            completed = False
            failure: str | None = None
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError
                raw_line = await asyncio.wait_for(process.stdout.readline(), remaining)
                if not raw_line:
                    break
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError, UnicodeDecodeError:
                    raise RuntimeError("codex returned invalid event data") from None
                if not isinstance(event, dict):
                    raise RuntimeError("codex returned invalid event data")
                event = cast(dict[str, object], event)
                event_type = event.get("type")
                if event_type == "thread.started" and isinstance(
                    event.get("thread_id"), str
                ):
                    self._id = cast(str, event["thread_id"])
                elif event_type == "item.completed":
                    item = event.get("item")
                    final_message = final_message or (
                        isinstance(item, dict)
                        and item.get("type") == "agent_message"
                        and isinstance(item.get("text"), str)
                    )
                elif event_type == "turn.failed":
                    failure = _error_message(event.get("error"))
                elif event_type == "error":
                    failure = _error_message(event.get("message", event.get("error")))
                elif event_type == "turn.completed":
                    completed = True
                yield event
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError
            return_code = await asyncio.wait_for(process.wait(), remaining)
            await stderr_task
            if failure is not None:
                raise RuntimeError(f"codex turn failed: {failure}")
            if return_code != 0:
                raise RuntimeError(_process_error(return_code, stderr_tail))
            if not completed:
                raise RuntimeError("codex ended without completing the turn")
            if not final_message:
                raise RuntimeError("codex completed without a final message")
        except TimeoutError:
            raise RuntimeError(f"codex timed out after {timeout:g}s") from None
        finally:
            await _stop(process)
            if not stderr_task.done():
                stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)

    def _command(self, binary: str, prompt: str) -> tuple[str, ...]:
        args = [
            binary,
            "exec",
            "--json",
            "-c",
            'approval_policy="never"',
            "-s",
            self.sandbox,
        ]
        if self.cwd is not None:
            args.extend(("-C", self.cwd))
        if self.model is not None:
            args.extend(("-m", self.model))
        if self.reasoning is not None:
            args.extend(("-c", f"model_reasoning_effort={json.dumps(self.reasoning)}"))
        if self.profile is not None:
            args.extend(("--profile", self.profile))
        if self.skip_git_repo_check:
            args.append("--skip-git-repo-check")
        if self._id is not None:
            args.extend(("resume", self._id))
        args.append(prompt)
        return tuple(args)

def _binary(settings: Settings) -> str:
    binary = resolve_command(settings)
    if binary is None:
        raise RuntimeError(f"{settings.command} binary was not found on PATH")
    return binary


async def _send(
    process: asyncio.subprocess.Process, message: dict[str, object]
) -> None:
    if process.stdin is None:
        raise RuntimeError("codex input is unavailable")
    process.stdin.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
    await process.stdin.drain()


async def _response(
    process: asyncio.subprocess.Process, request_id: int, operation: str
) -> dict[str, Any]:
    if process.stdout is None:
        raise RuntimeError("codex output is unavailable")
    while line := await process.stdout.readline():
        try:
            message = json.loads(line)
        except json.JSONDecodeError, UnicodeDecodeError:
            raise RuntimeError("codex returned invalid protocol data") from None
        if not isinstance(message, dict) or message.get("id") != request_id:
            continue
        if message.get("error") is not None:
            raise RuntimeError(
                f"codex {operation} failed: {_error_message(message['error'])}"
            )
        result = message.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"codex returned an invalid {operation} response")
        return result
    stderr = await process.stderr.read() if process.stderr is not None else b""
    raise RuntimeError(_process_error(process.returncode, deque((stderr,))))


def _model_info(item: dict[str, Any]) -> ModelInfo:
    try:
        efforts = tuple(
            ReasoningEffortInfo(option["reasoningEffort"], option["description"])
            for option in item["supportedReasoningEfforts"]
        )
        tiers = tuple(
            ServiceTierInfo(tier["id"], tier["name"], tier["description"])
            for tier in item.get("serviceTiers", ())
        )
        return ModelInfo(
            item["id"],
            item["displayName"],
            item["description"],
            item["isDefault"],
            item["defaultReasoningEffort"],
            efforts,
            tuple(item.get("inputModalities", ())),
            tiers,
            item.get("defaultServiceTier"),
        )
    except KeyError, TypeError:
        raise RuntimeError("codex returned invalid model metadata") from None


def _error_message(error: object) -> str:
    if isinstance(error, dict):
        message = cast(dict[str, object], error).get("message")
        if isinstance(message, str):
            return _redact(message)[:1000]
    if isinstance(error, str):
        return _redact(error)[:1000]
    return "unknown error"


def _process_error(return_code: int | None, stderr_tail: deque[bytes]) -> str:
    detail = _redact(b"".join(stderr_tail).decode(errors="replace")).strip()[-1000:]
    return f"codex exited with code {return_code}" + (f": {detail}" if detail else "")


async def _drain_stderr(
    process: asyncio.subprocess.Process, tail: deque[bytes], limit: int = 4096
) -> None:
    if process.stderr is None:
        return
    while chunk := await process.stderr.read(4096):
        tail.append(chunk)
        size = sum(map(len, tail))
        while tail and size - len(tail[0]) >= limit:
            size -= len(tail.popleft())


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()
        await process.wait()
