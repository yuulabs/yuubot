from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from attrs import define
from tortoise import connections

from yuuagents.providers.ipykernel import IpykernelExecutor
from yuuagents.budget import UsageSink
from yuuagents.python_runtime import (
    JsonSessionState,
    PythonImport,
    PythonKernelConfig,
    PythonRuntime,
    _optional_str_tuple,
    _resolve_python,
)

from yuubot.daemon.restricted_python import RestrictedPythonSession, RestrictedPythonWorker

# ── MIME lookup ────────────────────────────────────────────────────────

_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".txt": "text/plain",
    ".py": "text/plain",
    ".json": "application/json",
    ".csv": "text/plain",
    ".html": "text/plain",
    ".md": "text/plain",
    ".log": "text/plain",
    ".yaml": "text/plain",
    ".toml": "text/plain",
    ".pdf": "application/pdf",
}


def _norm_path(path: str) -> str:
    p = path.strip()
    if p.startswith("file://"):
        p = p[7:]
    return p


async def _chat_media_paths(ctx_id: int) -> frozenset[str]:
    conn = connections.get("default")
    _, rows = await conn.execute_query(
        "SELECT media_files FROM messages WHERE ctx_id = ? AND media_files IS NOT NULL "
        "ORDER BY id DESC LIMIT 500",
        [ctx_id],
    )
    paths: set[str] = set()
    for row in rows:
        raw = str(row[0] or "")
        try:
            items = json.loads(raw) if raw.startswith("[") else [raw]
        except Exception:
            continue
        for item in items:
            p = str(item).strip()
            if p.startswith("file://"):
                p = p[7:]
            if p.startswith("/"):
                paths.add(p)
    return frozenset(paths)


# ── Executor ───────────────────────────────────────────────────────────


@define
class ReadChatFileExecutor:
    """Reads media files from the current chat context only.

    File access is restricted to paths recorded in the ``media_files``
    column of the ``messages`` table for the given ``ctx_id``.
    """

    ctx_id: int

    def __contains__(self, tool_name: str) -> bool:
        return tool_name == "read_chat_file"

    async def run(
        self, tool_name: str, payload: dict[str, Any], sink: UsageSink
    ) -> Any:
        sink.declare_free("read_chat_file has no direct cost")

        path = payload.get("path", "")
        if not isinstance(path, str):
            raise TypeError("read_chat_file requires a string 'path' argument")

        allowed = await _chat_media_paths(self.ctx_id)
        norm = _norm_path(path)
        if norm not in allowed:
            return f"[error] File not in chat media: {path}"

        p = Path(norm)
        if not p.exists():
            return f"[error] File not found: {norm}"

        content = p.read_bytes()
        mime = _MIME.get(p.suffix.lower(), "application/octet-stream")

        if mime.startswith("image/"):
            b64 = base64.b64encode(content).decode()
            return [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
            ]

        return content.decode("utf-8", errors="replace")

    async def aclose(self) -> None:
        pass


# ── Provider ───────────────────────────────────────────────────────────


@define
class ReadChatFileProvider:
    """Factory for ReadChatFileExecutor, scoped to a chat context via ctx_id."""

    def create_executor(self, capability: dict[str, Any]) -> ReadChatFileExecutor:
        ctx_id = capability.get("ctx_id", 0)
        if not isinstance(ctx_id, int):
            raise TypeError(
                "ReadChatFileProvider requires an integer 'ctx_id' in capability"
            )
        return ReadChatFileExecutor(ctx_id=ctx_id)

    def create_tool_specs(
        self, prompt_config: dict[str, Any]
    ) -> list[dict[str, Any]]:
        level = prompt_config.get("level", "detail")
        if level == "type-only":
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "read_chat_file",
                        "description": "Read a file from the current chat media.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                }
            ]
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_chat_file",
                    "description": (
                        "Read a media file from the current chat context. "
                        "Only files shared in this conversation are accessible. "
                        "Images return base64-encoded data."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "File path from the chat media.",
                            },
                        },
                        "required": ["path"],
                    },
                },
            }
        ]


@define
class RestrictedPythonProvider:
    """Provider exposing execute_python through yuubot's RestrictedPython worker."""

    config: PythonKernelConfig
    worker: RestrictedPythonWorker

    @classmethod
    def create(cls, config: PythonKernelConfig | None = None) -> "RestrictedPythonProvider":
        return cls(
            config=config or PythonKernelConfig(),
            worker=RestrictedPythonWorker(),
        )

    def create_executor(self, capability: dict[str, Any]) -> IpykernelExecutor:
        imports_raw = capability.get("imports", [])
        imports = tuple(
            PythonImport(
                module=imp["module"] if isinstance(imp, dict) else str(imp),
                alias=imp.get("alias") if isinstance(imp, dict) else None,
            )
            for imp in imports_raw
        )
        state_raw = capability.get("state", {})
        runtime = PythonRuntime(
            config=self.config,
            imports=imports,
            state=JsonSessionState(state_raw if isinstance(state_raw, dict) else {}),
            expand_functions=_optional_str_tuple(capability.get("expand_functions")),
        )
        resolved = _resolve_python(runtime)
        session = RestrictedPythonSession(
            worker=self.worker,
            session_id=f"restricted-{uuid4().hex}",
            runtime=resolved,
        )
        return IpykernelExecutor(_session=session, _runtime=resolved)

    def create_tool_specs(self, prompt_config: dict[str, Any]) -> list[dict[str, Any]]:
        from yuuagents.providers import IpykernelProvider

        return IpykernelProvider(config=self.config).create_tool_specs(prompt_config)

    def create_tool_specs_for_executor(
        self,
        prompt_config: dict[str, Any],
        executor: IpykernelExecutor,
    ) -> list[dict[str, Any]]:
        from yuuagents.providers import IpykernelProvider

        return IpykernelProvider(config=self.config).create_tool_specs_for_executor(
            prompt_config,
            executor,
        )

    async def aclose(self) -> None:
        self.worker.stop()
