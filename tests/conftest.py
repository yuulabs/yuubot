"""Shared fixtures for end-to-end flow tests."""

import asyncio
import os
from pathlib import Path
import socket

import msgspec
import pytest
import yaml

# Set up a no-op TracerProvider so yuutrace.require_initialized() passes
# but traces do NOT go to the production ~/.yagents/traces.db.
from opentelemetry import trace as _otel_trace
from opentelemetry.sdk.trace import TracerProvider as _TracerProvider
_otel_trace.set_tracer_provider(_TracerProvider())

from yuubot.commands.builtin import build_command_tree
from yuubot.commands.entry import EntryManager
from yuubot.commands.roles import RoleManager
from yuubot.config import Config, BotConfig, DaemonConfig, DatabaseConfig, ResponseConfig, SessionConfig
from yuubot.core.db import init_db, close_db
from yuubot.daemon.agent_runner import AgentRunner
from yuubot.daemon.dispatcher import Dispatcher
from yuubot.daemon.llm import LLMExecutor
from yuubot.daemon.conversation import ConversationManager

# ── Constants ────────────────────────────────────────────────────

MASTER_QQ = 10001
BOT_QQ = 99999
GROUP_ID = 1000
FOLK_QQ = 20001
MOD_QQ = 20002


# ── Test character registration ──────────────────────────────────

@pytest.fixture(autouse=True)
def test_characters():
    """Register test Characters into CHARACTER_REGISTRY, restore on teardown."""
    from yuubot.characters import CHARACTER_REGISTRY, register
    from yuubot.prompt import AgentSpec, Character

    original = dict(CHARACTER_REGISTRY)

    # Register test main agent
    register(Character(
        name="main",
        description="Test main agent",
        min_role="folk",
        persona="你是测试机器人。",
        spec=AgentSpec(
            tools=["execute_skill_cli"],
            skills=["*"],
            max_steps=4,
        ),
        provider="test",
        model="test-model",
    ))

    # Register test general agent
    register(Character(
        name="general",
        description="General agent (master only)",
        min_role="master",
        persona="通用助手。",
        spec=AgentSpec(
            tools=["execute_bash"],
            skills=["*"],
            max_steps=4,
        ),
        provider="test",
        model="test-model",
    ))

    yield

    # Restore original registry
    CHARACTER_REGISTRY.clear()
    CHARACTER_REGISTRY.update(original)


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
async def db(tmp_path):
    """Initialize a tmp SQLite for yuubot ORM models."""
    from yuubot.core.models import GroupSetting
    db_path = str(tmp_path / "yuubot.db")
    await init_db(db_path)
    # Enable bot for test group by default (at-mode)
    await GroupSetting.create(group_id=GROUP_ID, bot_enabled=True, response_mode="at")
    yield db_path
    await close_db()


@pytest.fixture
def recorder_api_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def yuubot_config(tmp_path, recorder_api_port) -> Config:
    """Build a programmatic Config with test values."""
    return Config(
        bot=BotConfig(qq=BOT_QQ, master=MASTER_QQ, entries=["/y", "/yuu"]),
        daemon=DaemonConfig(recorder_api=f"http://127.0.0.1:{recorder_api_port}"),
        database=DatabaseConfig(path=str(tmp_path / "yuubot.db")),
        response=ResponseConfig(group_default="at", dm_whitelist=[]),
        session=SessionConfig(ttl=300, max_tokens=60000),
        yuuagents={
            "providers": {
                "test": {
                    "api_type": "openai-chat-completion",
                    "api_key_env": "",
                    "default_model": "test-model",
                    "base_url": "https://api.openai.com/v1",
                },
            },
            "agents": {
                "main": {
                    "description": "Test main agent",
                    "provider": "test",
                    "model": "test-model",
                },
                "general": {
                    "description": "General agent (master only)",
                    "provider": "test",
                    "model": "test-model",
                },
            },
            "daemon": {"socket": str(tmp_path / "yagents.sock")},
            "db": {"url": f"sqlite+aiosqlite:///{tmp_path / 'yagents.db'}"},
            "skills": {"paths": [str(tmp_path / "skills")]},
            "docker": {"image": "yuuagents-runtime:latest"},
        },
    )


@pytest.fixture
def config_path(tmp_path, yuubot_config) -> str:
    """Write config.yaml for subprocess-based skill execution."""
    raw = msgspec.to_builtins(yuubot_config)
    path = Path(tmp_path) / "config.yaml"
    path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return str(path)


