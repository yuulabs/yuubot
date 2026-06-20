"""Thin async GitHub REST client for integration capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import httpx
import msgspec

from yuubot.core.integrations.impls.github.models import (
    GitHubCommentPayload,
    GitHubContentPayload,
    GitHubIssuePayload,
    IssueState,
)


@dataclass
class GitHubClient:
    http: httpx.AsyncClient

    @classmethod
    def from_config(cls, *, token: str, base_url: str) -> "GitHubClient":
        return cls(
            http=httpx.AsyncClient(
                base_url=base_url.rstrip("/"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        )

    async def list_issues(
        self,
        *,
        owner: str,
        repo: str,
        state: IssueState,
        per_page: int,
    ) -> list[GitHubIssuePayload]:
        response = await self.http.get(
            f"/repos/{owner}/{repo}/issues",
            params={"state": state, "per_page": per_page},
        )
        response.raise_for_status()
        data = response.json()
        return msgspec.convert(data, type=list[GitHubIssuePayload], strict=False)

    async def read_issue(
        self,
        *,
        owner: str,
        repo: str,
        issue_number: int,
    ) -> GitHubIssuePayload:
        response = await self.http.get(f"/repos/{owner}/{repo}/issues/{issue_number}")
        response.raise_for_status()
        return msgspec.convert(response.json(), type=GitHubIssuePayload, strict=False)

    async def create_issue(
        self,
        *,
        owner: str,
        repo: str,
        title: str,
        body: str,
    ) -> GitHubIssuePayload:
        response = await self.http.post(
            f"/repos/{owner}/{repo}/issues",
            json={"title": title, "body": body},
        )
        response.raise_for_status()
        return msgspec.convert(response.json(), type=GitHubIssuePayload, strict=False)

    async def create_comment(
        self,
        *,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
    ) -> GitHubCommentPayload:
        response = await self.http.post(
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            json={"body": body},
        )
        response.raise_for_status()
        return msgspec.convert(response.json(), type=GitHubCommentPayload, strict=False)

    async def read_file(
        self,
        *,
        owner: str,
        repo: str,
        path: str,
        ref: str,
    ) -> GitHubContentPayload:
        params: dict[str, str] = {}
        if ref:
            params["ref"] = ref
        response = await self.http.get(
            f"/repos/{owner}/{repo}/contents/{path}",
            params=params,
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            raise ValueError("github.file.read does not support directory responses")
        return msgspec.convert(
            cast(dict[str, object], data),
            type=GitHubContentPayload,
            strict=False,
        )

    async def close(self) -> None:
        await self.http.aclose()
