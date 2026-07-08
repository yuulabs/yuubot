from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from yuubot import Yuubot
from yuubot.domain.messages import ContentItem, HistoryToolSpecs, InputMessage, SystemPrompt

from support.api import JsonObject, base_url, boot_app, http_json, put_actor, put_provider, running_server, ws_conversation_send
from support.api import SharedTestContext


async def _seed_history(app: Yuubot, conversation_id: str, count: int) -> list[int]:
    await app.runtime.history.extend(
        conversation_id,
        [HistoryToolSpecs(specs=[]), SystemPrompt(text="system")],
    )
    items = [
        InputMessage(role="user", name="user", content=[ContentItem(kind="text", text=f"msg-{index}")])
        for index in range(count)
    ]
    wrapped = await app.runtime.history.extend(conversation_id, items)
    return [int(item["seq"]) for item in wrapped]


@pytest.mark.asyncio
async def test_history_store_tail_limit_excludes_prefix_and_reports_has_more(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "data")
    try:
        conversation_id = "tail-page"
        seqs = await _seed_history(app, conversation_id, 5)
        items, has_more = await app.runtime.history.load_interaction_wrapped(conversation_id, limit=3)
        assert [int(item["seq"]) for item in items] == seqs[-3:]
        assert has_more is True
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_history_store_forward_after_seq_pagination(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "data")
    try:
        conversation_id = "forward-page"
        seqs = await _seed_history(app, conversation_id, 4)
        first_page, has_more = await app.runtime.history.load_interaction_wrapped(conversation_id, after_seq=seqs[0], limit=2)
        assert [int(item["seq"]) for item in first_page] == seqs[1:3]
        assert has_more is True
        second_page, has_more_again = await app.runtime.history.load_interaction_wrapped(
            conversation_id,
            after_seq=int(first_page[-1]["seq"]),
            limit=2,
        )
        assert [int(item["seq"]) for item in second_page] == seqs[3:]
        assert has_more_again is False
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_history_store_full_load_without_params(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "data")
    try:
        conversation_id = "full-load"
        seqs = await _seed_history(app, conversation_id, 3)
        items, has_more = await app.runtime.history.load_interaction_wrapped(conversation_id)
        assert [int(item["seq"]) for item in items] == seqs
        assert has_more is False
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_http_conversation_history_pagination_metadata(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor()
    conversation_id = test_context.conversation_id("history-page")
    for index in range(3):
        await ws_conversation_send(
            test_context.server,
            command_id=f"m{index}",
            actor_id=actor_id,
            conversation_id=conversation_id,
            content=f"hello-{index}",
        )

    url = base_url(test_context.server)
    full = await http_json("GET", f"{url}/api/conversations/{conversation_id}/history")
    full_items = cast(list[JsonObject], full["items"])
    assert full["has_more"] is False
    assert len(full_items) >= 3

    tail = await http_json("GET", f"{url}/api/conversations/{conversation_id}/history?limit=2")
    tail_items = cast(list[JsonObject], tail["items"])
    assert len(tail_items) == 2
    assert tail["has_more"] is True
    assert tail["first_seq"] == tail_items[0]["seq"]
    assert tail["last_seq"] == tail_items[-1]["seq"]
    assert tail_items == full_items[-2:]

    forward = await http_json(
        "GET",
        f"{url}/api/conversations/{conversation_id}/history?after_seq={tail['last_seq']}",
    )
    forward_items = cast(list[JsonObject], forward["items"])
    assert forward_items == [item for item in full_items if item["seq"] > tail["last_seq"]]

    await http_json(
        "GET",
        f"{url}/api/conversations/{conversation_id}/history?after_seq=-1",
        expected_status=400,
    )
    await http_json(
        "GET",
        f"{url}/api/conversations/{conversation_id}/history?limit=0",
        expected_status=400,
    )


@pytest.mark.asyncio
async def test_http_actor_put_returns_actor_snapshot_not_bootstrap(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "data")
    async with running_server(app) as server:
        await put_provider(server, "fake")
        actor = await put_actor(server, "amy", workspace=tmp_path / "workspace")
        assert actor["id"] == "amy"
        assert "schema_version" not in actor
        assert "providers" not in actor
