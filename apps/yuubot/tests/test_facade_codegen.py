"""Hand-written GitHub facade tests."""

from __future__ import annotations

import sys
import types
from typing import cast

import msgspec

from yuubot.core.facade import IntegrationInvokeBridge
from yuubot.core.facade.context import FACADE_CONTEXT_MODULE
from yuubot.core.integrations.core import IntegrationCore


async def test_github_facade_reaches_integration_bridge() -> None:
    requests: list[tuple[str, str, dict[str, object]]] = []
    bridge = IntegrationInvokeBridge(cast(IntegrationCore, _FakeIntegrationCore(requests)))
    await bridge.start()
    endpoint = bridge.endpoint
    context_mod = types.ModuleType(FACADE_CONTEXT_MODULE)
    context_mod.__dict__.update(
        {
            "ACTOR_ID": "actor-1",
            "AGENT_NAME": "agent-1",
            "SESSION_ID": "session-1",
            "MAILBOX_ID": "actor:actor-1",
            "HOST": endpoint.host,
            "PORT": endpoint.port,
            "TOKEN": endpoint.token,
            "TIMEOUT_S": 5.0,
        }
    )
    sys.modules[FACADE_CONTEXT_MODULE] = context_mod
    try:
        import yext.github

        repo = yext.github.repo(
            "yuulabs",
            "yuubot",
            integration_id="github-secondary",
        )
        issues = await repo.issues.list_recent(limit=5)
        issue = issues["#11111"]
        content = await repo.files.read(
            "README.md",
            ref="main",
            max_chars=8,
        )
    finally:
        sys.modules.pop(FACADE_CONTEXT_MODULE, None)
        await bridge.stop()

    assert requests == [
        (
            "github-secondary",
            "github.issue.list",
            {
                "owner": "yuulabs",
                "repo": "yuubot",
                "state": "open",
                "per_page": 5,
            },
        ),
        (
            "github-secondary",
            "github.file.read",
            {
                "owner": "yuulabs",
                "repo": "yuubot",
                "path": "README.md",
                "ref": "main",
            },
        ),
    ]
    assert str(issue) == (
        "#11111 Facade surface [open; body_chars=19; "
        "https://github.test/yuulabs/yuubot/issues/11111] - abc def ghi jkl mno"
    )
    assert issue.body(max_chars=7) == "abc def"
    assert content == "README c"


async def test_github_facade_missing_repo_returns_bridge_error() -> None:
    bridge = IntegrationInvokeBridge(cast(IntegrationCore, _FakeIntegrationCore([])))
    await bridge.start()
    endpoint = bridge.endpoint
    context_mod = types.ModuleType(FACADE_CONTEXT_MODULE)
    context_mod.__dict__.update(
        {
            "ACTOR_ID": "actor-1",
            "AGENT_NAME": "agent-1",
            "SESSION_ID": "session-1",
            "MAILBOX_ID": "actor:actor-1",
            "HOST": endpoint.host,
            "PORT": endpoint.port,
            "TOKEN": endpoint.token,
            "TIMEOUT_S": 5.0,
        }
    )
    sys.modules[FACADE_CONTEXT_MODULE] = context_mod
    try:
        import pytest
        import yext.github

        with pytest.raises(RuntimeError, match="owner and repo are required"):
            await yext.github.repo().issues.list_recent()
    finally:
        sys.modules.pop(FACADE_CONTEXT_MODULE, None)
        await bridge.stop()


class _FakeIntegrationCore:
    def __init__(self, requests: list[tuple[str, str, dict[str, object]]]) -> None:
        self.requests = requests

    async def invoke(
        self,
        *,
        actor_id: str,
        capability_id: str,
        payload: dict[str, object],
        context: object,
        integration_id: str = "",
    ) -> object:
        _ = actor_id, context
        if not payload["owner"] or not payload["repo"]:
            raise ValueError("owner and repo are required for GitHub capability calls")
        self.requests.append((integration_id, capability_id, dict(payload)))
        if capability_id == "github.issue.list":
            return _StructResult(
                issues=[
                    {
                        "number": 11111,
                        "title": "Facade surface",
                        "state": "open",
                        "url": "https://api.github.test/repos/yuulabs/yuubot/issues/11111",
                        "html_url": "https://github.test/yuulabs/yuubot/issues/11111",
                        "body": "abc def ghi jkl mno",
                    }
                ]
            )
        if capability_id == "github.file.read":
            return _FileResult(
                path="README.md",
                name="README.md",
                sha="abc123",
                content="README content",
                encoding="utf-8",
            )
        raise LookupError(capability_id)


class _StructResult(msgspec.Struct):
    issues: list[dict[str, object]]


class _FileResult(msgspec.Struct):
    path: str
    name: str
    sha: str
    content: str
    encoding: str
