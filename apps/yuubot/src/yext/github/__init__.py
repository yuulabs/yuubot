"""Hand-written GitHub facade for actor Python sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from yuubot.core.facade.protocol import FacadeRpcRequest
from yb import _context
from yb._client import request as _request

IssueState = Literal["open", "closed", "all"]

_DEFAULT_MAX_CHARS = 4000


@dataclass(frozen=True)
class GitHubIssue:
    """Printable issue summary with explicit content access."""

    number: int
    title: str
    state: str
    url: str
    html_url: str
    _body: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "GitHubIssue":
        return cls(
            number=_int_field(payload, "number"),
            title=str(payload["title"]),
            state=str(payload["state"]),
            url=str(payload["url"]),
            html_url=str(payload["html_url"]),
            _body=str(payload.get("body", "")),
        )

    def __str__(self) -> str:
        summary = _brief(self._body)
        return (
            f"#{self.number} {self.title} "
            f"[{self.state}; body_chars={len(self._body)}; {self.html_url}]"
            + (f" - {summary}" if summary else "")
        )

    def body(self, *, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
        return _limit(self._body, max_chars=max_chars)

    def as_dict(self) -> dict[str, object]:
        return {
            "number": self.number,
            "title": self.title,
            "state": self.state,
            "url": self.url,
            "html_url": self.html_url,
            "body_chars": len(self._body),
        }


@dataclass(frozen=True)
class GitHubFile:
    path: str
    name: str
    sha: str
    content: str
    encoding: str = "utf-8"

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, object],
        *,
        max_chars: int,
    ) -> "GitHubFile":
        return cls(
            path=str(payload["path"]),
            name=str(payload["name"]),
            sha=str(payload["sha"]),
            content=_limit(str(payload.get("content", "")), max_chars=max_chars),
            encoding=str(payload.get("encoding", "utf-8")),
        )

    def __str__(self) -> str:
        return (
            f"{self.path} "
            f"[encoding={self.encoding}; content_chars={len(self.content)}; sha={self.sha}]"
        )

    def text(self, *, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
        return _limit(self.content, max_chars=max_chars)

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "name": self.name,
            "sha": self.sha,
            "encoding": self.encoding,
            "content_chars": len(self.content),
        }


@dataclass(frozen=True)
class GitHubRepo:
    owner: str = ""
    name: str = ""
    integration_id: str = ""

    @property
    def issues(self) -> "GitHubIssues":
        return GitHubIssues(
            owner=self.owner,
            repo=self.name,
            integration_id=self.integration_id,
        )

    @property
    def files(self) -> "GitHubFiles":
        return GitHubFiles(
            owner=self.owner,
            repo=self.name,
            integration_id=self.integration_id,
        )


@dataclass(frozen=True)
class GitHubIssues:
    owner: str = ""
    repo: str = ""
    integration_id: str = ""

    async def list_recent(
        self,
        *,
        limit: int = 30,
        state: IssueState = "open",
    ) -> dict[str, GitHubIssue]:
        result = await _invoke(
            "github.issue.list",
            {
                "owner": self.owner,
                "repo": self.repo,
                "state": state,
                "per_page": limit,
            },
            integration_id=self.integration_id,
        )
        issues = result.get("issues", [])
        if not isinstance(issues, list):
            raise TypeError("github.issue.list result must contain an issues list")
        return {
            f"#{issue.number}": issue
            for issue in (
                GitHubIssue.from_payload(_require_object(item)) for item in issues
            )
        }

    async def read(self, number: int, *, max_chars: int = _DEFAULT_MAX_CHARS) -> GitHubIssue:
        result = await _invoke(
            "github.issue.read",
            {
                "owner": self.owner,
                "repo": self.repo,
                "issue_number": number,
            },
            integration_id=self.integration_id,
        )
        payload = _require_object(result.get("issue"))
        issue = GitHubIssue.from_payload(payload)
        return GitHubIssue(
            number=issue.number,
            title=issue.title,
            state=issue.state,
            url=issue.url,
            html_url=issue.html_url,
            _body=issue.body(max_chars=max_chars),
        )


@dataclass(frozen=True)
class GitHubFiles:
    owner: str = ""
    repo: str = ""
    integration_id: str = ""

    async def read(
        self,
        path: str,
        *,
        ref: str = "",
        max_chars: int = 8000,
        mime: str = "text/plain",
    ) -> str | dict[str, object]:
        result = await _invoke(
            "github.file.read",
            {
                "owner": self.owner,
                "repo": self.repo,
                "path": path,
                "ref": ref,
            },
            integration_id=self.integration_id,
        )
        file = GitHubFile.from_payload(result, max_chars=max_chars)
        if mime == "application/json":
            return {**file.as_dict(), "content": file.content}
        return file.text(max_chars=max_chars)


def repo(owner: str = "", name: str = "", /, *, integration_id: str = "") -> GitHubRepo:
    """Return a repository facade.

    Empty owner/name values intentionally flow to the integration so configured
    defaults are honored and missing defaults return a bridge error message.
    Pass ``integration_id`` when more than one GitHub integration is visible.
    """

    return GitHubRepo(owner=owner, name=name, integration_id=integration_id)


async def _invoke(
    capability_id: str,
    payload: dict[str, object],
    *,
    integration_id: str = "",
) -> dict[str, object]:
    actor = _context.actor_context()
    bridge = _context.bridge_context()
    response = await _request(
        FacadeRpcRequest(
            token=bridge.token,
            actor_id=actor.actor_id,
            integration_id=integration_id,
            agent_name=actor.agent_name,
            session_id=actor.session_id,
            mailbox_id=actor.mailbox_id,
            capability_id=capability_id,
            payload=payload,
        )
    )
    result = response.result
    if not isinstance(result, dict):
        raise TypeError("github facade result must be a JSON object")
    return result


def _require_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("github facade expected a JSON object")
    return cast(dict[str, object], value)


def _int_field(payload: dict[str, object], key: str) -> int:
    value = payload[key]
    if not isinstance(value, int):
        raise TypeError(f"github facade expected integer field {key!r}")
    return value


def _brief(value: str) -> str:
    return _limit(" ".join(value.split()), max_chars=160)


def _limit(value: str, *, max_chars: int) -> str:
    if max_chars < 0:
        raise ValueError("max_chars must be non-negative")
    if len(value) <= max_chars:
        return value
    return value[:max_chars]


__all__ = [
    "GitHubFile",
    "GitHubFiles",
    "GitHubIssue",
    "GitHubIssues",
    "GitHubRepo",
    "repo",
]
