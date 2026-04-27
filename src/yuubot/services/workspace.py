"""Controlled workspace service for files and allowlisted checks."""

from __future__ import annotations

import asyncio
import os
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import attrs

from yuubot.services.base import InvalidScope, AccessDenied, YuubotServiceError


def _is_master(payload: Mapping[str, Any]) -> bool:
    return str(payload.get("bot_kind", "")).lower() == "master"


def _workspace_root(payload: Mapping[str, Any]) -> Path:
    raw = str(payload.get("workspace_root", "") or "")
    if not raw:
        raise InvalidScope("workspace_root is unavailable")
    root = Path(raw).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve(payload: Mapping[str, Any], path: str) -> Path:
    root = _workspace_root(payload)
    target = (root / path).expanduser().resolve()
    if root != target and root not in target.parents:
        raise InvalidScope("workspace path traversal is not allowed")
    return target


def _int(value: object, default: int = 0) -> int:
    try:
        if isinstance(value, int | float | str | bytes | bytearray) and not isinstance(value, bool):
            return int(value)
    except (TypeError, ValueError):
        return default
    return default


_ALLOWED_COMMANDS = {
    "python",
    "python3",
    "pytest",
    "ruff",
    "ty",
    "git",
    "uv",
    "ls",
    "pwd",
}


@attrs.define
class WorkspaceService:
    async def read_file(self, payload: Mapping[str, Any]) -> str:
        path = str(payload.get("path", "") or "")
        if not path:
            raise YuubotServiceError("path is required")
        target = _resolve(payload, path)
        if not target.is_file():
            raise YuubotServiceError(f"file not found: {path}")
        max_chars = max(1, min(_int(payload.get("max_chars"), 20000), 200000))
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            return text[:max_chars] + "\n...[truncated]"
        return text

    async def write_file(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not _is_master(payload):
            raise AccessDenied("workspace writes are master-only")
        path = str(payload.get("path", "") or "")
        content = str(payload.get("content", "") or "")
        if not path:
            raise YuubotServiceError("path is required")
        target = _resolve(payload, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"status": "written", "path": str(target), "bytes": len(content.encode())}

    async def list_files(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        path = str(payload.get("path", ".") or ".")
        target = _resolve(payload, path)
        if not target.exists():
            return []
        if target.is_file():
            entries = [target]
        else:
            entries = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        root = _workspace_root(payload)
        return [
            {
                "path": str(entry.relative_to(root)),
                "is_dir": entry.is_dir(),
                "bytes": entry.stat().st_size if entry.is_file() else 0,
            }
            for entry in entries[:200]
        ]

    async def apply_patch(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not _is_master(payload):
            raise AccessDenied("workspace patching is master-only")
        patch = str(payload.get("patch", "") or "")
        if not patch.strip():
            raise YuubotServiceError("patch is empty")
        return await self.run_command(
            {
                **payload,
                "command": "git apply --whitespace=nowarn -",
                "stdin": patch,
                "timeout_s": payload.get("timeout_s", 30),
            }
        )

    async def run_command(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not _is_master(payload):
            raise AccessDenied("workspace commands are master-only")
        command = str(payload.get("command", "") or "")
        if not command:
            raise YuubotServiceError("command is required")
        argv = shlex.split(command)
        if not argv:
            raise YuubotServiceError("command is required")
        executable = Path(argv[0]).name
        if executable not in _ALLOWED_COMMANDS:
            raise AccessDenied(f"command is not allowlisted: {executable}")
        cwd = _workspace_root(payload)
        timeout_s = max(0.1, min(float(payload.get("timeout_s", 30) or 30), 120.0))
        env = os.environ.copy()
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE if payload.get("stdin") is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(
                    str(payload.get("stdin", "")).encode() if payload.get("stdin") is not None else None
                ),
                timeout=timeout_s,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return {"status": "timeout", "returncode": None, "stdout": "", "stderr": "command timed out"}
        return {
            "status": "ok" if process.returncode == 0 else "error",
            "returncode": process.returncode,
            "stdout": stdout.decode(errors="replace")[-20000:],
            "stderr": stderr.decode(errors="replace")[-20000:],
        }
