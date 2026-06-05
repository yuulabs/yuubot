from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import msgspec
import pytest
import yuullm
from yuuagents.mailbox import ScheduleTriggerMessage

from helpers import make_actor_record, make_character_record, make_llm_backend_record
from yuubot.bootstrap.config import YuuAgentsConfig
from yuubot.core.assembly import start_yuuagents_actor
from yuubot.core.bindings import ActorBinding
from yuubot.core.llm import BoundLLM
from yuubot.resources.records import RuntimePolicy, ToolConfig, YuuAgentBudget


@pytest.mark.asyncio
async def test_runtime_rollover_compacts_history_when_token_threshold_is_reached(
    tmp_path: Path,
) -> None:
    llm = RolloverLlm()
    character = make_character_record("actor-1", system_prompt="Base prompt.")
    backend = make_llm_backend_record("actor-1")
    actor = msgspec.structs.replace(
        make_actor_record(
            "actor-1",
            character=character,
            llm_backend=backend,
            max_steps=10,
        ),
        budget=YuuAgentBudget(max_steps=10, max_tokens=10),
        runtime_policy=RuntimePolicy(rollover_enabled=True, summarize_steps_span=8),
    )
    binding = ActorBinding(
        actor=actor,
        character=character,
        llm=BoundLLM(backend=backend, model="gpt-4", stream_options={}),
        workspace_path=tmp_path,
    )
    runtime = start_yuuagents_actor(
        binding,
        yuuagents_config=YuuAgentsConfig(),
        llm_client=llm,
    )
    try:
        agent = await runtime.handle_message(
            ScheduleTriggerMessage(
                agent_name=actor.name,
                content=yuullm.user("first task details"),
            )
        )
        assert agent is not None

        assert len(llm.calls) == 2
        _summary_messages, summary_tools = yuullm.split_history(llm.calls[1])
        assert summary_tools is None
        history_text = "\n".join(
            yuullm.render_message_text(message)
            for message in yuullm.split_history(agent.history)[0]
        )
        assert "rolled summary" in history_text
        assert "first task details" not in history_text
        assert agent.budget.usage.get("tokens", 0) == 0
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_runtime_expires_idle_agent_and_recreates_on_next_message(
    tmp_path: Path,
) -> None:
    llm = SimpleLlm()
    character = make_character_record("actor-1", system_prompt="Base prompt.")
    backend = make_llm_backend_record("actor-1")
    actor = msgspec.structs.replace(
        make_actor_record(
            "actor-1",
            character=character,
            llm_backend=backend,
            max_steps=10,
        ),
        runtime_policy=RuntimePolicy(idle_timeout_s=0.01),
    )
    binding = ActorBinding(
        actor=actor,
        character=character,
        llm=BoundLLM(backend=backend, model="gpt-4", stream_options={}),
        workspace_path=tmp_path,
    )
    runtime = start_yuuagents_actor(
        binding,
        yuuagents_config=YuuAgentsConfig(),
        llm_client=llm,
    )
    try:
        first = await runtime.handle_message(
            ScheduleTriggerMessage(
                agent_name=actor.name,
                content=yuullm.user("first"),
            )
        )
        assert first is not None
        first_agent_id = first.agent_id

        await asyncio.sleep(0.05)
        assert first_agent_id not in runtime.agents
        assert actor.name not in runtime.agents_by_name

        second = await runtime.handle_message(
            ScheduleTriggerMessage(
                agent_name=actor.name,
                content=yuullm.user("second"),
            )
        )
        assert second is not None
        assert second.agent_id != first_agent_id
        assert len(llm.calls) == 2
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_runtime_delegate_uses_independent_agent_and_returns_text(
    tmp_path: Path,
) -> None:
    llm = SimpleLlm()
    character = make_character_record("actor-1", system_prompt="Base prompt.")
    backend = make_llm_backend_record("actor-1")
    actor = make_actor_record(
        "actor-1",
        character=character,
        llm_backend=backend,
        max_steps=10,
    )
    binding = ActorBinding(
        actor=actor,
        character=character,
        llm=BoundLLM(backend=backend, model="gpt-4", stream_options={}),
        workspace_path=tmp_path,
    )
    runtime = start_yuuagents_actor(
        binding,
        yuuagents_config=YuuAgentsConfig(),
        llm_client=llm,
    )
    try:
        result = await runtime.run_delegate(
            task_id="task-1",
            prompt="inspect the logs",
            parent_agent_name=actor.name,
            delegate_name="scout",
        )

        assert result == "ok"
        assert not runtime.agents
        assert "inspect the logs" in "\n".join(
            yuullm.render_message_text(message)
            for message in yuullm.split_history(llm.calls[0])[0]
        )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_runtime_schedule_tool_uses_actor_schedule_executor(
    tmp_path: Path,
) -> None:
    llm = SimpleLlm()
    character = make_character_record("actor-1", system_prompt="Base prompt.")
    backend = make_llm_backend_record("actor-1")
    actor = msgspec.structs.replace(
        make_actor_record(
            "actor-1",
            character=character,
            llm_backend=backend,
            max_steps=10,
        ),
        agent_tools=(ToolConfig(provider_key="schedule"),),
    )
    binding = ActorBinding(
        actor=actor,
        character=character,
        llm=BoundLLM(backend=backend, model="gpt-4", stream_options={}),
        workspace_path=tmp_path,
    )
    runtime = start_yuuagents_actor(
        binding,
        yuuagents_config=YuuAgentsConfig(
            tool_backends={"schedule": {"db_path": str(tmp_path / "schedule.db")}}
        ),
        llm_client=llm,
    )
    try:
        created = await runtime.run_schedule_tool(
            agent_name=actor.name,
            tool_name="create_cron",
            payload={
                "cron": "0 0 * * *",
                "actions": ("agent:actor-1:nightly check",),
                "job_id": "nightly",
            },
        )
        listed = await runtime.run_schedule_tool(
            agent_name=actor.name,
            tool_name="list_crons",
            payload={},
        )

        assert created == "Created cron job nightly: 0 0 * * *"
        assert "nightly: 0 0 * * *" in str(listed)
    finally:
        await runtime.close()


class RolloverLlm:
    def __init__(self) -> None:
        self.calls: list[yuullm.History] = []

    async def stream(
        self,
        history: yuullm.History,
        **kwargs: Any,
    ) -> yuullm.StreamResult:
        _ = kwargs
        self.calls.append(list(history))
        text = "normal response" if len(self.calls) == 1 else "rolled summary"

        async def stream_items() -> AsyncIterator[yuullm.StreamItem]:
            yield yuullm.Response({"type": "text", "text": text})

        return stream_items(), yuullm.Store(
            usage=yuullm.Usage(
                provider="fake",
                model="fake",
                input_tokens=5,
                output_tokens=4,
            )
        )


class SimpleLlm:
    def __init__(self) -> None:
        self.calls: list[yuullm.History] = []

    async def stream(
        self,
        history: yuullm.History,
        **kwargs: Any,
    ) -> yuullm.StreamResult:
        _ = kwargs
        self.calls.append(list(history))

        async def stream_items() -> AsyncIterator[yuullm.StreamItem]:
            yield yuullm.Response({"type": "text", "text": "ok"})

        return stream_items(), yuullm.Store()
