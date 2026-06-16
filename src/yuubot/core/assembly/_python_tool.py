"""Python execution tool for the yuuagents Runtime ToolRegistry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pydantic
from yuuagents import PythonKernelConfig, PythonImport
from yuuagents.python.runtime import (
    PythonRuntime,
    ResolvedPythonRuntime,
    _resolve_python,
)
from yuuagents.python.session import PythonExecResult, PythonSession
from yuuagents.tool.primitives import (
    Tool,
    ToolCallTask,
    ToolContext,
    ToolDefinition,
)

from yuubot.core.assembly._constants import FACADE_EXPAND_FUNCTIONS, FACADE_IMPORTS
from yuubot.core.facade import ActorFacadeBinding


class ExecutePythonParams(pydantic.BaseModel):
    code: str
    capture: list[str] = ["stdout", "stderr"]


class ExecutePythonResult(pydantic.BaseModel):
    output: str = ""


class ExecutePythonTool(Tool[ExecutePythonParams, ExecutePythonResult]):
    def __init__(
        self,
        runtime: Any,
        *,
        facade: ActorFacadeBinding,
        workspace_path: Path,
    ) -> None:
        super().__init__(runtime)
        self.facade = facade
        self.workspace_path = workspace_path
        self._session: PythonSession | None = None
        self._description = self._build_description(facade)

    @property
    def definition(self) -> ToolDefinition[ExecutePythonParams, ExecutePythonResult]:
        return ToolDefinition(
            name="execute_python",
            description=self._description,
            input_model=ExecutePythonParams,
            output_model=ExecutePythonResult,
        )

    def _build_description(self, facade: ActorFacadeBinding) -> str:
        imports = _facade_imports(facade)
        runtime = PythonRuntime(
            config=PythonKernelConfig(
                sys_path=tuple(facade.sys_path),
            ),
            imports=imports,
            expand_functions=_facade_expand_functions(facade),
        )
        resolved = _resolve_python(runtime, default_doc_mode="full")
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
        python_runtime = PythonRuntime(
            config=PythonKernelConfig(
                cwd=str(self.workspace_path),
                sys_path=tuple(self.facade.sys_path),
                startup_code=self.facade.startup_code,
            ),
            state=dict(self.facade.session_state),
        )
        self._session = PythonSession(
            agent_id=agent_id,
            agent_name=self.facade.agent_name,
            runtime=ResolvedPythonRuntime(
                config=python_runtime.config,
                imports=python_runtime.imports,
                state=python_runtime.state,
                expand_functions=python_runtime.expand_functions,
            ),
        )
        return self._session

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


def _facade_imports(facade: ActorFacadeBinding) -> tuple[PythonImport, ...]:
    from yuubot.core.facade import facade_module_name

    modules = {facade_module_name(cap) for cap in facade.capabilities}
    return (
        *FACADE_IMPORTS,
        *(PythonImport(module=m) for m in sorted(modules) if m != "yext"),
    )


def _facade_expand_functions(
    facade: ActorFacadeBinding,
) -> tuple[str, ...]:
    from yuubot.core.facade import facade_module_name

    modules = {facade_module_name(cap) for cap in facade.capabilities}
    return (
        *FACADE_EXPAND_FUNCTIONS,
        *(f"{m}.*" for m in sorted(modules) if m != "yext"),
    )
