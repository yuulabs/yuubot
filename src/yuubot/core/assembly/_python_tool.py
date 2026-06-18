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


class ExecutePythonResult(pydantic.BaseModel):
    output: str = ""


class ExecutePythonTool(Tool[ExecutePythonParams, ExecutePythonResult]):
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
    def definition(self) -> ToolDefinition[ExecutePythonParams, ExecutePythonResult]:
        return ToolDefinition(
            name="execute_python",
            description=self._description,
            input_model=ExecutePythonParams,
            output_model=ExecutePythonResult,
        )

    @staticmethod
    def _build_description(config: PythonRuntime) -> str:
        resolved = _resolve_python(config, default_doc_mode="full")
        return "Execute Python code in an ipykernel session.\n\n" + resolved.tool_description_suffix()

    async def create_coro(
        self, task: ToolCallTask, context: ToolContext
    ) -> ExecutePythonResult:
        session = await self._get_session(context.agent_id)
        result = await session.execute(
            task.tool_call_params.params.code,
            timeout_s=15.0,
            call_id=task.tool_call_params.tool_call_id,
            entitylog=context.entity_log,
        )
        outputs = self._render_result(result)
        return ExecutePythonResult(output=outputs)

    async def cancel(self, task: ToolCallTask, reason: str) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

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

    def _render_result(self, result: PythonExecResult) -> str:
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
        return f"Python execution {result.status}"
