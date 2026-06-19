"""Tests for Stage config and ExampleActor routing."""

from __future__ import annotations


import pytest
import yuullm

import yuuagents as ya
from yuuagents.agent.definition import AgentDefinition, LlmConfig, PromptDefinition
from yuuagents.core.eventbus import EventBus
from yuuagents.core.mailbox import MailBox
from yuuagents.core.runtime import Runtime
from yuuagents.tool.primitives import ToolRegistry

from .conftest import FakeSessionFactory, text_response

FAKE_LLM_CONFIG = LlmConfig(provider="fake", model="fake-model")


# ---------------------------------------------------------------------------
# Stage.from_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stage_from_config_runs_registered_agent() -> None:
    llm = FakeSessionFactory([[text_response("handled")]])
    stage = ya.Stage(
        mailbox=MailBox(),
        eventbus=EventBus(),
        runtime=Runtime(registry=ToolRegistry(), eventbus=EventBus()),
        llm_session_factories={"fake": llm},
    )
    actor = ya.ExampleActor(stage, [AgentDefinition(name="main", llm=FAKE_LLM_CONFIG)])

    await stage.mailbox.send(
        ya.ScheduleTriggerMessage(
            agent_name="main",
            content=yuullm.user("wake up"),
        )
    )
    agent = await actor.run_once()

    assert agent is not None
    assert agent.done
    messages, _tools = yuullm.split_history(agent.history)
    assert yuullm.render_message_text(messages[-1]) == "handled"


def test_stage_from_config_accepts_provider_pool() -> None:
    pool = yuullm.ProviderPool()
    stage = ya.Stage.from_config(
        provider_pool=pool,
        llm_provider="main",
        llm_options={"main": {"temperature": 0.2}},
    )

    assert isinstance(
        stage.llm_session_factories["main"], ya.ProviderPoolSessionFactory
    )
    assert stage.llm_options["main"] == {"temperature": 0.2}


@pytest.mark.asyncio
async def test_stage_from_config_uses_passed_llm_session_factory() -> None:
    llm = FakeSessionFactory([[text_response("custom handled")]])
    stage = ya.Stage.from_config(
        llm_session_factories={"typed-test-llm": llm},
        llm_options={"typed-test-llm": {"marker": "ok"}},
    )
    actor = ya.ExampleActor(
        stage,
        [AgentDefinition(name="main", llm=LlmConfig(provider="typed-test-llm"))],
    )

    await stage.mailbox.send(
        ya.ScheduleTriggerMessage(
            agent_name="main",
            content=yuullm.user("hello"),
        )
    )
    agent = await actor.run_once()

    assert agent is not None
    messages, _tools = yuullm.split_history(agent.history)
    assert yuullm.render_message_text(messages[-1]) == "custom handled"
    assert stage.llm_options["typed-test-llm"] == {"marker": "ok"}


# ---------------------------------------------------------------------------
# Agent LLM stream options
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_definition_model_is_reported_as_selected_model() -> None:
    bus = EventBus()
    events: list[ya.RuntimeEvent] = []
    bus.subscribe(lambda event: events.append(event))
    llm = FakeSessionFactory([[text_response("ok")]])
    stage = ya.Stage(
        mailbox=MailBox(),
        eventbus=bus,
        runtime=Runtime(registry=ToolRegistry(), eventbus=bus),
        llm_session_factories={"fake": llm},
        llm_options={"fake": {"max_tokens": 512, "temperature": 0.1}},
    )
    definition = AgentDefinition(
        name="main",
        llm=LlmConfig(
            provider="fake",
            model="override-model",
            stream_options={"temperature": 0.7, "top_p": 0.9},
        ),
        prompt=PromptDefinition(system="test"),
    )

    agent = ya.create_agent(stage, definition)
    assert agent.llm.model == "override-model"
    agent.append(yuullm.user("Hi"))
    await agent.step()
    assert agent.done


