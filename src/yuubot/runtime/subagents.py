"""Fixed subagent registry and ephemeral agent-task runner."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal, cast

import msgspec

from ..actor.prompt import developer_prompt
from ..chat.harness import Harness, HarnessConfig
from ..chat.history import HistoryHelper
from ..domain.messages import (
    ConversationContext,
    GenText,
    HistoryToolSpecs,
    InputMessage,
    SystemPrompt,
    text_content,
)
from ..domain.models import ModelSelector
from ..domain.stream import extract_tool_calls, merge
from ..llm.gateway import RequestMetadata
from ..tools.base import ToolConfig
from .streams import TaskCoroFactory, TextStream
from .tasks import RuntimeTaskRecord, make_owner, new_task_id
from .event_payloads import TaskUsagePayload
from .turn_limits import TurnIdentity

if TYPE_CHECKING:
    from .core import Runtime

SubagentId = Literal["explore", "web-scout", "reviewer"]
ModelTier = Literal["same", "fast", "intelligent"]


class SubagentSpec(msgspec.Struct, frozen=True):
    id: SubagentId
    persona: str


SUBAGENTS: dict[SubagentId, SubagentSpec] = {
    "explore": SubagentSpec(
        "explore",
        "You are the explore subagent. Inspect code, files, and the current implementation to locate behavior, collect concrete evidence, and map the affected surface. Prefer read-oriented inspection. Produce modifications when the delegated message explicitly requests them and the available tools permit them. Report paths, observations, uncertainty, and a concise conclusion.",
    ),
    "web-scout": SubagentSpec(
        "web-scout",
        "You are the web-scout subagent. Investigate external documentation, recent facts, competitors, and source claims. Use search, read, and cited fixer capabilities as appropriate. Return a concise synthesis with source URLs, dates when freshness matters, and clearly marked uncertainty.",
    ),
    "reviewer": SubagentSpec(
        "reviewer",
        "You are the reviewer subagent. Independently review a design or change for omissions, behavioral regressions, risks, counterexamples, and missing tests. Prefer read-oriented workspace inspection and use external sources when they materially strengthen the review. Lead with findings grounded in paths and observable scenarios.",
    ),
}


def register_agent_task(
    runtime: Runtime,
    parent_context: ConversationContext,
    subagent: SubagentId,
    model_tier: ModelTier,
    model_selector: ModelSelector | str,
    message: str,
    configs: dict[str, ToolConfig],
) -> RuntimeTaskRecord:
    task_id = new_task_id()
    turn_id = str(parent_context.rpc.get("turn_id", ""))
    trace_id = str(parent_context.otel.get("trace_id") or parent_context.conversation_id)
    record = RuntimeTaskRecord(
        task_id,
        make_owner(parent_context.actor, parent_context.conversation_id),
        "agent",
        f"{subagent}:{task_id}",
        message,
        interactive=False,
        delivery="conversation",
        metadata={
            "parent_actor_id": parent_context.actor,
            "parent_conversation_id": parent_context.conversation_id,
            "parent_turn_id": turn_id,
            "subagent": subagent,
            "model_tier": model_tier,
            "model_selector": model_selector,
            "trace_id": trace_id,
            "parent_span_id": str(parent_context.otel.get("span_id") or ""),
        },
    )
    runtime.tasks.put(record)
    runtime.scheduler.schedule(
        record,
        _agent_coro_factory(
            runtime,
            record,
            parent_context,
            SUBAGENTS[subagent],
            model_selector,
            message,
            configs,
        ),
    )
    return record


def _agent_coro_factory(
    runtime: Runtime,
    record: RuntimeTaskRecord,
    parent_context: ConversationContext,
    subagent: SubagentSpec,
    model_selector: ModelSelector | str,
    message: str,
    configs: dict[str, ToolConfig],
) -> TaskCoroFactory:
    async def run(_stdin: TextStream, stdout: TextStream) -> str:
        from ..tools.registry import tool_specs

        token = runtime.turn_limits.open(
            TurnIdentity(
                parent_context.actor,
                f"subagent:{record.id}",
                str(record.metadata.get("parent_turn_id", "")),
                str(record.metadata.get("trace_id", record.id)),
            )
        )
        context = ConversationContext(
            model_selector,
            f"subagent:{record.id}",
            parent_context.actor,
            parent_context.workspace,
            parent_context.integrations,
            {
                "trace_id": record.metadata.get("trace_id", ""),
                "parent_span_id": record.metadata.get("parent_span_id", ""),
                "task_id": record.id,
            },
            {
                **parent_context.rpc,
                "turn_token": token,
                "delegation_depth": 1,
            },
            parent_context.model_supports_vision,
        )
        prompt = developer_prompt(
            subagent.persona,
            context.workspace,
            list(runtime.integrations.values()),
            actor_id=context.actor,
            has_python=any(config.type == "execute_python" for config in configs.values()),
        )
        history = HistoryHelper(
            [
                HistoryToolSpecs(tool_specs(configs)),
                SystemPrompt(prompt),
                InputMessage("user", context.actor, text_content(message)),
            ]
        )
        harness = Harness.from_config(HarnessConfig(configs), context, runtime)
        stop_event = asyncio.Event()
        try:
            while True:
                events = [
                    event
                    async for event in runtime.gateway_client.stream(
                        history.to_llm_input(),
                        model_selector,
                        context,
                        runtime.cache,
                        stop_event,
                        RequestMetadata(
                            str(record.metadata.get("trace_id", record.id)),
                            parent_context.actor,
                            context.conversation_id,
                            "delegate",
                            record.id,
                            parent_context.conversation_id,
                            subagent.id,
                        ).to_dict(),
                    )
                ]
                outputs, stop = merge(events)
                history.extend(outputs)
                account = {**stop.account, "task_id": record.id, "purpose": "delegate"}
                await runtime.state.append_usage(record.id, stop.usage, account)
                usage_records = cast(list[object], record.metadata.setdefault("usage", []))
                if isinstance(usage_records, list):
                    usage_records.append(
                        {
                            "usage": msgspec.to_builtins(stop.usage),
                            "account": account,
                        }
                    )
                runtime.emit(
                    TaskUsagePayload(
                        record.id,
                        stop.usage.input_tokens,
                        stop.usage.cached_input_tokens,
                        stop.usage.cache_write_tokens,
                        stop.usage.output_tokens,
                        account,
                    )
                )
                if stop.reason == "stop":
                    result = "".join(item.text for item in outputs if isinstance(item, GenText)).strip()
                    stdout.write(result)
                    return result
                if stop.reason not in {"tool_calls", "function_call"}:
                    raise RuntimeError(f"subagent stopped: {stop.reason}")
                results = await harness.gather(extract_tool_calls(outputs), stop_event)
                history.extend(results)
        finally:
            runtime.turn_limits.close(token)
            await harness.close()

    return run
