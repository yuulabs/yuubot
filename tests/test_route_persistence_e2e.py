from __future__ import annotations

from pathlib import Path
from typing import cast

from support.api import (
    JsonObject,
    SharedTestContext,
    base_url,
    boot_app,
    create_route,
    enable_actor,
    http_json,
    post_inbound,
    put_actor,
    put_provider,
    running_server,
    wait_for_history_kind,
)
from support.llm_rules import reply_text, user_message_contains
from support.prompt_conditioned_llm import PromptConditionedProvider


async def test_http_gateway_routes_persist_across_restart(tmp_path: Path) -> None:
    llm = PromptConditionedProvider([(user_message_contains("hello"), reply_text("hi"))])
    app = await boot_app(tmp_path / "data", llm)
    async with running_server(app) as server:
        await put_provider(server)
        await put_actor(server, "amy", workspace=tmp_path / "workspace")
        await enable_actor(server, "amy")
        await create_route(server, "mailbox", "mailbox", "amy")
        inbound = await post_inbound(server, "mailbox", "hello", "route-persist-c1")
        assert inbound["delivered"] is True
        await wait_for_history_kind(server, "route-persist-c1", "gen_text")

    restored = await boot_app(tmp_path / "data", llm)
    async with running_server(restored) as server:
        await enable_actor(server, "amy")
        routes = await http_json("GET", f"{base_url(server)}/api/routes")
        items = cast(list[JsonObject], routes["items"])
        assert len(items) == 1
        assert items[0]["actor_id"] == "amy"
        inbound = await post_inbound(server, "mailbox", "hello", "route-persist-c2")
        assert inbound["delivered"] is True


async def test_http_disabled_route_is_not_rebound(test_context: SharedTestContext) -> None:
    llm = PromptConditionedProvider([(user_message_contains("hello"), reply_text("hi"))])
    actor_id = await test_context.setup_actor(llm, enable=False)
    await enable_actor(test_context.server, actor_id)
    route = test_context.route_id("disabled-route")
    await test_context.create_route(route_id=route, pattern=route, actor_id=actor_id, enabled=False)
    inbound = await post_inbound(test_context.server, route, "hello")
    assert inbound["delivered"] is False

    await http_json(
        "PUT",
        f"{base_url(test_context.server)}/api/routes/{route}",
        {"pattern": route, "actor_id": actor_id, "enabled": True},
    )
    inbound = await post_inbound(test_context.server, route, "hello")
    assert inbound["delivered"] is True
