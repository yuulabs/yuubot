"""Tests for the built-in web integration."""

from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Callable
from typing import cast

import httpx
import pytest
import yuullm
from yuuagents.tool.files import FileToolConfig, WorkspaceFiles

from yuubot.core.actors.workspace import ActorWorkspaceResolver
from yuubot.core.gateway import Gateway
from yuubot.core.integrations import IntegrationCore, default_integration_factories
from yuubot.core.integrations.context import InvocationContext
from yuubot.core.integrations.contracts import LocalIntegrationStorage
from yuubot.core.integrations.impls.web import (
    WEB_DOWNLOAD_CAPABILITY_ID,
    WEB_READ_CAPABILITY_ID,
    WEB_SEARCH_CAPABILITY_ID,
    WebIntegrationFactory,
)
from yuubot.core.integrations.impls.web.client import WebClient, extract_page
from yuubot.core.integrations.impls.web.integration import WebIntegration
from yuubot.core.integrations.impls.web.models import (
    DownloadedFile,
    WebDownloadInput,
    WebReadInput,
    WebSearchInput,
)
from yuubot.core.routing import RouteBindings
from yuubot.core.secrets import Secret
from yuubot.resources.records import CapabilitySetRecord, IntegrationRecord
from yuubot.resources.records import ActorRecord
from yuubot.resources.store.models import ActorORM, CapabilitySetORM, IntegrationORM

_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c02"
    "0000000b4944415478da63fcff1f0003030200efbfa7db0000000049454e44ae426082"
)


async def test_search_maps_tavily_results_with_citations() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url == "https://api.tavily.test/search"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["query"] == "recent model release"
        assert body["max_results"] == 3
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Result",
                        "url": "https://example.test/post",
                        "content": "Snippet",
                        "score": 0.91,
                        "published_date": "2026-06-01",
                    }
                ]
            },
        )

    integration = _integration(handler)

    output = await integration.invoke_search(
        WebSearchInput(query="recent model release", max_results=3),
        InvocationContext(actor_id="actor"),
    )
    await integration.close()

    assert len(requests) == 1
    assert output.results[0].url == "https://example.test/post"
    assert output.results[0].snippet == "Snippet"
    assert output.results[0].score == 0.91
    assert output.results[0].citation.source == "tavily"
    assert output.results[0].citation.published_at == "2026-06-01"


def test_read_extracts_html_metadata_links_images_and_truncates() -> None:
    page = extract_page(
        url="https://example.test/articles/post",
        content_type="text/html; charset=utf-8",
        fetched_at="2026-06-28T00:00:00+00:00",
        max_chars=30,
        html="""
        <html>
          <head>
            <title>Example Title</title>
            <link rel="canonical" href="/canonical">
            <meta property="og:image" content="/og.png">
            <meta property="article:published_time" content="2026-06-01">
            <meta property="article:modified_time" content="2026-06-02">
          </head>
          <body>
            <nav>Skip-ish nav is still text</nav>
            <main>
              <h1>Heading</h1>
              <p>Hello <b>world</b> with enough text to truncate.</p>
              <a href="/next">Next</a>
              <img src="image.png" srcset="small.jpg 1x, /large.jpg 2x">
            </main>
            <script>ignored()</script>
          </body>
        </html>
        """,
    )

    assert page.title == "Example Title"
    assert page.canonical_url == "https://example.test/canonical"
    assert page.text == "Skip-ish nav is still text\nHea"
    assert page.links == ["https://example.test/next"]
    assert page.image_urls == [
        "https://example.test/og.png",
        "https://example.test/articles/image.png",
        "https://example.test/articles/small.jpg",
        "https://example.test/large.jpg",
    ]
    assert page.citation.published_at == "2026-06-01"
    assert page.citation.updated_at == "2026-06-02"


