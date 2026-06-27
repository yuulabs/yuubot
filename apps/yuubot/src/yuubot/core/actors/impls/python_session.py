"""Actor-owned Python sessions with yb/yext facade bindings."""

from __future__ import annotations

import ast
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from uuid import uuid4

from yuuagents import Budget, MailBox, PythonKernelConfig
from yuuagents.python.runtime import PythonRuntime, ResolvedPythonRuntime
from yuuagents.python.session import PythonSession

from yuubot.core.bindings import AgentBinding
from yuubot.core.facade import (
    ActorFacadeBinding,
    FacadeWorkspace,
    IntegrationInvokeBridge,
)
from yuubot.core.integrations import IntegrationCore
from yuubot.core.integrations.contracts import VisibleIntegrationSurface


@dataclass
class ActorPythonSessionFactory:
    integrations: IntegrationCore
    workspace: FacadeWorkspace
    bridge: IntegrationInvokeBridge
    name: str = "actor-python-sessions"
    _started: bool = False

    @classmethod
    def in_directory(
        cls,
        *,
        integrations: IntegrationCore,
        root: Path,
        mailbox_for_actor: Callable[[str], MailBox | None] | None = None,
        schedule_for_actor: Callable[
            [str, str, str, dict[str, object]], Awaitable[object]
        ]
        | None = None,
    ) -> "ActorPythonSessionFactory":
        return cls(
            integrations=integrations,
            workspace=FacadeWorkspace(root),
            bridge=IntegrationInvokeBridge(
                integrations,
                mailbox_for_actor=mailbox_for_actor,
                schedule_for_actor=schedule_for_actor,
            ),
        )

    async def start(self) -> None:
        if self._started:
            return
        await self.bridge.start()
        self.workspace.generate_catalog(self.integrations.declared_capability_specs())
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        await self.bridge.stop()
        self._started = False

    async def bind_facade(
        self,
        binding: AgentBinding,
        *,
        mailbox_id: str,
    ) -> ActorFacadeBinding:
        if not self._started:
            await self.start()
        owner_id = binding.owner_id
        session_id = f"{owner_id}-{uuid4()}"
        return self.workspace.bind_actor(
            actor_id=owner_id,
            agent_name=binding.agent_name,
            session_id=session_id,
            mailbox_id=mailbox_id,
            surfaces=await self._visible_surfaces(binding),
            endpoint=self.bridge.endpoint,
        )

    async def create(self, binding: AgentBinding) -> "ExecutePythonSession":
        facade = await self.bind_facade(
            binding,
            mailbox_id=f"python-session:{binding.owner_id}",
        )
        session = ExecutePythonSession(
            session_id=facade.session_id,
            facade=facade,
            bridge=self.bridge,
            cwd=binding.require_workspace_path(),
        )
        await session.warmup()
        return session

    def cleanup_actor(self, actor_id: str) -> None:
        self.workspace.cleanup_actor(actor_id)

    async def _visible_surfaces(
        self,
        binding: AgentBinding,
    ) -> list[VisibleIntegrationSurface]:
        """Integration SDK surfaces visible to this actor's facade (§2.7.1).

        CapabilitySets declare selected integrations by ``integration_ids``
        (``IntegrationRecord.id``). The visible SDK surfaces derive from the
        selected integration instances that are currently running; whether an
        integration is actually running is also enforced at invoke time by
        the IntegrationCore authorisation boundary, so a non-running selection
        contributes neither facade imports nor prompt content.
        """
        return self.integrations.visible_integration_surfaces(
            binding.capability_set.integration_ids
        )


@dataclass
class ExecutePythonSession:
    session_id: str
    facade: ActorFacadeBinding
    bridge: IntegrationInvokeBridge
    cwd: Path
    submit_timeout_s: float = 15.0
    budget: Budget = field(default_factory=lambda: Budget(limits={}))
    _session: PythonSession | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        runtime = PythonRuntime(
            config=PythonKernelConfig(
                cwd=str(self.cwd),
                sys_path=tuple(self.facade.sys_path),
                startup_code=self.facade.startup_code,
            ),
            state=dict(self.facade.session_state),
        )
        self._session = PythonSession(
            agent_id=self.session_id,
            agent_name=self.facade.agent_name,
            runtime=ResolvedPythonRuntime(
                config=runtime.config,
                imports=runtime.imports,
                state=runtime.state,
                expand_functions=runtime.expand_functions,
            ),
            session_id=self.session_id,
        )

    async def warmup(self) -> None:
        await self.execute("pass")

    async def call_facade(
        self,
        *,
        call_path: str,
        value: object,
    ) -> dict[str, object]:
        code = f"await {call_path}(value={value!r})"
        return extract_execute_python_result(await self.execute(code))

    async def execute(self, code: str) -> object:
        if self._session is None:
            raise RuntimeError("Python session not initialized")
        result = await self._session.execute(
            code,
            timeout_s=self.submit_timeout_s,
        )
        if result.status == "ok":
            outputs: list[dict[str, str]] = []
            if result.stdout:
                outputs.append({"type": "text", "text": result.stdout})
            for item in result.items:
                text = item.mime.data.get("text/plain")
                if text:
                    outputs.append({"type": "text", "text": str(text)})
            return outputs
        if result.status == "error":
            detail = "\n".join(result.traceback) if result.traceback else result.stderr
            raise RuntimeError(detail)
        raise RuntimeError(f"Python execution {result.status}")

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()


def extract_execute_python_result(output: object) -> dict[str, object]:
    for text in _tool_output_texts(output):
        if text.startswith("Captured stdout:"):
            continue
        with suppress(ValueError, SyntaxError):
            result = ast.literal_eval(text)
            if not isinstance(result, dict):
                raise TypeError("facade result must be a JSON object")
            return result
    raise RuntimeError(f"execute_python did not return a yuubot result: {output!r}")


def _tool_output_texts(output: object) -> list[str]:
    if isinstance(output, str):
        return [output]
    if not isinstance(output, list):
        return [repr(output)]
    texts: list[str] = []
    for item in output:
        if isinstance(item, Mapping):
            data = cast(Mapping[str, object], item)
            if data.get("type") == "text":
                texts.append(str(data.get("text", "")))
                continue
            texts.append(repr(item))
        else:
            texts.append(repr(item))
    return texts
