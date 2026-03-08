"""Flow: agent permission checks — config validation & runtime rejection."""

import pytest

from yuubot.config import Config, BotConfig, DaemonConfig, DatabaseConfig, ResponseConfig, SessionConfig
from tests.conftest import MASTER_QQ, FOLK_QQ, make_group_event, send
from tests.mocks import mock_recorder_api, mock_llm


# ── Helpers ──────────────────────────────────────────────────────

def _make_config(tmp_path, agents: dict) -> Config:
    return Config(
        bot=BotConfig(qq=99999, master=MASTER_QQ, entries=["/y", "/yuu"]),
        daemon=DaemonConfig(recorder_api="http://127.0.0.1:8767"),
        database=DatabaseConfig(path=str(tmp_path / "yuubot.db")),
        response=ResponseConfig(group_default="at", dm_whitelist=[]),
        session=SessionConfig(ttl=300, max_tokens=60000),
        yuuagents={
            "providers": {"test": {
                "api_type": "openai-chat-completion",
                "api_key_env": "",
                "default_model": "test-model",
                "base_url": "https://api.openai.com/v1",
            }},
            "agents": agents,
            "daemon": {"socket": str(tmp_path / "yagents.sock")},
            "db": {"url": f"sqlite+aiosqlite:///{tmp_path / 'yagents.db'}"},
            "skills": {"paths": [str(tmp_path / "skills")]},
            "docker": {"image": "yuuagents-runtime:latest"},
        },
    )


# ── Config validation: privilege escalation ──────────────────────


def test_folk_parent_with_master_child_rejected(tmp_path):
    """A folk parent delegating to a master child must fail at load time."""
    cfg = _make_config(tmp_path, {
        "parent": {
            "description": "folk parent",
            "min_role": "folk",
            "provider": "test",
            "model": "test-model",
            "tools": ["execute_skill_cli"],
            "subagents": ["dangerous_child"],
        },
        "dangerous_child": {
            "description": "master child",
            "min_role": "master",
            "provider": "test",
            "model": "test-model",
            "tools": ["execute_bash"],
        },
    })
    with pytest.raises(ValueError, match="Privilege escalation"):
        cfg.validate_agent_permissions()


def test_master_parent_with_folk_child_ok(tmp_path):
    """A master parent delegating to a folk child is fine."""
    cfg = _make_config(tmp_path, {
        "parent": {
            "description": "master parent",
            "min_role": "master",
            "provider": "test",
            "model": "test-model",
            "tools": ["execute_bash"],
            "subagents": ["safe_child"],
        },
        "safe_child": {
            "description": "folk child",
            "min_role": "folk",
            "provider": "test",
            "model": "test-model",
            "tools": ["execute_skill_cli"],
        },
    })
    cfg.validate_agent_permissions()  # should not raise


def test_same_level_parent_child_ok(tmp_path):
    """Parent and child at same level is fine."""
    cfg = _make_config(tmp_path, {
        "parent": {
            "description": "master parent",
            "min_role": "master",
            "provider": "test",
            "model": "test-model",
            "subagents": ["child"],
        },
        "child": {
            "description": "master child",
            "min_role": "master",
            "provider": "test",
            "model": "test-model",
        },
    })
    cfg.validate_agent_permissions()  # should not raise


# ── Runtime: folk user rejected from master agent ────────────────


async def test_folk_rejected_from_master_agent(dispatcher):
    """Folk user using #general (master-only) gets permission denied."""
    with mock_recorder_api() as sent, mock_llm():
        event = make_group_event("/yllm #general hello", user_id=FOLK_QQ)
        await send(dispatcher, event, wait=0.5)

    assert len(sent) >= 1
    assert any("权限" in s["message"][0]["data"]["text"] for s in sent)


async def test_master_can_use_master_agent(dispatcher, session_mgr):
    """Master user can use #general (master-only) successfully."""
    with mock_recorder_api() as sent, mock_llm():
        event = make_group_event("/yllm #general hello", user_id=MASTER_QQ)
        await send(dispatcher, event, wait=1.0)

    session = session_mgr.get(1)
    assert session is not None
    assert session.agent_name == "general"
