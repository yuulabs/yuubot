from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast

import httpx
import pytest
import websockets
from yuubot import Yuubot
from yuubot.app import load_process_config
from yuubot.db import Database
from yuubot.domain.stream import StreamEvent, Usage
from yuubot.llm import ScriptedProvider
from yuubot.util.stream import stream_stop_event

from support.api import (
    JsonObject,
    SharedTestContext,
    base_url,
    boot_app,
    bootstrap,
    conversation_history,
    disable_actor,
    enable_actor,
    http_json,
    multipart_body,
    post_inbound,
    put_actor,
    put_integration,
    put_provider,
    recv_ws_frames,
    running_server,
    ws_conversation_send,
    ws_url,
)
from support.llm_fakes import InterruptibleProvider, scripted_reply


async def test_http_bootstrap_history_and_delete(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("hi"))
    conversation_id = test_context.conversation_id("api-c1")
    await ws_conversation_send(test_context.server, command_id="m1", actor_id=actor_id, conversation_id=conversation_id, content="hello")

    assert await http_json("GET", f"{base_url(test_context.server)}/healthz") == {"status": "ok"}
    snapshot = await bootstrap(test_context.server)
    assert any(actor["id"] == actor_id for actor in cast(list[JsonObject], snapshot["actors"]))
    conversation = next(item for item in cast(list[JsonObject], snapshot["conversations"]) if item["id"] == conversation_id)
    assert conversation["message_count"] == 2

    detail = await http_json("GET", f"{base_url(test_context.server)}/api/conversations/{conversation_id}")
    assert detail["history_url"] == f"/api/conversations/{conversation_id}/history"
    assert detail["active"] is False

    history = await http_json("GET", f"{base_url(test_context.server)}/api/conversations/{conversation_id}/history")
    assert history["conversation_id"] == conversation_id
    assert [item["kind"] for item in cast(list[JsonObject], history["items"])][-2:] == ["input", "gen_text"]

    assert await http_json("DELETE", f"{base_url(test_context.server)}/api/conversations/{conversation_id}") == {
        "id": conversation_id,
        "deleted": True,
    }
    assert not any(item["id"] == conversation_id for item in cast(list[JsonObject], (await bootstrap(test_context.server))["conversations"]))
    await http_json(
        "GET",
        f"{base_url(test_context.server)}/api/conversations/{conversation_id}/history",
        expected_status=404,
    )


async def test_http_config_mutations_return_bootstrap_without_yaml(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "data", provider=scripted_reply("hi"))
    async with running_server(app) as server:
        url = base_url(server)
        protocols = await http_json("GET", f"{url}/api/provider-protocols")
        [protocol] = cast(list[JsonObject], protocols["items"])
        assert protocol["protocol"] == "openai-compatible"
        assert "config_schema" in protocol

        snapshot = await http_json(
            "PUT",
            f"{url}/api/providers/fake",
            {
                "name": "Fake",
                "protocol": "openai-compatible",
                "config": {"endpoint": "", "api_key": "test-key", "options": {}},
            },
        )
        assert snapshot["providers"] == [
            {
                "id": "fake",
                "name": "Fake",
                "protocol": "openai-compatible",
                "configured": True,
                "last_error": None,
                "model_count": 0,
                "configured_model_count": 0,
            }
        ]
        assert "deployment" not in snapshot
        assert "llms" not in snapshot
        await http_json(
            "PUT",
            f"{url}/api/providers/fake/model-cards/fake-model",
            {"selector": "fake-model", "toolcall": True, "input_price_per_million": 1.0},
        )

        snapshot = await http_json(
            "PUT",
            f"{url}/api/actors/amy",
            {
                "name": "Amy",
                "workspace": str(tmp_path / "workspace"),
                "persona": "Be concise.",
                "provider": "fake",
                "model": {"selector": "fake-model", "reasoning_effort": " high "},
                "tools": {"read": {"type": "read"}},
            },
        )
        actor = cast(list[JsonObject], snapshot["actors"])[0]
        assert actor["id"] == "amy"
        assert "tools" not in actor

        editable_actor = await http_json("GET", f"{url}/api/actors/amy")
        assert editable_actor["id"] == "amy"
        assert editable_actor["persona"] == "Be concise."
        assert editable_actor["provider"] == "fake"
        assert editable_actor["model"] == {
            "selector": "fake-model",
            "reasoning_effort": "high",
            "vision": False,
            "toolcall": True,
            "json": True,
            "input_price_per_million": 1.0,
            "cached_input_price_per_million": 0.0,
            "output_price_per_million": 0.0,
        }
        assert "tools" not in editable_actor

        snapshot = await put_integration(
            server,
            "github",
            name="gh",
            config={"access_token": "token", "default_owner": "yuulabs", "default_repo": "yuubot"},
        )
        github = next(item for item in cast(list[JsonObject], snapshot["integrations"]) if item["type"] == "github")
        assert github["configured"] is True
        assert github["config"] == {
            "access_token": "***",
            "default_owner": "yuulabs",
            "default_repo": "yuubot",
        }

        snapshot = await put_integration(
            server,
            "github",
            name="gh",
            config={"access_token": "***", "default_owner": "openai", "default_repo": "codex"},
        )
        github = next(item for item in cast(list[JsonObject], snapshot["integrations"]) if item["type"] == "github")
        assert github["config"] == {
            "access_token": "***",
            "default_owner": "openai",
            "default_repo": "codex",
        }

        snapshot = await http_json("POST", f"{url}/api/integrations/github/enable", {})
        github = next(item for item in cast(list[JsonObject], snapshot["integrations"]) if item["type"] == "github")
        assert github["enabled"] is True

        snapshot = await http_json("POST", f"{url}/api/integrations/github/disable", {})
        github = next(item for item in cast(list[JsonObject], snapshot["integrations"]) if item["type"] == "github")
        assert github["enabled"] is False
        assert not (tmp_path / "data" / "config.yaml").exists()

        snapshot = await http_json("DELETE", f"{url}/api/actors/amy")
        assert snapshot["actors"] == []

    restored = await boot_app(tmp_path / "data")
    async with running_server(restored) as server:
        restored_bootstrap = await bootstrap(server)
    assert [item["id"] for item in cast(list[JsonObject], restored_bootstrap["providers"])] == ["fake"]
    github = next(item for item in cast(list[JsonObject], restored_bootstrap["integrations"]) if item["type"] == "github")
    assert github["configured"] is True
    assert github["enabled"] is False


