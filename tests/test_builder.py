"""Tests for the agent turn builder pipeline."""

from __future__ import annotations

import attrs

from yuubot.core.onebot import to_inbound_message
from yuubot.core.types import InboundMessage
from yuubot.daemon.bot_info import BotInfo
from yuubot.daemon.builder import AgentRunBuilder
from yuubot.prompt import CapVisibility


def _make_inbound(text: str, *, ctx_id: int = 1) -> InboundMessage:
    return to_inbound_message(
        {
            "post_type": "message",
            "message_type": "group",
            "message_id": 1,
            "user_id": 100,
            "group_id": 1000,
            "message": [{"type": "text", "data": {"text": text}}],
            "raw_message": text,
            "time": 1700000000,
            "self_id": 99999,
            "sender": {"nickname": "tester", "card": ""},
            "ctx_id": ctx_id,
        }
    )


def _make_builder(config) -> AgentRunBuilder:
    bot_info = BotInfo(config)
    bot_info._bot_name = "Bot"
    bot_info._group_names[1000] = "测试群"
    return AgentRunBuilder(
        config=config,
        bot_info=bot_info,
        build_prompt=lambda agent_name: (None, None),
        build_tool_manager=lambda tool_names: tool_names,
        build_subprocess_env=lambda **kwargs: {key: str(value) for key, value in kwargs.items()},
        build_capability_context=lambda **kwargs: kwargs,
        resolve_docker=_resolve_docker,
        docker_home_info=_docker_home_info,
        needs_docker=lambda tool_names: "execute_bash" in tool_names,
        has_vision=lambda agent_name: False,
        docker=None,
    )


async def _resolve_docker(task_id: str) -> tuple[str, str]:
    return "/work", f"container-{task_id[:8]}"


async def _docker_home_info(container_id: str) -> tuple[str, str, str]:
    return "/mnt/host", "/home/tester", "/root"


async def test_build_task_bundle_merges_pending_messages(yuubot_config, monkeypatch):
    """Pending messages stay structured until render_task."""
    builder = _make_builder(yuubot_config)
    primary = _make_inbound("/yllm first")
    pending = _make_inbound("second")
    captured: dict[str, object] = {}

    async def fake_render_memory_hints(text: str, ctx_id: int | None = None) -> str:
        captured["memory_text"] = text
        return ""

    async def fake_render_task(msg, policy, context, **kwargs) -> str:
        captured["extra_events"] = msg.raw_event.get("_extra_events", [])
        captured["bot_name"] = context.bot_name
        return "rendered task"

    monkeypatch.setattr("yuubot.daemon.builder.render_memory_hints", fake_render_memory_hints)
    monkeypatch.setattr("yuubot.daemon.builder.render_task", fake_render_task)

    turn = await builder.build_turn_context(
        event=primary.raw_event,
        agent_name="main",
        text_override="",
        is_continuation=False,
        pending_messages=[pending],
        task_id="task-1",
    )
    bundle = await builder.build_task_bundle(turn)

    extra_events = captured["extra_events"]
    memory_text = captured["memory_text"]
    assert isinstance(extra_events, list)
    assert isinstance(memory_text, str)
    assert len(extra_events) == 1
    assert captured["bot_name"] == "Bot"
    assert "first" in memory_text
    assert "second" in memory_text
    assert bundle.user_items == [bundle.task_text]


