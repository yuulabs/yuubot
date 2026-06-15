"""Actor-owned Python sessions with yb/yext facade bindings."""

from __future__ import annotations

import ast
import json
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from uuid import uuid4

import yuullm
import msgspec
from yuuagents import (
    Budget,
    EventBus,
    MailBox,
    PythonKernelConfig,
    Runtime,
    TaskCompleted,
    TaskDetached,
    TaskFailed,
)
from yuuagents.tool_backends import IpykernelToolBackend
from yuuagents.tool_backends.ipykernel import PythonToolConfig

from yuubot.core.bindings import ActorBinding
from yuubot.core.capabilities import AnyCapabilitySpec
from yuubot.core.facade import (
    ActorFacadeBinding,
    FacadeWorkspace,
    IntegrationInvokeBridge,
)
from yuubot.core.integrations import IntegrationCore


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
        binding: ActorBinding,
        *,
        mailbox_id: str,
    ) -> ActorFacadeBinding:
        if not self._started:
            await self.start()
        actor_id = binding.actor.id
        session_id = f"{actor_id}-{uuid4()}"
        return self.workspace.bind_actor(
            actor_id=actor_id,
            agent_name=binding.actor.name,
            session_id=session_id,
            mailbox_id=mailbox_id,
            capabilities=self._visible_capabilities(binding),
            endpoint=self.bridge.endpoint,
        )

    async def create(self, binding: ActorBinding) -> "ExecutePythonSession":
        facade = await self.bind_facade(
            binding,
            mailbox_id=f"python-session:{binding.actor.id}",
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

    def _visible_capabilities(
        self,
        binding: ActorBinding,
    ) -> list[AnyCapabilitySpec]:
        allowed = set(binding.actor.allowed_capability_ids)
        return [
            capability
            for capability in self.integrations.declared_capability_specs()
            if capability.id in allowed
        ]


@dataclass
class ExecutePythonSession:
    session_id: str
    facade: ActorFacadeBinding
    bridge: IntegrationInvokeBridge
    cwd: Path
    submit_timeout_s: float = 15.0
    budget: Budget = field(default_factory=lambda: Budget(limits={}))
    _runtime: Runtime = field(init=False)

    def __post_init__(self) -> None:
        eventbus = EventBus()
        self._runtime = Runtime(eventbus=eventbus)
        backend = IpykernelToolBackend(
            config=PythonKernelConfig(
                cwd=str(self.cwd),
                sys_path=tuple(self.facade.sys_path),
                startup_code=self.facade.startup_code,
            ),
        )
        executor = backend.create_executor(
            msgspec.to_builtins(PythonToolConfig(state=self.facade.session_state))
        )
        self._runtime.add_executors(
            self.session_id,
            {"ipykernel": executor},
            owned=True,
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
        tool_call = yuullm.ToolCall(
            id=f"call-{uuid4()}",
            name="execute_python",
            arguments=json.dumps(
                {
                    "code": code,
                    "capture": ["stdout", "stderr"],
                },
                ensure_ascii=True,
            ),
        )
        task = self._runtime.submit(
            self.session_id,
            tool_call,
            self.budget,
        )
        outcome = await task.wait_foreground(self.submit_timeout_s)
        if isinstance(outcome, TaskCompleted):
            return outcome.result
        if isinstance(outcome, TaskFailed):
            raise outcome.error
        if isinstance(outcome, TaskDetached):
            return outcome.partial
        raise RuntimeError(f"unexpected execute_python outcome: {outcome!r}")

    async def close(self) -> None:
        await self._runtime.remove_agent(self.session_id)


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
