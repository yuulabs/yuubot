"""Built-in web search/read/download integration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec

from yuubot.core.capabilities import (
    AnyCapability,
    AnyCapabilitySpec,
    Capability,
    CapabilitySpec,
)
from yuubot.core.gateway import Gateway
from yuubot.core.integrations.context import InvocationContext
from yuubot.core.integrations.contracts import (
    IntegrationSdkSpec,
    IntegrationStorage,
    ReactionKind,
)
from yuubot.core.integrations.impls.web.client import WebClient
from yuubot.core.integrations.impls.web.models import (
    DownloadedFile,
    SearchResult,
    WebConfig,
    WebDownloadInput,
    WebPage,
    WebReadInput,
    WebSearchInput,
    WebSearchOutput,
)
from yuubot.resources.records import IntegrationRecord

if TYPE_CHECKING:
    from yuubot.core.integrations.core import IntegrationCore

WEB_INTEGRATION_NAME = "web"
WEB_SEARCH_CAPABILITY_ID = "web.search"
WEB_READ_CAPABILITY_ID = "web.read"
WEB_DOWNLOAD_CAPABILITY_ID = "web.download"

WEB_INTEGRATION_DESCRIPTION = (
    "Web integration for Tavily search, textual page extraction, and "
    "workspace-scoped remote asset downloads."
)

WEB_SOURCE_PATH_CONVENTION = (
    "Web is exposed only as agent-callable capabilities. It does not emit "
    "inbound messages and has no source path convention."
)

_WEB_SDK_PROMPT_SUMMARY = """\
- Surface: execute_python
- Python facade: yext.web hand-written API.

Call signatures and returned fields:
- `await web.search(query: str, *, max_results: int = 5)` returns
  `list[SearchResult]`.
- `SearchResult` fields are exactly: `url`, `title`, `snippet`, `score`,
  `citation`. Use `result.snippet` for the short description; there is no
  `result.description` field.
- `await web.read(url: str, *, max_chars: int = 12000)` returns a `WebPage`
  with `url`, `canonical_url`, `title`, `text`, `links`, `image_urls`,
  `citation`.
- `await web.download(url: str, *, filename: str = "", max_bytes: int = 0)`
  returns a downloaded file object with `path`, `url`, `content_type`,
  `bytes`, `sha256`, `citation`.

Examples:
```python
import yext.web as web

results = await web.search("site:openai.com model release", max_results=5)
for result in results:
    print(result.title)
    print(result.url)
    print(result.snippet)
    print("---")

page = await web.read(results[0].url, max_chars=12_000)
asset = await web.download(page.image_urls[0], max_bytes=2_000_000)
# Then call builtin read({"path": asset.path}) to inspect the downloaded image.
```

Workflow:
- Use web.search for discovery.
- Use web.read to extract page text, links, image URLs, and citation metadata.
- Use web.download only when a remote asset should be saved into the actor
  workspace under downloads/web/ for builtin read/edit/write/bash tools.
- Do not spend a web.search call to discover this facade schema; use the
  documented fields above."""

WEB_SDK_SPEC = IntegrationSdkSpec(
    import_paths=("yext.web",),
    prompt_summary=_WEB_SDK_PROMPT_SUMMARY,
    doc_modules=("yext.web",),
)

WEB_SEARCH_CAPABILITY_SPEC = CapabilitySpec[WebSearchInput, WebSearchOutput](
    id=WEB_SEARCH_CAPABILITY_ID,
    name="Search web",
    description="Search the web via Tavily and return cited result summaries.",
    input_type=WebSearchInput,
    output_type=WebSearchOutput,
    namespace="web",
    effect="read",
)

WEB_READ_CAPABILITY_SPEC = CapabilitySpec[WebReadInput, WebPage](
    id=WEB_READ_CAPABILITY_ID,
    name="Read web page",
    description="Fetch a URL and extract title, text, links, images, and citation metadata.",
    input_type=WebReadInput,
    output_type=WebPage,
    namespace="web",
    effect="read",
)

WEB_DOWNLOAD_CAPABILITY_SPEC = CapabilitySpec[WebDownloadInput, DownloadedFile](
    id=WEB_DOWNLOAD_CAPABILITY_ID,
    name="Download web asset",
    description="Download a remote asset into downloads/web/ in the actor workspace.",
    input_type=WebDownloadInput,
    output_type=DownloadedFile,
    namespace="web",
    effect="write",
)

WEB_CAPABILITY_SPECS: tuple[AnyCapabilitySpec, ...] = (
    WEB_SEARCH_CAPABILITY_SPEC,
    WEB_READ_CAPABILITY_SPEC,
    WEB_DOWNLOAD_CAPABILITY_SPEC,
)


@dataclass
class WebIntegrationFactory:
    name: str = WEB_INTEGRATION_NAME
    description: str = WEB_INTEGRATION_DESCRIPTION
    config_schema: type[msgspec.Struct] = WebConfig
    source_path_convention: str = WEB_SOURCE_PATH_CONVENTION

    def capability_specs(self) -> list[AnyCapabilitySpec]:
        return list(WEB_CAPABILITY_SPECS)

    @property
    def sdk_spec(self) -> IntegrationSdkSpec:
        return WEB_SDK_SPEC

    async def create(
        self,
        record: IntegrationRecord,
        *,
        gateway: Gateway,
        storage: IntegrationStorage,
    ) -> "WebIntegration":
        _ = gateway
        _ = storage
        config = record.typed_config(WebConfig)
        return WebIntegration(
            client=WebClient.from_config(
                api_key=config.api_key.reveal(),
                tavily_base_url=config.tavily_base_url,
                timeout_s=config.timeout_s,
                user_agent=config.user_agent,
                max_read_bytes=config.max_read_bytes,
                max_read_chars=config.max_read_chars,
                max_download_bytes=config.max_download_bytes,
            )
        )

    def routes(self, integrations: "IntegrationCore") -> list:
        _ = integrations
        return []


@dataclass
class WebIntegration:
    client: WebClient

    def capabilities(self) -> list[AnyCapability]:
        return [
            Capability(spec=WEB_SEARCH_CAPABILITY_SPEC, invoke=self.invoke_search),
            Capability(spec=WEB_READ_CAPABILITY_SPEC, invoke=self.invoke_read),
            Capability(spec=WEB_DOWNLOAD_CAPABILITY_SPEC, invoke=self.invoke_download),
        ]

    async def invoke_search(
        self,
        payload: WebSearchInput,
        context: InvocationContext,
    ) -> WebSearchOutput:
        _ = context
        results: list[SearchResult] = await self.client.search(
            query=payload.query,
            max_results=payload.max_results,
        )
        return WebSearchOutput(results=results)

    async def invoke_read(
        self,
        payload: WebReadInput,
        context: InvocationContext,
    ) -> WebPage:
        _ = context
        return await self.client.read(url=payload.url, max_chars=payload.max_chars)

    async def invoke_download(
        self,
        payload: WebDownloadInput,
        context: InvocationContext,
    ) -> DownloadedFile:
        if not context.workspace_path:
            raise ValueError("web.download requires an actor workspace")
        return await self.client.download(
            url=payload.url,
            workspace_path=Path(context.workspace_path),
            filename=payload.filename,
            max_bytes=payload.max_bytes,
        )

    async def response(
        self,
        target_msg_id: str,
        *,
        path: str = "",
        msg: str = "",
        react: ReactionKind | None = None,
    ) -> None:
        _ = target_msg_id
        _ = path
        _ = msg
        _ = react

    async def close(self) -> None:
        await self.client.close()
