"""Tests for hand-written GitHub facade bridge error handling."""

from __future__ import annotations

import pytest

from yuubot.core.facade.protocol import FacadeRpcResponse


async def test_github_facade_client_surfaces_bridge_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yext.github

    async def fail_request(request):
        _ = request
        raise RuntimeError("ValueError: owner and repo are required")

    monkeypatch.setattr(yext.github, "_request", fail_request)
    monkeypatch.setattr(yext.github._context, "actor_context", lambda: _ActorContext())
    monkeypatch.setattr(yext.github._context, "bridge_context", lambda: _BridgeContext())

    with pytest.raises(RuntimeError, match="owner and repo are required"):
        await yext.github.repo().issues.list_recent()


async def test_github_facade_rejects_non_object_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yext.github

    async def bad_request(request):
        _ = request
        return FacadeRpcResponse(ok=True, result={"issues": {}})

    monkeypatch.setattr(yext.github, "_request", bad_request)
    monkeypatch.setattr(yext.github._context, "actor_context", lambda: _ActorContext())
    monkeypatch.setattr(yext.github._context, "bridge_context", lambda: _BridgeContext())

    with pytest.raises(TypeError, match="issues list"):
        await yext.github.repo("yuulabs", "yuubot").issues.list_recent()


class _ActorContext:
    actor_id = "actor-1"
    agent_name = "agent-1"
    session_id = "session-1"
    mailbox_id = "actor:actor-1"


class _BridgeContext:
    host = "127.0.0.1"
    port = 1
    token = "token"
    timeout_s = 5.0
