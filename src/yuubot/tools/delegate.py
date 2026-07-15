"""Asynchronous subagent delegation tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal, cast

import msgspec
from attrs import define

from ..domain.messages import ConversationContext
from ..llm.gateway import GatewayClient
from ..domain.models import AliasModelSelector, ModelSelector
from ..runtime.tasks import RuntimeTaskRecord
from ..runtime.turn_limits import TurnLimitError
from .base import ToolConfig, ToolSpec

if TYPE_CHECKING:
    from ..runtime.core import Runtime

DESCRIPTION = """Use this for independent code exploration, external research, parallel evidence collection, or a separate review. Delegate a self-contained task to one asynchronous subagent and return its root Runtime Task id immediately. Up to four delegate tasks can be created from one parent user turn, and multiple calls in the same assistant response run concurrently. Delegation is one level: each subagent receives the parent's workspace, integrations, and enabled tools with delegate removed. A subagent remains active while any task in its task tree is active; child completion resumes the subagent, and only the root result is delivered to the parent conversation.

Subagents:
- explore: inspect code, files, and current implementation; locate behavior, collect evidence, and map impact.
- web-scout: research external documentation, recent facts, competitors, and source claims with search/read/fixer capabilities.
- reviewer: independently find omissions, risks, counterexamples, regressions, and missing tests in a design or change.

Model tiers:
- same: use the parent Actor model when the task needs matching capabilities or input support.
- fast: use the `fast` Alias for clear, bounded evidence collection and parallel work.
- intelligent: use the `intelligent` Alias for difficult reasoning, synthesis, review, or ambiguous work.
The parent model makes `same` available. The Gateway catalog enables `fast` and `intelligent` when those aliases declare tool support.

The subagent receives only `message`, so make it self-contained: include the objective, relevant paths or sources, scope, constraints, ownership boundaries, and expected output. For parallel edits, assign distinct files or directories in each message. Completion, failure, or cancellation returns later as a developer notice to the parent conversation."""


class DelegatePayload(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    subagent: Literal["explore", "web-scout", "reviewer"]
    model_tier: Literal["same", "fast", "intelligent"]
    message: str


class DelegateResult(msgspec.Struct, frozen=True):
    task_id: str
    status: Literal["pending", "running"]


@define
class DelegateTool:
    payload_type: ClassVar[type[msgspec.Struct]] = DelegatePayload
    context: ConversationContext
    runtime: Runtime

    async def prepare(self) -> None:
        return None

    async def execute(self, payload: msgspec.Struct) -> str:
        data = cast(DelegatePayload, payload)
        message = data.message.strip()
        if not message:
            raise ValueError("message must not be empty")
        if len(message) > 20_000:
            raise ValueError("message must be at most 20000 characters")
        depth = self.context.rpc.get("delegation_depth", 0)
        if depth != 0:
            raise RuntimeError("recursive_delegation_forbidden: delegation is available at the parent level")
        token = self.context.rpc.get("turn_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("delegate_turn_required: delegation is available during a parent user turn")
        selector = self._model_selector(data.model_tier)

        async def register() -> RuntimeTaskRecord:
            from .registry import all_tool_configs
            from ..runtime.subagents import register_agent_task

            configs = all_tool_configs()
            configs.pop("delegate", None)
            return register_agent_task(
                self.runtime,
                self.context,
                data.subagent,
                data.model_tier,
                selector,
                message,
                configs,
            )

        try:
            record = await self.runtime.turn_limits.run(token, "delegate", register)
        except TurnLimitError as exc:
            raise RuntimeError(f"{exc.code}: {exc}") from exc
        return msgspec.json.encode(DelegateResult(record.id, "running")).decode()

    async def close(self) -> None:
        return None

    def _model_selector(self, tier: str) -> ModelSelector | str:
        if tier == "same":
            return self.context.model
        client = self.runtime.gateway_client
        if isinstance(client, GatewayClient):
            enabled = (
                client.status.fast_delegate_enabled
                if tier == "fast"
                else client.status.intelligent_delegate_enabled
            )
            if not enabled:
                raise RuntimeError(f"gateway_model_unavailable: {tier} delegate tier is unavailable")
        return AliasModelSelector(tier)


def _factory(config: ToolConfig, context: ConversationContext, runtime: Runtime) -> DelegateTool:
    del config
    return DelegateTool(context, runtime)


DELEGATE_SPEC = ToolSpec(DelegatePayload, DESCRIPTION, _factory)
