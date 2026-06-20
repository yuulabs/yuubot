"""Tests for the built-in GitHub integration."""

from __future__ import annotations

import base64
from collections.abc import Callable
from pathlib import Path
from typing import cast

import httpx
import pytest

from yuubot.core.gateway import Gateway
from yuubot.core.integrations import IntegrationCore
from yuubot.core.integrations.context import InvocationContext
from yuubot.core.integrations.contracts import LocalIntegrationStorage
from yuubot.core.integrations.impls.github import (
    GITHUB_FILE_READ_CAPABILITY_ID,
    GITHUB_ISSUE_COMMENT_CAPABILITY_ID,
    GITHUB_ISSUE_CREATE_CAPABILITY_ID,
    GITHUB_ISSUE_LIST_CAPABILITY_ID,
    GITHUB_ISSUE_READ_CAPABILITY_ID,
    GitHubIntegrationFactory,
)
from yuubot.core.integrations.impls.github.client import GitHubClient
from yuubot.core.integrations.impls.github.integration import GitHubIntegration
from yuubot.core.integrations.impls.github.models import (
    FileReadInput,
    IssueCommentInput,
    IssueCreateInput,
    IssueListInput,
    IssueReadInput,
)
from yuubot.core.secrets import Secret
from yuubot.core.routing import RouteBindings
from yuubot.resources.records import IntegrationRecord


def _issue(number: int, title: str, *, body: str | None = None) -> dict[str, object]:
    return {
        "number": number,
        "title": title,
        "state": "open",
        "url": f"https://api.github.test/repos/owner/repo/issues/{number}",
        "html_url": f"https://github.test/owner/repo/issues/{number}",
        "body": body,
    }


async def test_factory_declares_capabilities_and_no_routes() -> None:
    factory = GitHubIntegrationFactory()

    spec_ids = {spec.id for spec in factory.capability_specs()}

    assert spec_ids == {
        GITHUB_ISSUE_LIST_CAPABILITY_ID,
        GITHUB_ISSUE_READ_CAPABILITY_ID,
        GITHUB_ISSUE_CREATE_CAPABILITY_ID,
        GITHUB_ISSUE_COMMENT_CAPABILITY_ID,
        GITHUB_FILE_READ_CAPABILITY_ID,
    }
    specs = {spec.id: spec for spec in factory.capability_specs()}
    assert specs[GITHUB_ISSUE_LIST_CAPABILITY_ID].effect == "read"
    assert specs[GITHUB_ISSUE_READ_CAPABILITY_ID].effect == "read"
    assert specs[GITHUB_FILE_READ_CAPABILITY_ID].effect == "read"
    assert specs[GITHUB_ISSUE_CREATE_CAPABILITY_ID].effect == "write"
    assert specs[GITHUB_ISSUE_COMMENT_CAPABILITY_ID].effect == "write"
    assert factory.routes(_fake_integration_core()) == []


async def test_factory_creates_client_with_revealed_secret(
    tmp_path: Path,
) -> None:
    factory = GitHubIntegrationFactory()
    record = IntegrationRecord(
        id="github-main",
        name="github",
        config={
            "access_token": Secret("test-token"),
            "default_owner": "yuulabs",
            "default_repo": "yuubot",
            "base_url": "https://api.github.test/",
        },
    )

    instance = await factory.create(
        record,
        gateway=Gateway(RouteBindings(rules=[])),
        storage=LocalIntegrationStorage(data_dir=tmp_path),
    )
    await instance.close()

    assert instance.default_owner == "yuulabs"
    assert instance.default_repo == "yuubot"
    assert instance.client.http.headers["Authorization"] == "Bearer test-token"
    assert instance.client.http.headers["Accept"] == "application/vnd.github+json"
    assert (
        instance.client.http.headers["X-GitHub-Api-Version"]
        == "2022-11-28"
    )


async def test_factory_rejects_unconnected_integration(tmp_path: Path) -> None:
    factory = GitHubIntegrationFactory()
    record = IntegrationRecord(
        id="github-main",
        name="github",
        config={},
    )

    with pytest.raises(ValueError, match="not connected"):
        await factory.create(
            record,
            gateway=Gateway(RouteBindings(rules=[])),
            storage=LocalIntegrationStorage(data_dir=tmp_path),
        )