async def test_build_task_bundle_prepends_handoff_message(yuubot_config, monkeypatch):
    """Rollover handoff text becomes the first rendered message of the next turn."""
    builder = _make_builder(yuubot_config)
    primary = _make_inbound("最新补充")
    captured: dict[str, object] = {}

    async def fake_render_memory_hints(text: str, ctx_id: int | None = None) -> str:
        captured["memory_text"] = text
        return ""

    async def fake_render_task(msg, policy, context, **kwargs) -> str:
        captured["first_text"] = msg.raw_event["message"][0]["data"]["text"]
        captured["extra_events"] = msg.raw_event.get("_extra_events", [])
        return "rendered task"

    monkeypatch.setattr("yuubot.daemon.builder.render_memory_hints", fake_render_memory_hints)
    monkeypatch.setattr("yuubot.daemon.builder.render_task", fake_render_task)

    turn = await builder.build_turn_context(
        event=primary.raw_event,
        agent_name="main",
        task_id="task-handoff",
        handoff_text="这是上一轮的压缩摘要",
    )
    await builder.build_task_bundle(turn)

    extra_events = captured["extra_events"]
    assert captured["first_text"] == "这是上一轮的压缩摘要"
    assert isinstance(extra_events, list)
    assert len(extra_events) == 1
    assert extra_events[0]["raw_message"] == "最新补充"
    assert "这是上一轮的压缩摘要" in str(captured["memory_text"])
    assert "最新补充" in str(captured["memory_text"])


async def test_build_run_context_collects_runtime_values(yuubot_config):
    """RunContext is built from the turn, not reconstructed from shared globals."""
    prompt_spec = attrs.make_class(
        "PromptSpec",
        {
            "tools": attrs.field(default=["execute_bash"]),
            "resolved_sections": attrs.field(default=[("persona", "测试人格")]),
            "agent_spec": attrs.field(
                default=attrs.make_class(
                    "AgentSpec",
                    {
                        "caps": attrs.field(default=[]),
                        "cap_actions": attrs.field(default={}),
                        "max_steps": attrs.field(default=4),
                        "soft_timeout": attrs.field(default=30),
                        "silence_timeout": attrs.field(default=30),
                    },
                )()
            ),
        },
    )()
    builder = _make_builder(yuubot_config)
    builder.build_prompt = lambda agent_name: (prompt_spec, object())

    turn = await builder.build_turn_context(
        event=_make_inbound("hello").raw_event,
        agent_name="main",
        user_role="MASTER",
        task_id="task-2",
    )
    run_ctx = await builder.build_run_context(
        turn=turn,
        task_id="task-2",
        runtime_id="runtime-1",
    )

    assert run_ctx.runtime_id == "runtime-1"
    assert run_ctx.persona == "测试人格"
    assert run_ctx.docker_binding.workdir == "/work"
    assert run_ctx.subprocess_env.values["ctx_id"] == "1"
    assert run_ctx.subprocess_env.values["user_id"] == "100"
    assert run_ctx.subprocess_env.values["docker_mount"] == "/mnt/host"


async def test_build_run_context_includes_action_filters(yuubot_config):
    """Capability action filters are passed into runtime context."""
    prompt_spec = attrs.make_class(
        "PromptSpec",
        {
            "tools": attrs.field(default=[]),
            "resolved_sections": attrs.field(default=[("persona", "测试人格")]),
            "agent_spec": attrs.field(
                default=attrs.make_class(
                    "AgentSpec",
                    {
                        "caps": attrs.field(default=["*"]),
                        "cap_actions": attrs.field(default={}),
                        "cap_visibility": attrs.field(
                            default={"mem": CapVisibility(mode="include", actions=("save", "recall"))}
                        ),
                        "max_steps": attrs.field(default=4),
                        "soft_timeout": attrs.field(default=30),
                        "silence_timeout": attrs.field(default=30),
                    },
                )()
            ),
        },
    )()
    builder = _make_builder(yuubot_config)
    builder.build_prompt = lambda agent_name: (prompt_spec, object())

    turn = await builder.build_turn_context(
        event=_make_inbound("hello").raw_event,
        agent_name="main",
        task_id="task-3",
    )
    run_ctx = await builder.build_run_context(
        turn=turn,
        task_id="task-3",
        runtime_id="runtime-3",
    )

    assert run_ctx.addon_context["action_filters"] == {
        "mem": CapVisibility(mode="include", actions=("save", "recall"))
    }
