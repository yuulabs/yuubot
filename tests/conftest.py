"""Shared fixtures for end-to-end flow tests."""

import asyncio

import pytest

from yuubot.commands.builtin import build_command_tree
from yuubot.commands.entry import EntryManager
from yuubot.commands.roles import RoleManager
from yuubot.config import Config, BotConfig, DaemonConfig, DatabaseConfig, ResponseConfig, SessionConfig
from yuubot.core.db import init_db, close_db
from yuubot.daemon.agent_runner import AgentRunner
from yuubot.daemon.dispatcher import Dispatcher
from yuubot.daemon.session import SessionManager

# ── Constants ────────────────────────────────────────────────────

MASTER_QQ = 10001
BOT_QQ = 99999
GROUP_ID = 1000
FOLK_QQ = 20001
MOD_QQ = 20002


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
def yuubot_config(tmp_path) -> Config:
    """Build a programmatic Config with test values."""
    return Config(
        bot=BotConfig(qq=BOT_QQ, master=MASTER_QQ, entries=["/y", "/yuu"]),
        daemon=DaemonConfig(recorder_api="http://127.0.0.1:8767"),
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
                    "max_steps": 4,
                    "persona": "你是测试机器人。",
                    "tools": ["execute_skill_cli"],
                    "skills": ["*"],
                    "expand_skills": [],
                },
                "general": {
                    "description": "General agent (master only)",
                    "min_role": "master",
                    "provider": "test",
                    "model": "test-model",
                    "max_steps": 4,
                    "persona": "通用助手。",
                    "tools": ["execute_bash"],
                    "skills": ["*"],
                },
            },
            "daemon": {"socket": str(tmp_path / "yagents.sock")},
            "db": {"url": f"sqlite+aiosqlite:///{tmp_path / 'yagents.db'}"},
            "skills": {"paths": [str(tmp_path / "skills")]},
            "docker": {"image": "yuuagents-runtime:latest"},
        },
    )


@pytest.fixture
def session_mgr() -> SessionManager:
    return SessionManager(ttl=300, max_tokens=60000)


@pytest.fixture
def dispatcher(db, yuubot_config, session_mgr) -> Dispatcher:
    """Build a real Dispatcher with real command tree, roles, agent runner."""
    root = build_command_tree(yuubot_config.bot.entries)
    role_mgr = RoleManager(master_qq=yuubot_config.bot.master)
    entry_mgr = EntryManager()
    agent_runner = AgentRunner(yuubot_config)

    deps = {
        "root": root,
        "role_mgr": role_mgr,
        "entry_mgr": entry_mgr,
        "config": yuubot_config,
        "session_mgr": session_mgr,
        "dm_whitelist": yuubot_config.response.dm_whitelist,
    }

    return Dispatcher(
        config=yuubot_config,
        root=root,
        role_mgr=role_mgr,
        deps=deps,
        agent_runner=agent_runner,
        session_mgr=session_mgr,
    )


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