async def test_integration_invokes_issue_paths_with_defaults() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/repos/yuulabs/yuubot/issues":
            assert request.url.params["state"] == "open"
            assert request.url.params["per_page"] == "25"
            return httpx.Response(200, json=[_issue(1, "First", body=None)])
        if request.method == "GET" and request.url.path == "/repos/acme/demo/issues/2":
            return httpx.Response(200, json=_issue(2, "Second", body="details"))
        if request.method == "POST" and request.url.path == "/repos/yuulabs/yuubot/issues":
            assert request.read() == b'{"title":"New issue","body":"body"}'
            return httpx.Response(201, json=_issue(3, "New issue", body="body"))
        if (
            request.method == "POST"
            and request.url.path == "/repos/yuulabs/yuubot/issues/3/comments"
        ):
            assert request.read() == b'{"body":"comment"}'
            return httpx.Response(
                201,
                json={
                    "id": 10,
                    "url": "https://api.github.test/comments/10",
                    "html_url": "https://github.test/comments/10",
                    "body": "comment",
                },
            )
        return httpx.Response(404)

    integration = _integration(handler)
    context = InvocationContext(actor_id="actor")

    listed = await integration.invoke_list_issues(
        IssueListInput(per_page=25),
        context,
    )
    read = await integration.invoke_read_issue(
        IssueReadInput(owner="acme", repo="demo", issue_number=2),
        context,
    )
    created = await integration.invoke_create_issue(
        IssueCreateInput(title="New issue", body="body"),
        context,
    )
    comment = await integration.invoke_comment_issue(
        IssueCommentInput(issue_number=3, body="comment"),
        context,
    )
    await integration.response("msg-1", path="ignored", msg="ignored", react="ok")
    await integration.close()

    assert listed.issues[0].number == 1
    assert listed.issues[0].body == ""
    assert read.issue.title == "Second"
    assert read.issue.body == "details"
    assert created.issue.number == 3
    assert comment.id == 10
    assert [request.headers["Authorization"] for request in requests] == [
        "Bearer test-token",
        "Bearer test-token",
        "Bearer test-token",
        "Bearer test-token",
    ]


async def test_file_read_decodes_base64_content() -> None:
    encoded = base64.b64encode(b"hello\nworld\n").decode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/repos/yuulabs/yuubot/contents/README.md"
        assert request.url.params["ref"] == "main"
        return httpx.Response(
            200,
            json={
                "type": "file",
                "path": "README.md",
                "name": "README.md",
                "sha": "abc123",
                "content": f"{encoded[:8]}\n{encoded[8:]}",
                "encoding": "base64",
            },
        )

    integration = _integration(handler)

    result = await integration.invoke_read_file(
        FileReadInput(path="README.md", ref="main"),
        InvocationContext(actor_id="actor"),
    )
    await integration.close()

    assert result.path == "README.md"
    assert result.name == "README.md"
    assert result.sha == "abc123"
    assert result.content == "hello\nworld\n"
    assert result.encoding == "utf-8"


async def test_file_read_rejects_directory_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(200, json=[])

    integration = _integration(handler)

    with pytest.raises(ValueError, match="directory responses"):
        await integration.invoke_read_file(
            FileReadInput(path="docs"),
            InvocationContext(actor_id="actor"),
        )
    await integration.close()


async def test_owner_and_repo_are_required_without_defaults() -> None:
    integration = _integration(lambda request: httpx.Response(500), owner="", repo="")

    with pytest.raises(ValueError, match="owner and repo are required"):
        await integration.invoke_list_issues(
            IssueListInput(),
            InvocationContext(actor_id="actor"),
        )
    await integration.close()


async def test_http_status_errors_propagate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"}, request=request)

    integration = _integration(handler)

    with pytest.raises(httpx.HTTPStatusError):
        await integration.invoke_read_issue(
            IssueReadInput(issue_number=99),
            InvocationContext(actor_id="actor"),
        )
    await integration.close()


def _integration(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    owner: str = "yuulabs",
    repo: str = "yuubot",
) -> GitHubIntegration:
    transport = httpx.MockTransport(handler)
    client = GitHubClient(
        http=httpx.AsyncClient(
            base_url="https://api.github.test",
            headers={
                "Authorization": "Bearer test-token",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            transport=transport,
        )
    )
    return GitHubIntegration(client=client, default_owner=owner, default_repo=repo)


def _fake_integration_core() -> IntegrationCore:
    return cast(IntegrationCore, object())
