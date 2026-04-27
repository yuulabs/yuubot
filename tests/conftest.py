"""Shared fixtures for end-to-end flow tests."""

# ruff: noqa: E402

import asyncio
from pathlib import Path
import sqlite3
import socket
from urllib.parse import quote
from unittest.mock import patch

import msgspec
import pytest
import yaml
import yuullm

from opentelemetry import trace as _otel_trace
from opentelemetry.sdk.trace import TracerProvider as _TracerProvider

_otel_trace.set_tracer_provider(_TracerProvider())

from yuubot.config import (
    Config,
    BotConfig,
    DaemonConfig,
    DaemonApiConfig,
    DatabaseConfig,
    ResponseConfig,
    SessionConfig,
)
from yuubot.core.db import init_db, close_db
from yuubot.daemon.dispatcher import Dispatcher
from yuubot.daemon.conversation import ConversationManager
from yuubot.daemon.agent_runner import AgentRunner
from yuubot.daemon.llm import LLMExecutor
from yuubot.commands.builtin import build_command_tree
from yuubot.commands.entry import EntryManager

# ── Constants ────────────────────────────────────────────────────

MASTER_QQ = 10001
BOT_QQ = 99999
GROUP_ID = 1000
FOLK_QQ = 20001
MOD_QQ = 20002

PROVIDER_MODEL_LISTS: dict[str, list[str]] = {
    "test": ["test-model", "test-model-v2"],
    "aihubmix": [
        "anthropic/claude-sonnet-4.1",
        "google/gemini-3.1-flash-lite-preview",
        "deepseek/deepseek-chat",
    ],
    "openrouter": [
        "anthropic/claude-sonnet-4.1",
        "anthropic/claude-sonnet-4.0-preview",
        "openai/gpt-4.1",
        "google/gemini-3.1-flash-lite-preview",
        "deepseek/deepseek-chat",
    ],
    "deepseek": ["deepseek-chat"],
}


