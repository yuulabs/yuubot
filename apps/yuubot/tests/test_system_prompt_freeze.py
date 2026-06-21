"""Freeze test: the system prompt assembled for a conversation is frozen for
the lifetime of the runtime that owns the conversation agent.

This reuses :class:`tests.llm_prompt.framework.PromptCapture` as a runtime
read dependency WITHOUT modifying it. Two conversation turns are driven
through the daemon's admin conversation routes; the system message the LLM
receives on turn 2 must equal the system message from turn 1, even after
``AGENTS.md`` is mutated between turns.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import msgspec
import yuullm
from yuubot.bootstrap.config import DatabaseConfig, PathsConfig
from yuubot.resources.store.models import CapabilitySetORM
from yuubot.runtime.daemon import YuubotDaemon, build_daemon

from tests.helpers import (
    make_test_daemon_infrastructure,
    register_test_llm_provider,
)
from tests.llm_prompt.framework import PromptCapture


ACTOR_ID = "freeze-actor"
CONVERSATION_ID = "freeze-conversation"
SYSTEM_PROMPT_BODY = "You are a freeze verification agent."
WORKSPACE_RELATIVE = "freeze-ws"


async def test_system_prompt_freezes_within_runtime_lifetime(
    yuubot_config,
    tmp_path: Path,
) -> None:
    capture = PromptCapture()
    register_test_llm_provider("openai", capture)

    daemon = await _build_daemon(yuubot_config, tmp_path)
    await daemon.start()
    try:
        await _insert_actor_with_workspace(daemon, workspace_path=WORKSPACE_RELATIVE)
        await daemon.resources.event_bus.drain()

        workspace_dir = tmp_path / "data" / "workspace" / WORKSPACE_RELATIVE
        workspace_dir.mkdir(parents=True, exist_ok=True)
        agents_md = workspace_dir / "AGENTS.md"
        agents_md.write_text("__MARKER_AGENTS_V1__\n", encoding="utf-8")

        # Build the ASGI app ONCE. daemon.asgi_app() reconstructs the
        # ConversationManager (and its in-memory runtime cache) on each call,
        # so re-invoking it between turns would discard the cached agent and
        # re-render the system prompt — defeating the freeze under test.
        app = daemon.asgi_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-Daemon-Secret": yuubot_config.server.daemon_secret},
        ) as client:
            created = await client.post(
                "/api/admin/conversations",
                json={
                    "conversation_id": CONVERSATION_ID,
                    "actor_id": ACTOR_ID,
                },
            )
            assert created.status_code == 201, created.text

            await _send_message(client, CONVERSATION_ID, "first turn")
        await capture.wait_for_calls(1)
        system_1 = _captured_system_text(capture.calls[0])

        # Mutate disk + allow clock to advance between turns. The cached
        # agent built on turn 1 must keep the same system message.
        agents_md.write_text("__MARKER_AGENTS_V2__\n", encoding="utf-8")
        await asyncio.sleep(0.01)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-Daemon-Secret": yuubot_config.server.daemon_secret},
        ) as client:
            await _send_message(client, CONVERSATION_ID, "second turn")
        await capture.wait_for_calls(2)
        system_2 = _captured_system_text(capture.calls[1])
    finally:
        await daemon.stop()

    assert system_1 == system_2, (
        "system message changed between turns within the same runtime lifetime"
    )
    assert "__MARKER_AGENTS_V1__" in system_1, (
        "AGENTS.md v1 marker missing from frozen system message"
    )
    assert "__MARKER_AGENTS_V2__" not in system_1, (
        "AGENTS.md v2 marker leaked into the frozen system message"
    )


async def _build_daemon(
    base_config,
    tmp_path: Path,
) -> YuubotDaemon:
    return await build_daemon(
        msgspec.structs.replace(
            base_config,
            database=DatabaseConfig(path=":memory:"),
            paths=PathsConfig(data_dir=str(tmp_path / "data")),
        ),
        components=make_test_daemon_infrastructure(),
    )


async def _insert_actor_with_workspace(daemon: YuubotDaemon, *, workspace_path: str) -> None:
    repository = daemon.resources.repository

    # The shared helper inserts the echo actor with a default capability set
    # (no workspace). We then update the persisted capability set row to
    # carry a relative workspace name, which the daemon's ConversationManager
    # resolves under its own ``workspace_root`` derived from PathsConfig.
    from tests.helpers import insert_echo_actor_resources

    resources = await insert_echo_actor_resources(
        repository,
        actor_id=ACTOR_ID,
        system_prompt=SYSTEM_PROMPT_BODY,
    )
    await repository.update(
        CapabilitySetORM,
        resources.actor.capability_set.id,
        workspace_path=workspace_path,
    )


async def _send_message(
    client: httpx.AsyncClient,
    conversation_id: str,
    text: str,
) -> httpx.Response:
    response = await client.post(
        f"/api/admin/conversations/{conversation_id}/messages",
        json={"text": text},
    )
    assert response.status_code == 202, response.text
    return response


def _captured_system_text(captured_messages: list[yuullm.Message]) -> str:
    """Render the system message text captured by ``PromptCapture``."""
    for message in captured_messages:
        if message.role == "system":
            return yuullm.render_message_text(message)
    raise AssertionError(
        "no system message captured in the LLM call history; "
        f"roles seen: {[m.role for m in captured_messages]}"
    )
