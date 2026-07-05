"""GitHub facade.

Use repo(owner="", name="") to access issues and repository files with the configured PAT.
"""

from __future__ import annotations

import os
import urllib.parse
import base64
from dataclasses import dataclass
from typing import cast

import httpx
import msgspec


class IssueSummary(msgspec.Struct, frozen=True, kw_only=True):
    number: int
    title: str
    state: str
    url: str


@dataclass(frozen=True, slots=True)
class GitHubRepo:
    owner: str
    name: str
    base_url: str
    token: str

    @property
    def issues(self) -> "Issues":
        return Issues(self)

    @property
    def files(self) -> "Files":
        return Files(self)

    async def request_json(self, path: str) -> object:
        async with httpx.AsyncClient(
            base_url=self.base_url.rstrip("/"),
            headers={
                "accept": "application/vnd.github+json",
                "authorization": f"Bearer {self.token}",
                "x-github-api-version": "2022-11-28",
            },
            timeout=30,
        ) as client:
            response = await client.get(path)
            response.raise_for_status()
            return cast(object, response.json())


@dataclass(frozen=True, slots=True)
class Issues:
    repo: GitHubRepo

    async def list_recent(self, limit: int = 30, state: str = "open") -> list[IssueSummary]:
        path = f"/repos/{self.repo.owner}/{self.repo.name}/issues?state={urllib.parse.quote(state)}&per_page={limit}"
        data = _list(await self.repo.request_json(path))
        return [IssueSummary(number=_int(item.get("number")), title=_str(item.get("title")), state=_str(item.get("state")), url=_str(item.get("html_url"))) for item in data]

    async def read(self, number: int, *, max_chars: int = 4000) -> str:
        path = f"/repos/{self.repo.owner}/{self.repo.name}/issues/{number}"
        item = _dict(await self.repo.request_json(path))
        return f"#{_int(item.get('number'))} {_str(item.get('title'))}\n\n{_str(item.get('body'))}"[:max_chars]


@dataclass(frozen=True, slots=True)
class Files:
    repo: GitHubRepo

    async def read(self, path: str, *, ref: str = "", max_chars: int = 8000, mime: str = "text/plain") -> str:
        del mime
        query = f"?ref={urllib.parse.quote(ref)}" if ref else ""
        url_path = f"/repos/{self.repo.owner}/{self.repo.name}/contents/{urllib.parse.quote(path)}{query}"
        item = _dict(await self.repo.request_json(url_path))
        return base64.b64decode(_str(item.get("content"))).decode("utf-8", errors="replace")[:max_chars]


def repo(owner: str = "", name: str = "", *, integration_id: str = "") -> GitHubRepo:
    del integration_id
    token = os.getenv("GITHUB_TOKEN") or os.getenv("YEXT_GITHUB_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required for yext.github")
    resolved_owner = owner or os.getenv("YEXT_GITHUB_DEFAULT_OWNER", "")
    resolved_name = name or os.getenv("YEXT_GITHUB_DEFAULT_REPO", "")
    if not resolved_owner or not resolved_name:
        raise ValueError("owner/name are required when defaults are not configured")
    return GitHubRepo(
        owner=resolved_owner,
        name=resolved_name,
        base_url=os.getenv("YEXT_GITHUB_BASE_URL", "https://api.github.com"),
        token=token,
    )


def _dict(value: object) -> dict[str, object]:
    return cast(dict[str, object], value if isinstance(value, dict) else {})


def _list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [cast(dict[str, object], item) for item in value if isinstance(item, dict)]


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0
