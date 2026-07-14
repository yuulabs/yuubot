from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import httpx
import pytest
import websockets
from yuubot import Yuubot
from yuubot.app import load_process_config
from yuubot.domain import ConversationContext, LLMInput
from yuubot.domain.stream import StreamEvent, StreamStopPayload, TextDeltaPayload, Usage
from yuubot.llm import ScriptedStream
from yuubot.runtime.cache import CachePool
from yuubot.util.stream import stream_stop_event

from support.api import (
    JsonObject,
    SharedTestContext,
    base_url,
    boot_app,
    bootstrap,
    conversation_history,
    disable_actor,
    enable_integration,
    enable_actor,
    http_json,
    multipart_body,
    post_inbound,
    put_actor,
    put_integration,
    recv_ws_frames,
    running_server,
    ws_conversation_send,
    ws_url,
)
from support.llm_fakes import InterruptibleStream, scripted_reply


class CancellationAwareProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.interrupted = asyncio.Event()

    async def stream(
        self,
        input: LLMInput,
        model: str,
        context: ConversationContext,
        cache: CachePool,
        stop_event: asyncio.Event,
        metadata: dict[str, str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del input, model, context, cache, metadata
        self.started.set()
        try:
            while not stop_event.is_set():
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        self.interrupted.set()
        yield StreamEvent("stop", "stream_stop", StreamStopPayload("interrupted"))

    async def close(self) -> None:
        return None


async def test_http_bootstrap_history_and_delete(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("hi"))
    conversation_id = test_context.conversation_id("api-c1")
    await ws_conversation_send(test_context.server, "m1", actor_id, conversation_id, "hello")

    assert await http_json("GET", f"{base_url(test_context.server)}/healthz") == {"status": "ok"}
    snapshot = await bootstrap(test_context.server)
    assert any(actor["id"] == actor_id for actor in cast(list[JsonObject], snapshot["actors"]))
    conversations = await http_json("GET", f"{base_url(test_context.server)}/api/conversations")
    conversation = next(item for item in cast(list[JsonObject], conversations) if item["id"] == conversation_id)
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
    conversations = await http_json("GET", f"{base_url(test_context.server)}/api/conversations")
    assert not any(item["id"] == conversation_id for item in cast(list[JsonObject], conversations))
    await http_json(
        "GET",
        f"{base_url(test_context.server)}/api/conversations/{conversation_id}/history",
        expected_status=404,
    )


async def test_http_config_mutations_return_resource_snapshots_without_yaml(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "data", scripted_reply("hi"))
    async with running_server(app) as server:
        url = base_url(server)

        actor = await http_json(
            "PUT",
            f"{url}/api/actors/amy",
            {
                "name": "Amy",
                "workspace": str(tmp_path / "workspace"),
                "persona": "Be concise.",
                "model": {"type": "alias", "alias": "fake-model"},
                "tools": {"read": {"type": "read"}},
            },
        )
        assert actor["id"] == "amy"
        assert "tools" not in actor

        editable_actor = await http_json("GET", f"{url}/api/actors/amy")
        assert editable_actor["id"] == "amy"
        assert editable_actor["persona"] == "Be concise."
        assert "tools" not in editable_actor

        github = await put_integration(
            server,
            "github",
            name="gh",
            config={"access_token": "token", "default_owner": "yuulabs", "default_repo": "yuubot"},
        )
        assert github["type"] == "github"
        assert github["configured"] is True
        assert github["config"] == {
            "access_token": "***",
            "default_owner": "yuulabs",
            "default_repo": "yuubot",
            "base_url": "https://api.github.com",
        }

        github = await put_integration(
            server,
            "github",
            name="gh",
            config={"access_token": "***", "default_owner": "openai", "default_repo": "codex"},
        )
        assert github["config"] == {
            "access_token": "***",
            "default_owner": "openai",
            "default_repo": "codex",
            "base_url": "https://api.github.com",
        }

        github = await http_json("POST", f"{url}/api/integrations/github/enable", {})
        assert github["type"] == "github"
        assert github["enabled"] is True

        github = await http_json("POST", f"{url}/api/integrations/github/disable", {})
        assert github["type"] == "github"
        assert github["enabled"] is False

        decoded = await put_integration(server, "github", name="GitHub", config={"access_token": "secret"})
        assert decoded["type"] == "github"
        assert decoded["name"] == "GitHub"

        invalid = await http_json(
            "PUT",
            f"{url}/api/integrations/github/config",
            {"name": "GitHub", "config": "not-an-object"},
            expected_status=400,
        )
        assert invalid["error"]["code"] == "bad_request"

        web = await put_integration(
            server,
            "web",
            name="web",
            config={"tavily_api_key": "tvly-secret", "jina_api_key": "jina-secret"},
        )
        assert web["config"] == {
            "tavily_api_key": "***",
            "jina_api_key": "***",
            "read_backends": ["jina", "tavily", "httpx"],
            "tavily_base_url": "https://api.tavily.com",
            "jina_base_url": "https://r.jina.ai",
            "timeout_s": 30.0,
            "jina_timeout_s": 30.0,
            "user_agent": "yuubot/0.1",
            "max_read_bytes": 1048576,
            "max_read_chars": 12000,
            "max_download_bytes": 104857600,
            "tavily_extract_depth": "basic",
            "tavily_extract_format": "markdown",
        }
        enabled_web = await enable_integration(server, "web")
        assert enabled_web["enabled"] is True

        invalid_integer = await http_json(
            "PUT",
            f"{url}/api/integrations/web/config",
            {"name": "web", "config": {"tavily_api_key": "secret", "max_read_bytes": "1048576"}},
            expected_status=400,
        )
        assert invalid_integer["error"]["code"] == "bad_request"
        assert "max_read_bytes" in str(invalid_integer["error"])
        assert not (tmp_path / "data" / "config.yaml").exists()

        deleted = await http_json("DELETE", f"{url}/api/actors/amy")
        assert deleted == {"id": "amy", "deleted": True}
        assert (await bootstrap(server))["actors"] == []

    restored = await boot_app(tmp_path / "data")
    async with running_server(restored) as server:
        restored_bootstrap = await bootstrap(server)
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

    assert snapshot.actors == []
    assert "secret" not in repr(snapshot)
    github = next(item for item in snapshot.integrations if item.type == "github")
    assert github.configured is False
    assert github.enabled is False


async def test_http_rejects_invalid_integration_config_before_enable(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "data")
    async with running_server(app) as server:
        response = await http_json(
            "PUT",
            f"{base_url(server)}/api/integrations/github/config",
            {"name": "gh", "config": {"default_owner": "yuulabs"}},
            expected_status=400,
        )
        assert response["error"]["code"] == "bad_request"
        assert "access_token" in str(response["error"])

        snapshot = await bootstrap(server)
        github = next(item for item in cast(list[JsonObject], snapshot["integrations"]) if item["type"] == "github")
        assert github["configured"] is False
        assert github["enabled"] is False
        assert github["last_error"] is None

    restored = await boot_app(tmp_path / "data")
    async with running_server(restored) as server:
        restored_github = next(
            item for item in cast(list[JsonObject], (await bootstrap(server))["integrations"]) if item["type"] == "github"
        )
    assert restored_github["configured"] is False
    assert restored_github["last_error"] is None


async def test_http_bootstrap_exposes_integration_schemas(shared_server: object) -> None:
    integrations = cast(list[JsonObject], (await bootstrap(shared_server))["integrations"])
    github = next(item for item in integrations if item["type"] == "github")
    web = next(item for item in integrations if item["type"] == "web")
    defs = cast(JsonObject, cast(JsonObject, github["config_schema"])["$defs"])
    assert "access_token" in cast(JsonObject, cast(JsonObject, defs["GitHubConfig"])["properties"])
    assert web["config_schema"]


async def test_http_serves_actor_workspace_files_with_containment(test_context: SharedTestContext, tmp_path: Path) -> None:
    workspace = test_context.workspace
    workspace.mkdir(exist_ok=True)
    workspace.joinpath("notes").mkdir()
    workspace.joinpath("notes", "memo.txt").write_text("hello", encoding="utf-8")
    tmp_path.joinpath("outside.txt").write_text("secret", encoding="utf-8")
    workspace.joinpath("escape").symlink_to(tmp_path / "outside.txt")

    actor_id = await test_context.setup_actor(ScriptedStream([]))
    url = base_url(test_context.server)
    listing = await http_json("GET", f"{url}/api/actors/{actor_id}/browse?path=notes")
    assert listing["path"] == "notes"
    assert cast(list[JsonObject], listing["entries"])[0]["path"] == "notes/memo.txt"

    async with httpx.AsyncClient() as client:
        response = await client.get(f"{url}/api/actors/{actor_id}/files/notes/memo.txt", timeout=10.0)
        downloaded = await client.get(f"{url}/api/actors/{actor_id}/files/notes/memo.txt?download=true", timeout=10.0)
        partial = await client.get(f"{url}/api/actors/{actor_id}/files/notes/memo.txt", headers={"Range": "bytes=1-3"}, timeout=10.0)
        assert response.text == "hello"
        assert response.headers["content-disposition"].startswith("inline;")
        assert downloaded.headers["content-disposition"].startswith("attachment;")
        assert partial.status_code == 206
        assert partial.text == "ell"

    for path in ("../outside.txt", "escape"):
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{url}/api/actors/{actor_id}/files/{path}", timeout=10.0)
            assert response.status_code in {400, 404}


async def test_http_edits_utf8_workspace_file_with_etag_conflict_protection(test_context: SharedTestContext) -> None:
    workspace = test_context.workspace
    workspace.mkdir(exist_ok=True)
    target = workspace / "memo.md"
    target.write_text("first", encoding="utf-8")
    actor_id = await test_context.setup_actor(ScriptedStream([]))
    url = base_url(test_context.server)

    async with httpx.AsyncClient() as client:
        loaded = await client.get(f"{url}/api/actors/{actor_id}/files/memo.md")
        assert loaded.status_code == 200
        etag = loaded.headers["etag"]

        saved = await client.put(
            f"{url}/api/actors/{actor_id}/files/memo.md",
            content="second",
            headers={"Content-Type": "text/plain; charset=utf-8", "If-Match": etag},
        )
        assert saved.status_code == 200
        assert saved.headers["etag"] != etag
        assert target.read_text(encoding="utf-8") == "second"

        conflict = await client.put(
            f"{url}/api/actors/{actor_id}/files/memo.md",
            content="stale",
            headers={"Content-Type": "text/plain; charset=utf-8", "If-Match": etag},
        )
        assert conflict.status_code == 412
        assert target.read_text(encoding="utf-8") == "second"


async def test_http_rejects_non_utf8_workspace_file_edit(test_context: SharedTestContext) -> None:
    workspace = test_context.workspace
    workspace.mkdir(exist_ok=True)
    target = workspace / "binary.dat"
    target.write_bytes(b"\xff\x00")
    actor_id = await test_context.setup_actor(ScriptedStream([]))
    url = base_url(test_context.server)

    async with httpx.AsyncClient() as client:
        loaded = await client.get(f"{url}/api/actors/{actor_id}/files/binary.dat")
        response = await client.put(
            f"{url}/api/actors/{actor_id}/files/binary.dat",
            content="text",
            headers={"Content-Type": "text/plain; charset=utf-8", "If-Match": loaded.headers["etag"]},
        )
    assert response.status_code == 400
    assert target.read_bytes() == b"\xff\x00"


async def test_http_rejects_workspace_file_edit_through_symlink(test_context: SharedTestContext) -> None:
    workspace = test_context.workspace
    workspace.mkdir(exist_ok=True)
    target = workspace / "target.txt"
    target.write_text("original", encoding="utf-8")
    (workspace / "alias.txt").symlink_to(target)
    actor_id = await test_context.setup_actor(ScriptedStream([]))
    url = base_url(test_context.server)

    async with httpx.AsyncClient() as client:
        loaded = await client.get(f"{url}/api/actors/{actor_id}/files/target.txt")
        response = await client.put(
            f"{url}/api/actors/{actor_id}/files/alias.txt",
            content="changed",
            headers={"Content-Type": "text/plain; charset=utf-8", "If-Match": loaded.headers["etag"]},
        )
    assert response.status_code == 400
    assert target.read_text(encoding="utf-8") == "original"


async def test_http_uploads_actor_workspace_files(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(ScriptedStream([]))
    url = base_url(test_context.server)
    boundary = "yuubot-test-boundary"
    body = multipart_body(boundary, "report.txt", "text/plain", b"hello")
    first = await http_json(
        "POST",
        f"{url}/api/actors/{actor_id}/uploads",
        body,
        f"multipart/form-data; boundary={boundary}",
    )
    second = await http_json(
        "POST",
        f"{url}/api/actors/{actor_id}/uploads",
        body,
        f"multipart/form-data; boundary={boundary}",
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
    actor_id = await test_context.setup_actor(ScriptedStream([]))
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
        f"multipart/form-data; boundary={boundary}",
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
    actor_id = await test_context.setup_actor(ScriptedStream([]))
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
    actor_id = await test_context.setup_actor(ScriptedStream([]), enable=False)
    test_context.workspace.mkdir(exist_ok=True)
    test_context.workspace.joinpath("note.txt").write_text("hello", encoding="utf-8")

    listing = await http_json("GET", f"{base_url(test_context.server)}/api/actors/{actor_id}/browse")
    assert "note.txt" in {cast(str, entry["path"]) for entry in cast(list[JsonObject], listing["entries"])}


async def test_ws_conversation_send(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("hi"))
    conversation_id = test_context.conversation_id("ws-c1")
    frames = await ws_conversation_send(
        test_context.server,
        "m1",
        actor_id,
        conversation_id,
        [{"kind": "text", "text": "hello", "mime": "text/plain"}],
    )
    assert frames[0] == {"id": "m1", "type": "conversation.send.accepted", "payload": {"conversation_id": conversation_id}}
    delta = next(frame for frame in frames if frame["type"] == "conversation.delta")
    assert cast(JsonObject, cast(JsonObject, delta["payload"])["chunk"])["kind"] == "text_delta"
    assert (await conversation_history(test_context.server, conversation_id))[-1]["kind"] == "gen_text"


async def test_ws_conversation_send_normalizes_workspace_refs(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("hi"))
    conversation_id = test_context.conversation_id("ws-ref")
    await ws_conversation_send(
        test_context.server,
        "m1",
        actor_id,
        conversation_id,
        [
            {"kind": "file", "path": "uploads/image-jpeg/one.jpg", "mime": "image/jpeg"},
            {"kind": "text", "text": " cc ", "mime": "text/plain"},
            {"kind": "file", "path": "uploads/image-jpeg/two.jpg", "mime": "image/jpeg"},
        ],
    )
    history = await conversation_history(test_context.server, conversation_id)
    assert history[0]["kind"] == "input"
    payload = cast(JsonObject, history[0]["payload"])
    content = cast(list[JsonObject], payload["content"])
    assert len(content) == 1
    assert content[0]["kind"] == "text"
    assert cast(str, content[0]["text"]).endswith(
        "[[ uploads/image-jpeg/one.jpg ]] cc [[ uploads/image-jpeg/two.jpg ]]"
    )


async def test_ws_preassigned_conversation_id_first_message(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("hello from draft"))
    conversation_id = test_context.conversation_id("draft-first")
    frames = await ws_conversation_send(
        test_context.server,
        "draft-m1",
        actor_id,
        conversation_id,
        "hi",
    )
    assert frames[0] == {
        "id": "draft-m1",
        "type": "conversation.send.accepted",
        "payload": {"conversation_id": conversation_id},
    }
    history = await conversation_history(test_context.server, conversation_id)
    assert [item["kind"] for item in history][-2:] == ["input", "gen_text"]


async def test_ws_conversation_send_survives_client_disconnect_until_interrupt(test_context: SharedTestContext) -> None:
    provider = CancellationAwareProvider()
    actor_id = await test_context.setup_actor(provider)
    conversation_id = test_context.conversation_id("ws-cancel")
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
        await provider.started.wait()
    await asyncio.sleep(0.05)
    assert not provider.cancelled.is_set()
    detail = await http_json("GET", f"{base_url(test_context.server)}/api/conversations/{conversation_id}")
    assert detail["active"] is True

    interrupted = await http_json(
        "POST",
        f"{base_url(test_context.server)}/api/admin/interrupt",
        {"conversation_id": conversation_id},
    )
    assert interrupted == {"conversation_id": conversation_id, "interrupted": True}
    await asyncio.wait_for(provider.interrupted.wait(), timeout=5)
    history = await conversation_history(test_context.server, conversation_id)
    assert [item["kind"] for item in history] == ["input"]


async def test_ws_second_send_on_same_connection_does_not_duplicate_stream_frames(test_context: SharedTestContext) -> None:
    provider = ScriptedStream(
        [
            [
                StreamEvent("text-1", "text_delta", TextDeltaPayload("first")),
                stream_stop_event("stop", Usage(), {}),
            ],
            [
                StreamEvent("text-1", "text_delta", TextDeltaPayload("hel")),
                StreamEvent("text-1", "text_delta", TextDeltaPayload("lo")),
                stream_stop_event("stop", Usage(), {}),
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
            if frame.get("type") == "conversation.commit" and cast(JsonObject, frame["payload"])["continues"] is False:
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
            if frame.get("type") == "conversation.commit" and cast(JsonObject, frame["payload"])["continues"] is False:
                break

    text_delta_frames = [
        frame
        for frame in second_turn_frames
        if frame.get("type") == "conversation.delta"
        and cast(JsonObject, cast(JsonObject, frame["payload"])["chunk"])["kind"] == "text_delta"
    ]
    assert len(text_delta_frames) == 2
    texts: list[str] = []
    for frame in text_delta_frames:
        payload = cast(JsonObject, frame["payload"])
        event = cast(JsonObject, payload["chunk"])
        event_payload = cast(JsonObject, event["payload"])
        texts.append(cast(str, event_payload["text"]))
    assert texts == ["hel", "lo"]


async def test_ws_direct_stream_survives_runtime_eventbus_noisy_backpressure(
    test_context: SharedTestContext,
) -> None:
    provider = ScriptedStream(
        [
            [
                StreamEvent("text-1", "text_delta", TextDeltaPayload("hel")),
                StreamEvent("text-1", "text_delta", TextDeltaPayload("lo")),
                stream_stop_event("stop", Usage(), {}),
            ],
        ]
    )
    actor_id = await test_context.setup_actor(provider)
    app = cast(Yuubot, getattr(test_context.server, "app"))
    old_queue = app.runtime.eventbus._queue  # noqa: SLF001
    app.runtime.eventbus._queue = asyncio.Queue(maxsize=1)  # noqa: SLF001
    conversation_id = test_context.conversation_id("ws-direct-stream")
    try:
        frames = await ws_conversation_send(
            test_context.server,
            "m1",
            actor_id,
            conversation_id,
            "hello",
        )
    finally:
        app.runtime.eventbus._queue = old_queue  # noqa: SLF001

    texts: list[str] = []
    for frame in frames:
        if frame.get("type") != "conversation.delta":
            continue
        event = cast(JsonObject, cast(JsonObject, frame["payload"])["chunk"])
        if event.get("kind") != "text_delta":
            continue
        payload = cast(JsonObject, event["payload"])
        texts.append(cast(str, payload["text"]))

    assert texts == ["hel", "lo"]


async def test_ws_rejects_busy_conversation(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(InterruptibleStream())
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
        lambda frame, _: frame.get("type") == "error"
        and cast(JsonObject, frame["error"])["code"] == "conversation_busy",
    )
    assert frames[0]["type"] == "conversation.send.accepted"
    busy = next(frame for frame in frames if frame["type"] == "error")
    assert busy["id"] == "m2"
    assert cast(JsonObject, busy["error"])["code"] == "conversation_busy"


async def test_ws_interrupts_running_conversation(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(InterruptibleStream())
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
                for interrupt_id in ("m2", "m2-repeat"):
                    await ws.send(
                        json.dumps(
                            {
                                "id": interrupt_id,
                                "type": "conversation.interrupt",
                                "payload": {"conversation_id": conversation_id},
                            }
                        )
                    )
            if frame["type"] == "conversation.commit" and cast(JsonObject, frame["payload"])["continues"] is False:
                break

        await ws.send(
            json.dumps(
                {
                    "id": "m3",
                    "type": "conversation.send",
                    "payload": {
                        "actor_id": actor_id,
                        "conversation_id": conversation_id,
                        "content": [{"kind": "text", "text": "after interrupt"}],
                    },
                }
            )
        )
        while True:
            frame = cast(JsonObject, json.loads(await asyncio.wait_for(ws.recv(), timeout=10)))
            frames.append(frame)
            if (
                frame["type"] == "conversation.commit"
                and cast(JsonObject, frame["payload"])["continues"] is False
            ):
                break

    assert any(
        frame["type"] == "conversation.interrupt.result" and cast(JsonObject, frame["payload"])["interrupted"] is True
        for frame in frames
    )
    assert {
        frame.get("id")
        for frame in frames
        if frame.get("type") == "conversation.interrupt.result"
        and cast(JsonObject, frame["payload"])["interrupted"] is True
    } >= {"m2", "m2-repeat"}
    assert cast(JsonObject, frames[-1]["payload"])["continues"] is False
    assert any(
        frame.get("id") == "m3" and frame.get("type") == "conversation.send.accepted"
        for frame in frames
    )
    history = await conversation_history(test_context.server, conversation_id)
    assert sum(item["kind"] == "input" for item in history) == 2


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


async def test_ws_subscribes_conversation_commits(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("hi"))
    conversation_id = test_context.conversation_id("history-ws")
    appended: JsonObject | None = None
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
            if frame["type"] == "error":
                raise AssertionError(frame)
            payload = cast(JsonObject, frame["payload"])
            append = cast(list[JsonObject], payload.get("append", []))
            if frame["type"] == "conversation.commit" and any(item.get("kind") == "gen_text" for item in append):
                appended = frame
                break

    assert appended is not None
    item = cast(list[JsonObject], cast(JsonObject, appended["payload"])["append"])[0]
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
            "delivery": "manual",
            "wait_s": 0,
            "ttl_s": 3600,
        },
    )
    frames = await recv_ws_frames(
        test_context.server,
        [{"id": "t1", "type": "task.subscribe", "payload": {"task_id": task["id"]}}],
        lambda frame, _: (
            frame.get("type") == "task.event"
            and cast(JsonObject, frame["payload"])["status"] == "done"
            and "ready" in str(cast(JsonObject, frame["payload"]).get("stdout", ""))
        ),
    )

    task_events = [frame for frame in frames if frame["type"] == "task.event"]
    assert any("ready" in str(cast(JsonObject, frame["payload"])["stdout"]) for frame in task_events)
    assert cast(JsonObject, task_events[-1]["payload"])["status"] == "done"


async def test_ws_subscribes_completed_task_with_stdout(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor()
    conversation_id = test_context.conversation_id("task-c1")
    task = await http_json(
        "POST",
        f"{base_url(test_context.server)}/api/tasks",
        {
            "name": test_context.name("demo"),
            "shell": "echo ready",
            "intro": "test",
            "owner": f"actor:{actor_id}:conv:{conversation_id}",
            "delivery": "manual",
            "wait_s": 5,
            "ttl_s": 3600,
        },
    )
    assert task["status"] == "done"
    assert "ready" in str(task["stdout_tail"])

    frames = await recv_ws_frames(
        test_context.server,
        [{"id": "t1", "type": "task.subscribe", "payload": {"task_id": task["id"]}}],
        lambda frame, _: frame.get("type") == "task.event"
        and cast(JsonObject, frame["payload"])["status"] == "done",
    )

    task_events = [frame for frame in frames if frame["type"] == "task.event"]
    assert cast(JsonObject, task_events[-1]["payload"])["status"] == "done"
    assert "ready" in str(cast(JsonObject, task_events[-1]["payload"])["stdout"])


async def test_http_manual_task_requires_ttl(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor()
    conversation_id = test_context.conversation_id("task-ttl-c1")
    body: JsonObject = {
        "name": test_context.name("demo"),
        "shell": "true",
        "intro": "test",
        "owner": f"actor:{actor_id}:conv:{conversation_id}",
        "delivery": "manual",
        "wait_s": 0,
    }

    await http_json("POST", f"{base_url(test_context.server)}/api/tasks", body, expected_status=400)
    await http_json(
        "POST",
        f"{base_url(test_context.server)}/api/tasks",
        {**body, "ttl_s": 3601},
        expected_status=400,
    )


async def test_http_expired_manual_task_returns_404(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor()
    conversation_id = test_context.conversation_id("task-expire-c1")
    app = getattr(test_context.server, "app")
    assert isinstance(app, Yuubot)
    now = 0.0
    app.runtime.tasks.terminal_records.now = lambda: now
    task = await http_json(
        "POST",
        f"{base_url(test_context.server)}/api/tasks",
        {
            "name": test_context.name("expire"),
            "shell": "echo expiring",
            "intro": "test",
            "owner": f"actor:{actor_id}:conv:{conversation_id}",
            "delivery": "manual",
            "wait_s": 5,
            "ttl_s": 1,
        },
    )

    assert "expiring" in str(task["stdout_tail"])
    now = 1.0
    await http_json("GET", f"{base_url(test_context.server)}/api/tasks/{task['id']}", expected_status=404)


async def test_http_disable_actor_closes_active_conversation(test_context: SharedTestContext) -> None:
    actor_id = await test_context.setup_actor(scripted_reply("hi"))
    conversation_id = test_context.conversation_id("disable-c1")
    app = getattr(test_context.server, "app")
    assert isinstance(app, Yuubot)
    assert f"actor:{actor_id}" in app.runtime.mailboxes
    await ws_conversation_send(test_context.server, "m1", actor_id, conversation_id, "hello")
    assert (await conversation_history(test_context.server, conversation_id))[-1]["kind"] == "gen_text"
    await disable_actor(test_context.server, actor_id)
    snapshot = await bootstrap(test_context.server)
    actor = next(item for item in cast(list[JsonObject], snapshot["actors"]) if item["id"] == actor_id)
    assert actor["enabled"] is False
    assert actor["status"] == "disabled"
    assert f"actor:{actor_id}" not in app.runtime.mailboxes


async def test_http_actor_startup_failure_visible_in_bootstrap(tmp_path: Path) -> None:
    app = await boot_app(tmp_path / "data", scripted_reply("hi"))
    async with running_server(app) as server:
        await put_actor(server, "amy", workspace=tmp_path / "workspace", model="fake-model")
        await enable_actor(server, "amy")

    restored = await boot_app(tmp_path / "data")
    async with running_server(restored) as server:
        actor = cast(list[JsonObject], (await bootstrap(server))["actors"])[0]
    assert actor["id"] == "amy"
    assert actor["enabled"] is True
    assert actor["status"] == "blocked"
