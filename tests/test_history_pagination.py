from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from support.api import JsonObject, base_url, boot_app, http_json, put_actor, running_server, ws_conversation_send
from support.api import SharedTestContext


@pytest.mark.asyncio
async def test_http_conversation_history_pagination_metadata(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor()
    conversation_id = test_context.conversation_id("history-page")
    for index in range(3):
        await ws_conversation_send(
            test_context.server,
            f"m{index}",
            actor_id,
            conversation_id,
            f"hello-{index}",
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
    assert all(item["seq"] >= tail["first_seq"] for item in tail_items)

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
        actor = await put_actor(server, "amy", workspace=tmp_path / "workspace")
        assert actor["id"] == "amy"
        assert "schema_version" not in actor
        assert "providers" not in actor