async def test_download_saves_under_actor_workspace_and_read_tool_reads_image(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://cdn.example.test/pixel.png"
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=_PNG_BYTES,
        )

    workspace = tmp_path / "workspace"
    integration = _integration(handler)

    output = await integration.invoke_download(
        WebDownloadInput(url="https://cdn.example.test/pixel.png"),
        InvocationContext(actor_id="actor", workspace_path=str(workspace)),
    )
    await integration.close()

    assert output.path == "downloads/web/pixel.png"
    assert output.bytes == len(_PNG_BYTES)
    assert (workspace / output.path).read_bytes() == _PNG_BYTES

    files = WorkspaceFiles.from_config(FileToolConfig(workspace_root=str(workspace)))
    read_output = files.read(output.path)
    assert isinstance(read_output, list)
    assert yuullm.is_image_item(read_output[1])
    assert read_output[1]["image_url"]["url"] == (workspace / output.path).as_uri()


async def test_download_rejects_traversal_and_absolute_filenames(tmp_path: Path) -> None:
    integration = _integration(lambda request: httpx.Response(200, content=b"asset"))
    context = InvocationContext(actor_id="actor", workspace_path=str(tmp_path))

    with pytest.raises(ValueError, match="relative"):
        await integration.invoke_download(
            WebDownloadInput(url="https://example.test/a.png", filename="/tmp/a.png"),
            context,
        )

    with pytest.raises(ValueError, match="traversal"):
        await integration.invoke_download(
            WebDownloadInput(url="https://example.test/a.png", filename="../a.png"),
            context,
        )
    await integration.close()


async def test_download_enforces_max_bytes(tmp_path: Path) -> None:
    integration = _integration(lambda request: httpx.Response(200, content=b"abcdef"))

    with pytest.raises(ValueError, match="exceeds limit"):
        await integration.invoke_download(
            WebDownloadInput(url="https://example.test/file.bin", max_bytes=3),
            InvocationContext(actor_id="actor", workspace_path=str(tmp_path)),
        )
    await integration.close()


async def test_read_fetches_html_via_http_client() -> None:
    integration = _integration(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<title>T</title><p>Hello</p>",
        )
    )

    page = await integration.invoke_read(
        WebReadInput(url="https://example.test/page"),
        InvocationContext(actor_id="actor"),
    )
    await integration.close()

    assert page.title == "T"
    assert page.text == "Hello"


async def test_read_requests_identity_encoding_to_avoid_decoder_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<p>Hello</p>",
        )

    integration = _integration(handler)

    page = await integration.invoke_read(
        WebReadInput(url="https://example.test/page"),
        InvocationContext(actor_id="actor"),
    )
    await integration.close()

    assert page.text == "Hello"


async def test_factory_allows_missing_tavily_key_but_search_requires_it(
    tmp_path: Path,
) -> None:
    factory = WebIntegrationFactory()
    record = IntegrationRecord(id="web-main", name="web", config={})
    instance = await factory.create(
        record,
        gateway=Gateway(RouteBindings(rules=[])),
        storage=LocalIntegrationStorage(tmp_path),
    )
    await instance.close()

    integration = WebIntegration(
        client=WebClient(
            http=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"content-type": "text/html"},
                        content=b"<p>Hello</p>",
                    )
                ),
                follow_redirects=True,
            ),
            tavily_base_url="https://api.tavily.test",
            tavily_api_key="",
            max_read_bytes=1_000_000,
            max_read_chars=80_000,
            max_download_bytes=1_000_000,
        )
    )
    page = await integration.invoke_read(
        WebReadInput(url="https://example.test/page"),
        InvocationContext(actor_id="actor"),
    )
    assert page.text == "Hello"

    with pytest.raises(ValueError, match="web.search requires a Tavily API key"):
        await integration.invoke_search(
            WebSearchInput(query="recent model release"),
            InvocationContext(actor_id="actor"),
        )
    await integration.close()


