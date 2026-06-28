"""Python execution tool for the yuuagents Runtime ToolRegistry."""

from __future__ import annotations

from typing import Any

import pydantic
from yuuagents import PythonRuntime, ResolvedPythonRuntime
from yuuagents.python.runtime import _resolve_python
from yuuagents.python.session import PythonExecResult, PythonSession
from yuuagents.tool.primitives import (
    Tool,
    ToolCallTask,
    ToolContext,
    ToolDefinition,
)


class ExecutePythonParams(pydantic.BaseModel):
    code: str
    capture: list[str] = ["stdout", "stderr"]


class ExecutePythonTool(Tool[ExecutePythonParams, str]):
    config_type = PythonRuntime

    def __init__(self, runtime: Any, *, config: PythonRuntime) -> None:
        self.runtime = runtime
        self.config = config
        self._session: PythonSession | None = None
        self._description = self._build_description(config)

    @classmethod
    def from_startup(
        cls,
        runtime: Any,
        config: PythonRuntime,
    ) -> ExecutePythonTool:
        return cls(runtime, config=config)

    @property
    def definition(self) -> ToolDefinition[ExecutePythonParams, str]:
        return ToolDefinition(
            name="execute_python",
            description=self._description,
            input_model=ExecutePythonParams,
        )

    @staticmethod
    def _build_description(config: PythonRuntime) -> str:
        resolved = _resolve_python(config, default_doc_mode="full")
        return "Execute Python code in an ipykernel session.\n\n" + resolved.tool_description_suffix()

    async def create_coro(
        self, task: ToolCallTask, context: ToolContext
    ) -> str:
        session = await self._get_session(context.agent_id)
        params = ExecutePythonParams.model_validate(task.tool_call_params.params)
        result = await session.execute(
            params.code,
            timeout_s=15.0,
            call_id=task.tool_call_params.tool_call_id,
            entitylog=context.entity_log,
        )
        rendered = self._render_result(result)
        if result.status == "crashed":
            reset_note = "The Python session was reset; retry after fixing the code or environment."
            try:
                await session.close()
            except Exception as exc:
                reset_note = (
                    "The Python session reset failed: "
                    f"{type(exc).__name__}: {exc}"
                )
            finally:
                self._session = None
            return f"{rendered}\n{reset_note}"
        return rendered

    async def cancel(self, task: ToolCallTask, reason: str) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def restart_session(self) -> None:
        """Close the current kernel session handle and drop it.

        Lazy restart semantics: the next ``create_coro`` call sees
        ``self._session is None`` and re-spawns a fresh kernel via
        ``_get_session`` using the SAME ``config`` (so the same workspace
        ``.venv`` interpreter is reused). Safe to call when no session was
        ever started — the no-op close path mirrors the crash-reset behaviour.
        """
        await self.close_session()

    async def close_session(self) -> bool:
        """Close the current kernel session and return whether one existed."""
        session = self._session
        if session is None:
            return False
        self._session = None
        try:
            await session.close()
        except Exception:
            # Tolerate close errors — the handle is already dropped, and the
            # crash-reset path in ``create_coro`` is equally tolerant.
            pass
        return True

    async def _get_session(self, agent_id: str) -> PythonSession:
        if self._session is not None:
            return self._session
        self._session = PythonSession(
            agent_id=agent_id,
            agent_name=self._agent_name(),
            runtime=ResolvedPythonRuntime(
                config=self.config.config,
                imports=self.config.imports,
                state=self.config.state,
                expand_functions=self.config.expand_functions,
            ),
        )
        return self._session

    def _agent_name(self) -> str:
        return str(self.config.state.get("agent_name", ""))

    @staticmethod
    def _render_result(result: PythonExecResult) -> str:
        if result.status == "ok":
            parts: list[str] = []
            if result.stdout:
                parts.append(f"Captured stdout:\n{result.stdout}")
            if result.stderr:
                parts.append(f"Captured stderr:\n{result.stderr}")
            for item in result.items:
                text = item.mime.data.get("text/plain")
                if text:
                    parts.append(str(text))
            return "\n".join(parts)
        if result.status == "error":
            tb = "\n".join(result.traceback) if result.traceback else result.stderr
            return f"Python execution error:\n{tb}"
        if result.status == "crashed":
            tb = "\n".join(result.traceback) if result.traceback else result.stderr
            if tb:
                return f"Python execution crashed:\n{tb}"
            return "Python execution crashed"
        return f"Python execution {result.status}"
