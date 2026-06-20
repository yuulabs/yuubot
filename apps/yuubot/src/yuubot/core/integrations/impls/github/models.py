"""Typed contracts for the built-in GitHub integration."""

from __future__ import annotations

from typing import Annotated, Literal

import msgspec

from yuubot.core.secrets import Secret


class GitHubConfig(msgspec.Struct, forbid_unknown_fields=False):
    client_id: Annotated[
        str,
        msgspec.Meta(
            title="OAuth client ID",
            description="GitHub OAuth app client ID used for browser authorization.",
        ),
    ] = ""
    client_secret: Annotated[
        Secret,
        msgspec.Meta(
            title="OAuth client secret",
            description="GitHub OAuth app client secret used only by the admin backend.",
        ),
    ] = msgspec.field(default_factory=lambda: Secret(""))
    access_token: Annotated[
        Secret,
        msgspec.Meta(
            title="OAuth access token",
            description="GitHub OAuth access token stored after browser authorization.",
        ),
    ] = msgspec.field(default_factory=lambda: Secret(""))
    oauth_state: Annotated[
        Secret,
        msgspec.Meta(
            title="Pending OAuth state",
            description="Temporary CSRF state value for an in-progress GitHub OAuth flow.",
        ),
    ] = msgspec.field(default_factory=lambda: Secret(""))
    oauth_scope: Annotated[
        str,
        msgspec.Meta(
            title="OAuth scope",
            description="Space-delimited GitHub OAuth scopes requested during authorization.",
        ),
    ] = "repo"
    oauth_authorize_url: Annotated[
        str,
        msgspec.Meta(
            title="OAuth authorize URL",
            description="GitHub OAuth authorization endpoint.",
        ),
    ] = "https://github.com/login/oauth/authorize"
    oauth_access_token_url: Annotated[
        str,
        msgspec.Meta(
            title="OAuth access token URL",
            description="GitHub OAuth token exchange endpoint.",
        ),
    ] = "https://github.com/login/oauth/access_token"
    default_owner: Annotated[
        str,
        msgspec.Meta(
            title="Default owner",
            description="Default repository owner used when capability calls omit owner.",
        ),
    ] = ""
    default_repo: Annotated[
        str,
        msgspec.Meta(
            title="Default repository",
            description="Default repository name used when capability calls omit repo.",
        ),
    ] = ""
    base_url: Annotated[
        str,
        msgspec.Meta(
            title="GitHub API base URL",
            description="Base URL for the GitHub REST API.",
        ),
    ] = "https://api.github.com"


IssueState = Literal["open", "closed", "all"]


class IssueListInput(msgspec.Struct, forbid_unknown_fields=False):
    owner: str = ""
    repo: str = ""
    state: IssueState = "open"
    per_page: int = 30


class IssueReadInput(msgspec.Struct, forbid_unknown_fields=False):
    issue_number: int
    owner: str = ""
    repo: str = ""


class IssueCreateInput(msgspec.Struct, forbid_unknown_fields=False):
    title: str
    body: str = ""
    owner: str = ""
    repo: str = ""


class IssueCommentInput(msgspec.Struct, forbid_unknown_fields=False):
    issue_number: int
    body: str
    owner: str = ""
    repo: str = ""


class FileReadInput(msgspec.Struct, forbid_unknown_fields=False):
    path: str
    owner: str = ""
    repo: str = ""
    ref: str = ""


class IssueSummary(msgspec.Struct):
    number: int
    title: str
    state: str
    url: str
    html_url: str
    body: str = ""


class IssueListOutput(msgspec.Struct):
    issues: list[IssueSummary] = msgspec.field(default_factory=list)


class IssueReadOutput(msgspec.Struct):
    issue: IssueSummary


class IssueCreateOutput(msgspec.Struct):
    issue: IssueSummary


class IssueCommentOutput(msgspec.Struct):
    id: int
    url: str
    html_url: str
    body: str


class FileReadOutput(msgspec.Struct):
    path: str
    name: str
    sha: str
    content: str
    encoding: str = "utf-8"


class GitHubIssuePayload(msgspec.Struct, forbid_unknown_fields=False):
    number: int
    title: str
    state: str
    url: str
    html_url: str
    body: str | None = None


class GitHubCommentPayload(msgspec.Struct, forbid_unknown_fields=False):
    id: int
    url: str
    html_url: str
    body: str


class GitHubContentPayload(msgspec.Struct, forbid_unknown_fields=False):
    type: str
    path: str
    name: str
    sha: str
    content: str
    encoding: str


class GitHubOAuthTokenResponse(msgspec.Struct, forbid_unknown_fields=False):
    access_token: str = ""
    token_type: str = ""
    scope: str = ""
    error: str = ""
    error_description: str = ""