async def test_factory_declares_capabilities_and_admin_schema(tmp_path: Path) -> None:
    factory = WebIntegrationFactory()

    spec_ids = {spec.id for spec in factory.capability_specs()}
    assert spec_ids == {
        WEB_SEARCH_CAPABILITY_ID,
        WEB_READ_CAPABILITY_ID,
        WEB_DOWNLOAD_CAPABILITY_ID,
    }
    assert {spec.id: spec.effect for spec in factory.capability_specs()}[
        WEB_DOWNLOAD_CAPABILITY_ID
    ] == "write"
    assert factory.sdk_spec.import_paths == ("yext.web",)
    assert factory.routes(cast(IntegrationCore, object())) == []
    assert "result.snippet" in factory.sdk_spec.prompt_summary
    assert "result.description" in factory.sdk_spec.prompt_summary
    assert "there is no\n  `result.description` field" in factory.sdk_spec.prompt_summary
    assert "Do not spend a web.search call to discover this facade schema" in (
        factory.sdk_spec.prompt_summary
    )

    record = IntegrationRecord(
        id="web-main",
        name="web",
        config={"api_key": Secret("test-key")},
    )
    instance = await factory.create(
        record,
        gateway=Gateway(RouteBindings(rules=[])),
        storage=LocalIntegrationStorage(tmp_path),
    )
    await instance.close()


async def test_default_registry_exposes_web_kind() -> None:
    kinds = {
        kind.name: kind
        for kind in default_integration_factories().integration_kinds()
    }
    web = kinds["web"]
    properties = cast(dict[str, object], web.config_schema["properties"])
    api_key = cast(dict[str, object], properties["api_key"])

    assert api_key["format"] == "secret"
    assert {cap.id for cap in web.capabilities} == {
        WEB_SEARCH_CAPABILITY_ID,
        WEB_READ_CAPABILITY_ID,
        WEB_DOWNLOAD_CAPABILITY_ID,
    }
    assert web.sdk_spec.import_paths == ("yext.web",)


async def test_integration_core_injects_actor_workspace(
    resources,
    tmp_path: Path,
) -> None:
    await resources.repository.insert(
        IntegrationORM,
        IntegrationRecord(
            id="web-main",
            name="web",
            config={"api_key": Secret("test-key")},
            enabled=True,
        ),
    )
    await resources.repository.insert(
        CapabilitySetORM,
        CapabilitySetRecord(id="cap-web", name="web", integration_ids=("web-main",)),
    )
    await resources.repository.insert(
        ActorORM,
        ActorRecord(
            id="actor-1",
            name="actor",
            persona_prompt="",
            capability_set_id="cap-web",
            llm_backend_id="",
            model="",
            enabled=True,
        ),
    )

    integration = _integration(lambda request: httpx.Response(200, content=_PNG_BYTES))
    factories = default_integration_factories()
    integrations = IntegrationCore(
        repository=resources.repository,
        factories=factories,
        gateway=Gateway(RouteBindings(rules=[])),
        integrations_root=tmp_path / "integrations",
        workspace_resolver=ActorWorkspaceResolver(tmp_path / "workspaces"),
    )
    integrations._instances["web-main"] = integration
    integrations._instance_records["web-main"] = IntegrationRecord(
        id="web-main",
        name="web",
        config={"api_key": Secret("test-key")},
        enabled=True,
    )
    integrations._capabilities_index.update(
        {
            ("web-main", capability.id): capability
            for capability in integration.capabilities()
        }
    )

    result = cast(
        DownloadedFile,
        await integrations.invoke(
            actor_id="actor-1",
            integration_id="web-main",
            capability_id=WEB_DOWNLOAD_CAPABILITY_ID,
            payload={"url": "https://example.test/pixel.png"},
        ),
    )
    await integration.close()

    assert result.path == "downloads/web/pixel.png"
    workspace = ActorWorkspaceResolver(tmp_path / "workspaces").resolve("actor-1")
    assert (workspace / result.path).exists()


def _integration(
    handler: Callable[[httpx.Request], httpx.Response],
) -> WebIntegration:
    transport = httpx.MockTransport(handler)
    return WebIntegration(
        client=WebClient(
            http=httpx.AsyncClient(transport=transport, follow_redirects=True),
            tavily_base_url="https://api.tavily.test",
            tavily_api_key="test-key",
            max_read_bytes=1_000_000,
            max_read_chars=80_000,
            max_download_bytes=1_000_000,
        )
    )