def test_process_config_reads_paths_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "env-data"
    monkeypatch.setenv("YUU_TEST_DATA_DIR", str(data_dir))
    config = tmp_path / "config.yaml"
    config.write_text(
        """
paths:
  data_dir: ${YUU_TEST_DATA_DIR}
providers:
  - id: ignored
""",
        encoding="utf-8",
    )

    assert Path(load_process_config(config).data_dir) == data_dir


async def test_from_config_file_ignores_business_seed(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
data_dir: {tmp_path / "data"}
providers:
  - id: seeded
    name: Seeded
    protocol: openai-compatible
    config:
      endpoint: ""
      api_key: secret
      options: {{}}
integrations:
  - id: github
    type: github
    name: gh
    config:
      access_token: token
      default_owner: yuulabs
      default_repo: yuubot
actors:
  - id: amy
    name: Amy
    provider: seeded
    model:
      selector: seeded-model
""",
        encoding="utf-8",
    )

    app = await Yuubot.from_config_file(config)
    try:
        snapshot = await app.bootstrap_snapshot()
    finally:
        await app.shutdown()

    assert snapshot.providers == []
    assert snapshot.actors == []
    github = next(item for item in snapshot.integrations if item.type == "github")
    assert github.configured is False
    assert github.enabled is False


async def test_http_persists_integration_enable_error(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "data")
    async with running_server(app) as server:
        await put_integration(server, "github", name="gh", config={"default_owner": "yuulabs"})
        await http_json("POST", f"{base_url(server)}/api/integrations/github/enable", {}, expected_status=422)

        snapshot = await bootstrap(server)
        github = next(item for item in cast(list[JsonObject], snapshot["integrations"]) if item["type"] == "github")
        assert github["enabled"] is False
        assert cast(JsonObject, github["last_error"])["type"] == "ValidationError"

    restored = await boot_app(tmp_path / "data")
    async with running_server(restored) as server:
        restored_github = next(
            item for item in cast(list[JsonObject], (await bootstrap(server))["integrations"]) if item["type"] == "github"
        )
    assert cast(JsonObject, restored_github["last_error"])["type"] == "ValidationError"


async def test_http_bootstrap_exposes_integration_schemas(shared_server: object) -> None:
    integrations = cast(list[JsonObject], (await bootstrap(shared_server))["integrations"])
    github = next(item for item in integrations if item["type"] == "github")
    tavily = next(item for item in integrations if item["type"] == "tavily_web")
    defs = cast(JsonObject, cast(JsonObject, github["config_schema"])["$defs"])
    assert "access_token" in cast(JsonObject, cast(JsonObject, defs["GitHubConfig"])["properties"])
    assert tavily["config_schema"]


async def test_http_serves_actor_workspace_files_with_containment(test_context: SharedTestContext, tmp_path: Path) -> None:
    workspace = test_context.workspace
    workspace.mkdir()
    workspace.joinpath("notes").mkdir()
    workspace.joinpath("notes", "memo.txt").write_text("hello", encoding="utf-8")
    tmp_path.joinpath("outside.txt").write_text("secret", encoding="utf-8")
    workspace.joinpath("escape").symlink_to(tmp_path / "outside.txt")

    actor_id = await test_context.setup_actor(ScriptedProvider([]))
    url = base_url(test_context.server)
    listing = await http_json("GET", f"{url}/api/actors/{actor_id}/browse?path=notes")
    assert listing["path"] == "notes"
    assert cast(list[JsonObject], listing["entries"])[0]["path"] == "notes/memo.txt"

    async with httpx.AsyncClient() as client:
        response = await client.get(f"{url}/api/actors/{actor_id}/files/notes/memo.txt", timeout=10.0)
        assert response.text == "hello"

    for path in ("../outside.txt", "escape"):
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{url}/api/actors/{actor_id}/files/{path}", timeout=10.0)
            assert response.status_code in {400, 404}


async def test_http_uploads_actor_workspace_files(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(ScriptedProvider([]))
    url = base_url(test_context.server)
    boundary = "yuubot-test-boundary"
    body = multipart_body(boundary, "report.txt", "text/plain", b"hello")
    first = await http_json(
        "POST",
        f"{url}/api/actors/{actor_id}/uploads",
        body,
        content_type=f"multipart/form-data; boundary={boundary}",
    )
    second = await http_json(
        "POST",
        f"{url}/api/actors/{actor_id}/uploads",
        body,
        content_type=f"multipart/form-data; boundary={boundary}",
    )
    assert first["files"] == [
        {
            "kind": "file",
            "path": "uploads/text-plain/report.txt",
            "mime": "text/plain",
            "meta": {"name": "report.txt", "size": 5},
        }
    ]
    assert cast(list[JsonObject], second["files"])[0]["path"] == "uploads/text-plain/report-1.txt"
    assert (test_context.workspace / "uploads" / "text-plain" / "report.txt").read_text(encoding="utf-8") == "hello"


async def test_http_manages_actor_workspace_entries(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(ScriptedProvider([]))
    test_context.workspace.mkdir(exist_ok=True)
    url = base_url(test_context.server)

    created = await http_json(
        "POST",
        f"{url}/api/actors/{actor_id}/workspace/directories",
        {"path": "docs"},
        expected_status=201,
    )
    assert "docs" in {cast(str, entry["path"]) for entry in cast(list[JsonObject], created["entries"])}

    boundary = "yuubot-test-boundary"
    body = multipart_body(boundary, "report.txt", "text/plain", b"hello")
    uploaded = await http_json(
        "POST",
        f"{url}/api/actors/{actor_id}/uploads?path=docs",
        body,
        content_type=f"multipart/form-data; boundary={boundary}",
    )
    assert cast(list[JsonObject], uploaded["files"])[0]["path"] == "docs/report.txt"

    renamed = await http_json(
        "POST",
        f"{url}/api/actors/{actor_id}/workspace/rename",
        {"path": "docs/report.txt", "name": "summary.txt"},
    )
    assert cast(list[JsonObject], renamed["entries"])[0]["path"] == "docs/summary.txt"

    await http_json("POST", f"{url}/api/actors/{actor_id}/workspace/directories", {"path": "archive"}, expected_status=201)
    moved = await http_json(
        "POST",
        f"{url}/api/actors/{actor_id}/workspace/move",
        {"sources": ["docs/summary.txt"], "destination": "archive"},
    )
    assert cast(list[JsonObject], moved["entries"])[0]["path"] == "archive/summary.txt"

    deleted = await http_json(
        "DELETE",
        f"{url}/api/actors/{actor_id}/workspace/entries",
        {"paths": ["archive/summary.txt", "docs"]},
    )
    assert deleted["path"] == "archive"
    assert not cast(list[JsonObject], deleted["entries"])
    assert (test_context.workspace / "archive").is_dir()
    assert not (test_context.workspace / "docs").exists()
    assert not (test_context.workspace / "archive" / "summary.txt").exists()


async def test_http_workspace_mutations_reject_unsafe_paths(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(ScriptedProvider([]))
    test_context.workspace.mkdir(exist_ok=True)
    url = base_url(test_context.server)

    await http_json(
        "POST",
        f"{url}/api/actors/{actor_id}/workspace/directories",
        {"path": "../outside"},
        expected_status=400,
    )
    await http_json(
        "DELETE",
        f"{url}/api/actors/{actor_id}/workspace/entries",
        {"paths": [""]},
        expected_status=400,
    )


async def test_http_browses_disabled_actor_workspace(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(ScriptedProvider([]), enable=False)
    test_context.workspace.mkdir(exist_ok=True)
    test_context.workspace.joinpath("note.txt").write_text("hello", encoding="utf-8")

    listing = await http_json("GET", f"{base_url(test_context.server)}/api/actors/{actor_id}/browse")
    assert "note.txt" in {cast(str, entry["path"]) for entry in cast(list[JsonObject], listing["entries"])}


async def test_ws_conversation_send(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("hi"))
    conversation_id = test_context.conversation_id("ws-c1")
    frames = await ws_conversation_send(
        test_context.server,
        command_id="m1",
        actor_id=actor_id,
        conversation_id=conversation_id,
        content=[{"kind": "text", "text": "hello", "mime": "text/plain"}],
    )
    assert frames[0] == {"id": "m1", "type": "conversation.send.accepted", "payload": {"conversation_id": conversation_id}}
    assert frames[1]["type"] == "conversation.stream"
    assert cast(JsonObject, cast(JsonObject, frames[1]["payload"])["event"])["kind"] == "text_delta"
    assert (await conversation_history(test_context.server, conversation_id))[-1]["kind"] == "gen_text"


async def test_ws_preassigned_conversation_id_first_message(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("hello from draft"))
    conversation_id = test_context.conversation_id("draft-first")
    frames = await ws_conversation_send(
        test_context.server,
        command_id="draft-m1",
        actor_id=actor_id,
        conversation_id=conversation_id,
        content="hi",
    )
    assert frames[0] == {
        "id": "draft-m1",
        "type": "conversation.send.accepted",
        "payload": {"conversation_id": conversation_id},
    }
    history = await conversation_history(test_context.server, conversation_id)
    assert [item["kind"] for item in history][-2:] == ["input", "gen_text"]


async def test_ws_conversation_send_completes_after_client_disconnect(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("survives disconnect"))
    conversation_id = test_context.conversation_id("ws-survive")
    async with websockets.connect(ws_url(test_context.server), open_timeout=5) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": "m1",
                    "type": "conversation.send",
                    "payload": {
                        "actor_id": actor_id,
                        "conversation_id": conversation_id,
                        "content": [{"kind": "text", "text": "hello"}],
                    },
                }
            )
        )
        accepted = cast(JsonObject, json.loads(await asyncio.wait_for(ws.recv(), timeout=10)))
        assert accepted["type"] == "conversation.send.accepted"
    for _ in range(50):
        history = await conversation_history(test_context.server, conversation_id)
        kinds = [item["kind"] for item in history]
        if "input" in kinds and "gen_text" in kinds:
            break
        await asyncio.sleep(0.1)
    else:
        raise AssertionError("conversation did not complete after websocket disconnect")


async def test_ws_second_send_on_same_connection_does_not_duplicate_stream_frames(test_context: SharedTestContext) -> None:
    provider = ScriptedProvider(
        [
            [
                StreamEvent(group_id="text-1", kind="text_delta", payload={"text": "first"}),
                stream_stop_event("stop", Usage(), {}, cost_estimated=False),
            ],
            [
                StreamEvent(group_id="text-1", kind="text_delta", payload={"text": "hel"}),
                StreamEvent(group_id="text-1", kind="text_delta", payload={"text": "lo"}),
                stream_stop_event("stop", Usage(), {}, cost_estimated=False),
            ],
        ]
    )
    actor_id = await test_context.setup_actor(provider)
    conversation_id = test_context.conversation_id("ws-dedupe")

    frames: list[JsonObject] = []
    async with websockets.connect(ws_url(test_context.server), open_timeout=5) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": "m1",
                    "type": "conversation.send",
                    "payload": {
                        "actor_id": actor_id,
                        "conversation_id": conversation_id,
                        "content": [{"kind": "text", "text": "hello"}],
                    },
                }
            )
        )
        while True:
            frame = cast(JsonObject, json.loads(await asyncio.wait_for(ws.recv(), timeout=30)))
            frames.append(frame)
            if frame.get("type") == "conversation.stream":
                event = cast(JsonObject, cast(JsonObject, frame["payload"])["event"])
                if event.get("kind") == "stream_stop":
                    break

        await ws.send(
            json.dumps(
                {
                    "id": "m2",
                    "type": "conversation.send",
                    "payload": {
                        "actor_id": actor_id,
                        "conversation_id": conversation_id,
                        "content": [{"kind": "text", "text": "again"}],
                    },
                }
            )
        )
        second_turn_frames: list[JsonObject] = []
        while True:
            frame = cast(JsonObject, json.loads(await asyncio.wait_for(ws.recv(), timeout=30)))
            second_turn_frames.append(frame)
            if frame.get("type") == "conversation.stream":
                event = cast(JsonObject, cast(JsonObject, frame["payload"])["event"])
                if event.get("kind") == "stream_stop":
                    break

    text_delta_frames = [
        frame
        for frame in second_turn_frames
        if frame.get("type") == "conversation.stream"
        and cast(JsonObject, cast(JsonObject, frame["payload"])["event"])["kind"] == "text_delta"
    ]
    assert len(text_delta_frames) == 2
    texts = [
        cast(str, cast(JsonObject, cast(JsonObject, frame["payload"])["event"])["payload"]["text"])
        for frame in text_delta_frames
    ]
    assert texts == ["hel", "lo"]


async def test_ws_rejects_busy_conversation(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(InterruptibleProvider())
    conversation_id = test_context.conversation_id("busy-c1")
    frames = await recv_ws_frames(
        test_context.server,
        [
            {
                "id": "m1",
                "type": "conversation.send",
                "payload": {
                    "actor_id": actor_id,
                    "conversation_id": conversation_id,
                    "content": [{"kind": "text", "text": "hello"}],
                },
            },
            {
                "id": "m2",
                "type": "conversation.send",
                "payload": {
                    "actor_id": actor_id,
                    "conversation_id": conversation_id,
                    "content": [{"kind": "text", "text": "again"}],
                },
            },
        ],
        stop_when=lambda frame, _: frame.get("type") == "error"
        and cast(JsonObject, frame["error"])["code"] == "conversation_busy",
    )
    assert frames[0]["type"] == "conversation.send.accepted"
    busy = next(frame for frame in frames if frame["type"] == "error")
    assert busy["id"] == "m2"
    assert cast(JsonObject, busy["error"])["code"] == "conversation_busy"


async def test_ws_interrupts_running_conversation(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(InterruptibleProvider())
    conversation_id = test_context.conversation_id("interrupt-ws")
    frames: list[JsonObject] = []
    async with websockets.connect(ws_url(test_context.server), open_timeout=5) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": "m1",
                    "type": "conversation.send",
                    "payload": {
                        "actor_id": actor_id,
                        "conversation_id": conversation_id,
                        "content": [{"kind": "text", "text": "hello"}],
                    },
                }
            )
        )
        while True:
            frame = cast(JsonObject, json.loads(await asyncio.wait_for(ws.recv(), timeout=10)))
            frames.append(frame)
            if frame["type"] == "conversation.send.accepted":
                await ws.send(
                    json.dumps(
                        {
                            "id": "m2",
                            "type": "conversation.interrupt",
                            "payload": {"conversation_id": conversation_id},
                        }
                    )
                )
            payload = cast(JsonObject, frame["payload"])
            event = cast(JsonObject, payload.get("event", {}))
            if frame["type"] == "conversation.stream" and event.get("kind") == "stream_stop":
                break

    assert any(
        frame["type"] == "conversation.interrupt.result" and cast(JsonObject, frame["payload"])["interrupted"] is True
        for frame in frames
    )
    final_event = cast(JsonObject, cast(JsonObject, frames[-1]["payload"])["event"])
    assert cast(JsonObject, final_event["payload"])["reason"] == "interrupted"


async def test_ws_subscribes_runtime_events(test_context: SharedTestContext) -> None:
    async with websockets.connect(ws_url(test_context.server), open_timeout=5) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": "r1",
                    "type": "runtime.events.subscribe",
                    "payload": {"kinds": ["gateway.dispatch"]},
                }
            )
        )
        while True:
            frame = cast(JsonObject, json.loads(await asyncio.wait_for(ws.recv(), timeout=10)))
            if frame["type"] == "runtime.events.subscribe.result":
                break

        await post_inbound(test_context.server, test_context.route_id("demo"), "hello")

        while True:
            frame = cast(JsonObject, json.loads(await asyncio.wait_for(ws.recv(), timeout=10)))
            if frame["type"] == "runtime.event":
                event = cast(JsonObject, frame["payload"])
                assert event["kind"] == "gateway.dispatch"
                dispatch = cast(JsonObject, event["event"])
                assert dispatch["route"] == test_context.name("demo")
                assert dispatch["delivered"] is False
                break


async def test_ws_subscribes_conversation_history_append(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("hi"))
    conversation_id = test_context.conversation_id("history-ws")
    appended: JsonObject | None = None
    async with websockets.connect(ws_url(test_context.server), open_timeout=5) as ws:
        await ws.send(
            json.dumps(
                {
                    "id": "h1",
                    "type": "conversation.history.subscribe",
                    "payload": {"conversation_id": conversation_id},
                }
            )
        )
        while True:
            frame = cast(JsonObject, json.loads(await asyncio.wait_for(ws.recv(), timeout=10)))
            if frame["type"] == "conversation.history.subscribe.result":
                await ws.send(
                    json.dumps(
                        {
                            "id": "m1",
                            "type": "conversation.send",
                            "payload": {
                                "actor_id": actor_id,
                                "conversation_id": conversation_id,
                                "content": [{"kind": "text", "text": "hello"}],
                            },
                        }
                    )
                )
            payload = cast(JsonObject, frame["payload"])
            item = cast(JsonObject, payload.get("item", {}))
            if frame["type"] == "conversation.history.append" and item.get("kind") == "gen_text":
                appended = frame
                break

    assert appended is not None
    item = cast(JsonObject, cast(JsonObject, appended["payload"])["item"])
    assert item["kind"] == "gen_text"
    assert item["payload"] == {"text": "hi"}


async def test_ws_subscribes_task_stdout_and_status(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor()
    conversation_id = test_context.conversation_id("task-c1")
    task = await http_json(
        "POST",
        f"{base_url(test_context.server)}/api/tasks",
        {
            "name": test_context.name("demo"),
            "shell": "sleep 0.05 && echo ready",
            "intro": "test",
            "owner": f"actor:{actor_id}:conv:{conversation_id}",
            "wait_s": 0,
        },
    )
    frames = await recv_ws_frames(
        test_context.server,
        [{"id": "t1", "type": "task.subscribe", "payload": {"task_id": task["id"]}}],
        stop_when=lambda frame, _: frame.get("type") == "task.event"
        and cast(JsonObject, frame["payload"])["status"] == "done",
    )

    task_events = [frame for frame in frames if frame["type"] == "task.event"]
    assert any("ready" in str(cast(JsonObject, frame["payload"])["stdout"]) for frame in task_events)
    assert cast(JsonObject, task_events[-1]["payload"])["status"] == "done"


async def test_http_disable_actor_closes_active_conversation(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("hi"))
    conversation_id = test_context.conversation_id("disable-c1")
    await ws_conversation_send(test_context.server, command_id="m1", actor_id=actor_id, conversation_id=conversation_id, content="hello")
    assert (await conversation_history(test_context.server, conversation_id))[-1]["kind"] == "gen_text"
    await disable_actor(test_context.server, actor_id)
    snapshot = await bootstrap(test_context.server)
    actor = next(item for item in cast(list[JsonObject], snapshot["actors"]) if item["id"] == actor_id)
    assert actor["enabled"] is False
    assert actor["status"] == "disabled"


async def test_http_actor_startup_failure_visible_in_bootstrap(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "data", provider=scripted_reply("hi"))
    async with running_server(app) as server:
        await put_provider(server, model="fake-model")
        await put_actor(server, "amy", workspace=tmp_path / "workspace", model="fake-model")
        await enable_actor(server, "amy")

    db = await Database.open(tmp_path / "data" / "db")
    try:
        await db.execute("delete from llm_providers where id = ?", ("fake",))
        await db.commit()
    finally:
        await db.close()

    restored = await boot_app(tmp_path / "data")
    async with running_server(restored) as server:
        actor = cast(list[JsonObject], (await bootstrap(server))["actors"])[0]
    assert actor["id"] == "amy"
    assert actor["enabled"] is True
    assert actor["status"] == "blocked"
    assert cast(JsonObject, actor["last_error"])["type"] == "KeyError"