@pytest.mark.asyncio
async def test_agent_uses_declared_llm_provider() -> None:
    anthropic = FakeSessionFactory([[text_response("wrong")]])
    openai = FakeSessionFactory([[text_response("ok")]])
    stage = ya.Stage(
        mailbox=MailBox(),
        eventbus=EventBus(),
        runtime=Runtime(registry=ToolRegistry(), eventbus=EventBus()),
        llm_session_factories={
            "anthropic": anthropic,
            "openai": openai,
        },
        llm_options={
            "anthropic": {"temperature": 0.1},
            "openai": {"temperature": 0.7},
        },
    )

    agent = ya.create_agent(
        stage,
        AgentDefinition(llm=LlmConfig(provider="openai", model="gpt-4o")),
    )
    agent.append(yuullm.user("Hi"))
    await agent.step()

    messages, _tools = yuullm.split_history(agent.history)
    assert yuullm.render_message_text(messages[-1]) == "ok"


def test_create_agent_accepts_external_agent_id() -> None:
    stage = ya.Stage(
        mailbox=MailBox(),
        eventbus=EventBus(),
        runtime=Runtime(registry=ToolRegistry(), eventbus=EventBus()),
        llm_session_factories={"fake": FakeSessionFactory([])},
    )
    definition = AgentDefinition(name="main", llm=FAKE_LLM_CONFIG)

    agent = ya.create_agent(stage, definition, agent_id="agent-fixed")

    assert agent.id == "agent-fixed"


# ---------------------------------------------------------------------------
# ExampleActor message routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_actor_run_once_routes_schedule_trigger_to_registered_agent() -> None:
    from yuuagents.core.mailbox import ScheduleTriggerMessage

    bus = EventBus()
    llm = FakeSessionFactory([[text_response("handled")]])
    stage = ya.Stage(
        mailbox=MailBox(),
        eventbus=bus,
        runtime=Runtime(registry=ToolRegistry(), eventbus=bus),
        llm_session_factories={"fake": llm},
    )
    definition = AgentDefinition(
        name="main",
        llm=FAKE_LLM_CONFIG,
        prompt=PromptDefinition(system="test"),
    )
    actor = ya.ExampleActor(stage, [definition])

    await stage.mailbox.send(
        ScheduleTriggerMessage(
            agent_name="main",
            job_id="job123",
            content=yuullm.user("wake up"),
        )
    )
    agent = await actor.run_once()

    assert agent is not None
    assert agent.name == "main"
    assert agent.done
    messages, _tools = yuullm.split_history(agent.history)
    assert yuullm.render_message_text(messages[-1]) == "handled"


@pytest.mark.asyncio
async def test_actor_routes_background_completion_by_agent_name() -> None:
    from yuuagents.core.mailbox import BackgroundCompletedMessage

    bus = EventBus()
    llm = FakeSessionFactory([[text_response("handled bg")]])
    stage = ya.Stage(
        mailbox=MailBox(),
        eventbus=bus,
        runtime=Runtime(registry=ToolRegistry(), eventbus=bus),
        llm_session_factories={"fake": llm},
    )
    definition = AgentDefinition(
        name="main",
        llm=FAKE_LLM_CONFIG,
        prompt=PromptDefinition(system="test"),
    )
    actor = ya.ExampleActor(stage, [definition])

    await stage.mailbox.send(
        BackgroundCompletedMessage(
            task_id="task-1",
            agent_name="main",
            actor_id="actor-1",
            session_id="session-1",
            content=yuullm.user("background done"),
        )
    )
    agent = await actor.run_once()

    assert agent is not None
    assert agent.name == "main"
    messages, _tools = yuullm.split_history(agent.history)
    assert yuullm.render_message_text(messages[-2]) == "background done"
    assert yuullm.render_message_text(messages[-1]) == "handled bg"


@pytest.mark.asyncio
async def test_actor_expire_agent_closes_and_untracks_agent() -> None:
    bus = EventBus()
    events: list[ya.RuntimeEvent] = []
    bus.subscribe(lambda event: events.append(event))
    stage = ya.Stage(
        mailbox=MailBox(),
        eventbus=bus,
        runtime=Runtime(registry=ToolRegistry(), eventbus=bus),
        llm_session_factories={"fake": FakeSessionFactory([])},
    )
    actor = ya.ExampleActor(stage)
    definition = AgentDefinition(name="main", llm=FAKE_LLM_CONFIG)
    agent = actor.create_agent(definition)

    await actor.expire_agent(agent)

    assert agent.id not in actor.agents
    assert "main" not in actor.agents_by_name
    end_events = [event for event in events if event.name == "output.entity_end"]
    assert len(end_events) == 1
    assert end_events[0].data["entity_id"] == agent.id
    assert end_events[0].data["status"] == "expired"