@pytest.fixture(autouse=True)
def recorder_api_env(yuubot_config, config_path, monkeypatch):
    monkeypatch.setenv("YUUBOT_TEST_RECORDER_API", yuubot_config.daemon.recorder_api)
    monkeypatch.setenv("YUUBOT_CONFIG", config_path)


@pytest.fixture
def session_mgr() -> ConversationManager:
    return ConversationManager(ttl=300, max_tokens=60000)


async def _lightweight_init(runner: AgentRunner) -> None:
    """Initialize yuuagents without starting daemon or touching ~/.yagents."""
    if runner._initialized:
        return
    import json
    import msgspec
    from yuuagents.config import Config as YuuagentsConfig
    from yuuagents.persistence import TaskPersistence
    from yuubot import config as yuubot_config

    base_data = json.loads(msgspec.json.encode(YuuagentsConfig()))
    merged_data = yuubot_config._deep_merge(base_data, runner.config.yuuagents)
    cfg = msgspec.convert(merged_data, YuuagentsConfig)

    # Only init DB (in-memory), skip dirs/config-write/docker/daemon
    persistence = TaskPersistence(db_url=cfg.db_url)
    await persistence.start()
    await persistence.stop()

    runner._initialized = True


@pytest.fixture
async def dispatcher(db, yuubot_config, session_mgr) -> Dispatcher:
    """Build a real Dispatcher with real command tree, roles, agent runner."""
    role_mgr = RoleManager(master_qq=yuubot_config.bot.master)
    entry_mgr = EntryManager()
    agent_runner = AgentRunner(yuubot_config)
    session_mgr_for_llm = session_mgr  # same instance
    llm_exec = LLMExecutor(
        conv_mgr=session_mgr_for_llm,
        agent_runner=agent_runner,
        config=yuubot_config,
        role_mgr=role_mgr,
    )
    root = build_command_tree(yuubot_config.bot.entries, llm_executor=llm_exec)

    deps = {
        "root": root,
        "role_mgr": role_mgr,
        "entry_mgr": entry_mgr,
        "config": yuubot_config,
        "session_mgr": session_mgr,
        "dm_whitelist": yuubot_config.response.dm_whitelist,
    }

    # Lightweight init: only DB, no daemon/docker/filesystem side effects
    await _lightweight_init(agent_runner)

    disp = Dispatcher(
        config=yuubot_config,
        root=root,
        role_mgr=role_mgr,
        deps=deps,
        agent_runner=agent_runner,
        conv_mgr=session_mgr,
    )
    yield disp
    await disp.stop()
    await agent_runner.stop()


# ── Event builders ───────────────────────────────────────────────


def make_group_event(
    text: str,
    user_id: int = FOLK_QQ,
    group_id: int = GROUP_ID,
    nickname: str = "测试用户",
    *,
    at_bot: bool = True,
    ctx_id: int = 1,
) -> dict:
    """Build a OneBot V11 group message event dict."""
    message = []
    if at_bot:
        message.append({"type": "at", "data": {"qq": str(BOT_QQ)}})
        message.append({"type": "text", "data": {"text": " " + text}})
    else:
        message.append({"type": "text", "data": {"text": text}})

    return {
        "post_type": "message",
        "message_type": "group",
        "message_id": 12345,
        "user_id": user_id,
        "group_id": group_id,
        "message": message,
        "raw_message": text,
        "time": 1700000000,
        "self_id": BOT_QQ,
        "sender": {"nickname": nickname, "card": ""},
        "ctx_id": ctx_id,
    }


def make_private_event(
    text: str,
    user_id: int = MASTER_QQ,
    nickname: str = "Master",
    *,
    ctx_id: int = 2,
) -> dict:
    """Build a OneBot V11 private message event dict."""
    return {
        "post_type": "message",
        "message_type": "private",
        "message_id": 12346,
        "user_id": user_id,
        "message": [{"type": "text", "data": {"text": text}}],
        "raw_message": text,
        "time": 1700000000,
        "self_id": BOT_QQ,
        "sender": {"nickname": nickname},
        "ctx_id": ctx_id,
    }


async def send(dispatcher: Dispatcher, event: dict, wait: float = 0.2) -> None:
    """Dispatch an event and wait for the worker to process it."""
    await dispatcher.dispatch(event)
    # Give the async worker queue time to process
    await asyncio.sleep(wait)