@pytest.fixture(autouse=True)
def mock_provider_model_lists():
    async def _fake_list_models(self):
        return [
            yuullm.ProviderModel(id=model_id)
            for model_id in PROVIDER_MODEL_LISTS.get(self.provider, [])
        ]

    with (
        patch.object(
            yuullm.providers.OpenAIChatCompletionProvider, "list_models", _fake_list_models,
        ),
        patch.object(
            yuullm.providers.AnthropicMessagesProvider, "list_models", _fake_list_models,
        ),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_selector_candidate_choice(monkeypatch):
    from yuubot.model_resolution import ModelResolver, _score_candidate

    async def _fake_choose_candidate(
        self, provider: str, selector: str, candidates: list[str],
    ) -> str:
        del self, provider
        return sorted(
            candidates, key=lambda item: _score_candidate(selector, item), reverse=True,
        )[0]

    monkeypatch.setattr(ModelResolver, "_choose_candidate", _fake_choose_candidate)


# ── Port fixtures ───────────────────────────────────────────────

@pytest.fixture
def recorder_api_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def daemon_api_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ── Database ─────────────────────────────────────────────────────

@pytest.fixture
async def db(tmp_path):
    from yuubot.core.models import GroupSetting

    db_path = str(tmp_path / "yuubot.db")
    await init_db(db_path)
    await GroupSetting.create(group_id=GROUP_ID, bot_enabled=True)
    yield db_path
    await close_db()


@pytest.fixture
def traces_db(tmp_path):
    uri = f"file:{quote(str(tmp_path / 'traces.db'))}?mode=memory&cache=shared"
    conn = sqlite3.connect(uri, uri=True)
    conn.executescript(
        """
        CREATE TABLE spans (
            span_id TEXT PRIMARY KEY,
            parent_span_id TEXT,
            start_time_unix_nano INTEGER,
            agent TEXT,
            trace_id TEXT
        );
        CREATE TABLE events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            span_id TEXT,
            name TEXT,
            attributes_json TEXT
        );
        """
    )
    try:
        yield conn, uri
    finally:
        conn.close()


# ── Config ───────────────────────────────────────────────────────

@pytest.fixture
def yuubot_config(tmp_path, recorder_api_port, daemon_api_port, traces_db) -> Config:
    _, traces_db_uri = traces_db
    return Config(
        bot=BotConfig(qq=BOT_QQ, master=MASTER_QQ, entries=["/y", "/yuu"]),
        daemon=DaemonConfig(
            recorder_api=f"http://127.0.0.1:{recorder_api_port}",
            api=DaemonApiConfig(host="127.0.0.1", port=daemon_api_port),
        ),
        database=DatabaseConfig(path=str(tmp_path / "yuubot.db")),
        response=ResponseConfig(group_default="at", dm_whitelist=[]),
        session=SessionConfig(ttl=300, max_tokens=60000),
        agent_llm_refs={
            "yuu": "test/test-model",
            "maid": "test/test-model",
            "general": "test/test-model",
            "mem_curator": "test/test-model",
        },
        provider_priorities={"aihubmix": 120, "openrouter": 80, "deepseek": 110, "test": 60},
        provider_affinity={"deepseek-*": {"deepseek": 100}},
        capabilities={
            "defaults": {"vision": True, "tool_use": True, "reasoning": True, "ctx": 128000},
            "patterns": {"deepseek-*": "-vision", "mistral-*": "-vision"},
        },
        llm_roles={
            "vision": "gemini-3.1-flash-lite-preview",
            "selector": "deepseek-chat",
            "summarizer": "deepseek-chat",
        },
        yuuagents={
            "provider_aliases": {"or": "openrouter", "ahm": "aihubmix"},
            "providers": {
                "test": {
                    "api_type": "openai-chat-completion",
                    "api_key_env": "YUUBOT_TEST_LLM_KEY",
                    "default_model": "test-model",
                    "base_url": "https://api.openai.com/v1",
                },
                "aihubmix": {
                    "api_type": "openai-chat-completion",
                    "api_key_env": "YUUBOT_TEST_LLM_KEY",
                    "default_model": "anthropic/claude-sonnet-4.1",
                    "base_url": "https://api.aihubmix.com/v1",
                },
                "openrouter": {
                    "api_type": "openai-chat-completion",
                    "api_key_env": "YUUBOT_TEST_LLM_KEY",
                    "default_model": "anthropic/claude-sonnet-4.1",
                    "base_url": "https://openrouter.ai/api/v1",
                },
                "deepseek": {
                    "api_type": "openai-chat-completion",
                    "api_key_env": "YUUBOT_TEST_LLM_KEY",
                    "default_model": "deepseek-chat",
                    "base_url": "https://api.deepseek.com/v1",
                },
            },
            "yuutrace": {"db_path": traces_db_uri},
        },
    )


@pytest.fixture
def config_path(tmp_path, yuubot_config) -> str:
    raw = msgspec.to_builtins(yuubot_config)
    path = Path(tmp_path) / "config.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return str(path)


@pytest.fixture(autouse=True)
def recorder_api_env(yuubot_config, config_path, monkeypatch):
    monkeypatch.setenv("YUUBOT_TEST_RECORDER_API", yuubot_config.daemon.recorder_api)
    monkeypatch.setenv("YUUBOT_CONFIG", config_path)
    monkeypatch.setenv("YUUBOT_TEST_LLM_KEY", "sk-test-dummy-key-not-real")


# ── Test daemon server ───────────────────────────────────────────

@pytest.fixture
def test_daemon(yuubot_config):
    """Real FastAPI server for /agent-fns endpoints on daemon_api_port."""
    from tests.framework import TestDaemonServer

    server = TestDaemonServer(yuubot_config)
    port = server.start(host="127.0.0.1", port=yuubot_config.daemon.api.port)
    assert port == yuubot_config.daemon.api.port
    yield server
    server.stop()


# ── Convenience ──────────────────────────────────────────────────

@pytest.fixture
def session_mgr() -> ConversationManager:
    return ConversationManager(ttl=300, max_tokens=60000)


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
    await dispatcher.dispatch(event)
    await asyncio.sleep(wait)
