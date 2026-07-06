"""Restart the ipykernel worker to reload dependencies after uv add/remove."""

from __future__ import annotations

from typing import ClassVar

import msgspec
from attrs import define

from ..domain.messages import ConversationContext
from ..python.pool import KernelPool
from .base import ToolConfig, ToolSpec

from ..runtime.core import Runtime

DESCRIPTION = """Restart the Python kernel worker for this workspace.

Call this after `uv add` or `uv remove` so the next `execute_python` runs in a fresh process that can import newly installed packages. This immediately terminates this conversation's leased worker and the current actor's idle workers; the next `execute_python` cold-starts a new kernel.

This does not run code or change workspace files. It only resets the in-process import cache."""


class RestartKernelPayload(msgspec.Struct, frozen=True, kw_only=True):
    pass


@define
class RestartKernelTool:
    payload_type: ClassVar[type[msgspec.Struct]] = RestartKernelPayload

    pool: KernelPool
    lease_key: str

    async def prepare(self) -> None:
        return None

    async def execute(self, payload: msgspec.Struct) -> str:
        del payload
        await self.pool.purge_for_restart(self.lease_key)
        return "ok"

    async def close(self) -> None:
        return None


def _factory(config: ToolConfig, context: ConversationContext, runtime: Runtime) -> RestartKernelTool:
    del config
    return RestartKernelTool(pool=runtime.actors[context.actor].kernels, lease_key=context.conversation_id)


RESTART_KERNEL_SPEC = ToolSpec(
    payload_type=RestartKernelPayload,
    description=DESCRIPTION,
    factory=_factory,
)
