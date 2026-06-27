"""Built-in GitHub integration."""

from __future__ import annotations

import base64
from dataclasses import dataclass
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
from yuubot.core.integrations.impls.github.client import GitHubClient
from yuubot.core.integrations.impls.github.models import (
    FileReadInput,
    FileReadOutput,
    GitHubConfig,
    GitHubContentPayload,
    GitHubIssuePayload,
    IssueCommentInput,
    IssueCommentOutput,
    IssueCreateInput,
    IssueCreateOutput,
    IssueListInput,
    IssueListOutput,
    IssueReadInput,
    IssueReadOutput,
    IssueSummary,
)
from yuubot.resources.records import IntegrationRecord

if TYPE_CHECKING:
    from yuubot.core.integrations.core import IntegrationCore

GITHUB_INTEGRATION_NAME = "github"
GITHUB_ISSUE_LIST_CAPABILITY_ID = "github.issue.list"
GITHUB_ISSUE_READ_CAPABILITY_ID = "github.issue.read"
GITHUB_ISSUE_CREATE_CAPABILITY_ID = "github.issue.create"
GITHUB_ISSUE_COMMENT_CAPABILITY_ID = "github.issue.comment"
GITHUB_FILE_READ_CAPABILITY_ID = "github.file.read"

GITHUB_INTEGRATION_DESCRIPTION = (
    "GitHub REST integration for reading repository issues and files, and "
    "creating issues or comments."
)

GITHUB_SOURCE_PATH_CONVENTION = (
    "GitHub is exposed only as agent-callable capabilities in this phase. "
    "It does not emit inbound messages and has no source path convention."
)

# SDK surface the GitHub integration exposes to agent facades + the system
# prompt ``# Integration SDKs`` section (design §2.7.1). The hand-written
# ``yext.github`` facade module is the callable surface; this prose documents
# its API, capabilities, and failure guidance so the agent knows how to use it
# without the system prompt mechanically expanding every function schema.
_GITHUB_SDK_PROMPT_SUMMARY = """\
- Surface: execute_python
- Python facade: yext.github hand-written API.
  Do NOT derive import paths mechanically from capability ids.

Examples:
```python
import yext.github

repo = yext.github.repo("OWNER", "REPO")
issues = await repo.issues.list_recent(limit=5)
issue = await repo.issues.read(123, body_max_chars=4000)
content = await repo.files.read("README.md", ref="main", max_chars=8000)
```

Capabilities:
- github.issue.list     — read.  Inputs: owner?, repo?, state?, per_page?.
- github.issue.read     — read.  Inputs: number, body_max_chars?.
- github.issue.create   — write. Inputs: owner?, repo?, title, body?.
- github.issue.comment  — write. Inputs: number, body.
- github.file.read      — read.  Inputs: path, ref?, max_chars?.

Failure guidance:
- If owner/repo are missing, ask the user or use configured defaults if documented.
- If GitHub returns not found/private/scope/rate-limit, summarize the exact failure; do not retry the same call.
- If output is too large, narrow the request or ask before retry."""

GITHUB_SDK_SPEC = IntegrationSdkSpec(
    import_paths=("yext.github",),
    prompt_summary=_GITHUB_SDK_PROMPT_SUMMARY,
    doc_modules=("yext.github",),
)

GITHUB_ISSUE_LIST_CAPABILITY_SPEC = CapabilitySpec[IssueListInput, IssueListOutput](
    id=GITHUB_ISSUE_LIST_CAPABILITY_ID,
    name="List GitHub issues",
    description="List issues for a GitHub repository.",
    input_type=IssueListInput,
    output_type=IssueListOutput,
    namespace="github",
    effect="read",
)

GITHUB_ISSUE_READ_CAPABILITY_SPEC = CapabilitySpec[IssueReadInput, IssueReadOutput](
    id=GITHUB_ISSUE_READ_CAPABILITY_ID,
    name="Read GitHub issue",
    description="Read a single GitHub issue.",
    input_type=IssueReadInput,
    output_type=IssueReadOutput,
    namespace="github",
    effect="read",
)

GITHUB_ISSUE_CREATE_CAPABILITY_SPEC = CapabilitySpec[
    IssueCreateInput,
    IssueCreateOutput,
](
    id=GITHUB_ISSUE_CREATE_CAPABILITY_ID,
    name="Create GitHub issue",
    description="Create an issue in a GitHub repository.",
    input_type=IssueCreateInput,
    output_type=IssueCreateOutput,
    namespace="github",
    effect="write",
)

GITHUB_ISSUE_COMMENT_CAPABILITY_SPEC = CapabilitySpec[
    IssueCommentInput,
    IssueCommentOutput,
](
    id=GITHUB_ISSUE_COMMENT_CAPABILITY_ID,
    name="Comment on GitHub issue",
    description="Add a comment to a GitHub issue.",
    input_type=IssueCommentInput,
    output_type=IssueCommentOutput,
    namespace="github",
    effect="write",
)

GITHUB_FILE_READ_CAPABILITY_SPEC = CapabilitySpec[FileReadInput, FileReadOutput](
    id=GITHUB_FILE_READ_CAPABILITY_ID,
    name="Read GitHub file",
    description="Read a UTF-8 text file from a GitHub repository.",
    input_type=FileReadInput,
    output_type=FileReadOutput,
    namespace="github",
    effect="read",
)

GITHUB_CAPABILITY_SPECS: tuple[AnyCapabilitySpec, ...] = (
    GITHUB_ISSUE_LIST_CAPABILITY_SPEC,
    GITHUB_ISSUE_READ_CAPABILITY_SPEC,
    GITHUB_ISSUE_CREATE_CAPABILITY_SPEC,
    GITHUB_ISSUE_COMMENT_CAPABILITY_SPEC,
    GITHUB_FILE_READ_CAPABILITY_SPEC,
)


@dataclass
class GitHubIntegrationFactory:
    name: str = GITHUB_INTEGRATION_NAME
    description: str = GITHUB_INTEGRATION_DESCRIPTION
    config_schema: type[msgspec.Struct] = GitHubConfig
    source_path_convention: str = GITHUB_SOURCE_PATH_CONVENTION

    def capability_specs(self) -> list[AnyCapabilitySpec]:
        return list(GITHUB_CAPABILITY_SPECS)

    @property
    def sdk_spec(self) -> IntegrationSdkSpec:
        return GITHUB_SDK_SPEC

    async def create(
        self,
        record: IntegrationRecord,
        *,
        gateway: Gateway,
        storage: IntegrationStorage,
    ) -> "GitHubIntegration":
        _ = gateway
        _ = storage
        config = record.typed_config(GitHubConfig)
        access_token = config.access_token.reveal()
        if not access_token:
            raise ValueError("GitHub integration is not connected")
        return GitHubIntegration(
            client=GitHubClient.from_config(
                token=access_token,
                base_url=config.base_url,
            ),
            default_owner=config.default_owner,
            default_repo=config.default_repo,
        )

    def routes(self, integrations: "IntegrationCore") -> list:
        _ = integrations
        return []


@dataclass
class GitHubIntegration:
    client: GitHubClient
    default_owner: str = ""
    default_repo: str = ""

    def capabilities(self) -> list[AnyCapability]:
        return [
            Capability(spec=GITHUB_ISSUE_LIST_CAPABILITY_SPEC, invoke=self.invoke_list_issues),
            Capability(spec=GITHUB_ISSUE_READ_CAPABILITY_SPEC, invoke=self.invoke_read_issue),
            Capability(
                spec=GITHUB_ISSUE_CREATE_CAPABILITY_SPEC,
                invoke=self.invoke_create_issue,
            ),
            Capability(
                spec=GITHUB_ISSUE_COMMENT_CAPABILITY_SPEC,
                invoke=self.invoke_comment_issue,
            ),
            Capability(spec=GITHUB_FILE_READ_CAPABILITY_SPEC, invoke=self.invoke_read_file),
        ]

    async def invoke_list_issues(
        self,
        payload: IssueListInput,
        context: InvocationContext,
    ) -> IssueListOutput:
        _ = context
        owner, repo = self._resolve_repo(payload.owner, payload.repo)
        issues = await self.client.list_issues(
            owner=owner,
            repo=repo,
            state=payload.state,
            per_page=payload.per_page,
        )
        return IssueListOutput(issues=[_issue_summary(issue) for issue in issues])

    async def invoke_read_issue(
        self,
        payload: IssueReadInput,
        context: InvocationContext,
    ) -> IssueReadOutput:
        _ = context
        owner, repo = self._resolve_repo(payload.owner, payload.repo)
        issue = await self.client.read_issue(
            owner=owner,
            repo=repo,
            issue_number=payload.issue_number,
        )
        return IssueReadOutput(issue=_issue_summary(issue))

    async def invoke_create_issue(
        self,
        payload: IssueCreateInput,
        context: InvocationContext,
    ) -> IssueCreateOutput:
        _ = context
        owner, repo = self._resolve_repo(payload.owner, payload.repo)
        issue = await self.client.create_issue(
            owner=owner,
            repo=repo,
            title=payload.title,
            body=payload.body,
        )
        return IssueCreateOutput(issue=_issue_summary(issue))

    async def invoke_comment_issue(
        self,
        payload: IssueCommentInput,
        context: InvocationContext,
    ) -> IssueCommentOutput:
        _ = context
        owner, repo = self._resolve_repo(payload.owner, payload.repo)
        comment = await self.client.create_comment(
            owner=owner,
            repo=repo,
            issue_number=payload.issue_number,
            body=payload.body,
        )
        return IssueCommentOutput(
            id=comment.id,
            url=comment.url,
            html_url=comment.html_url,
            body=comment.body,
        )

    async def invoke_read_file(
        self,
        payload: FileReadInput,
        context: InvocationContext,
    ) -> FileReadOutput:
        _ = context
        owner, repo = self._resolve_repo(payload.owner, payload.repo)
        file_payload = await self.client.read_file(
            owner=owner,
            repo=repo,
            path=payload.path,
            ref=payload.ref,
        )
        return _file_read_output(file_payload)

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

    def _resolve_repo(self, owner: str, repo: str) -> tuple[str, str]:
        resolved_owner = owner or self.default_owner
        resolved_repo = repo or self.default_repo
        if not resolved_owner or not resolved_repo:
            raise ValueError("owner and repo are required for GitHub capability calls")
        return resolved_owner, resolved_repo


def _issue_summary(payload: GitHubIssuePayload) -> IssueSummary:
    return IssueSummary(
        number=payload.number,
        title=payload.title,
        state=payload.state,
        url=payload.url,
        html_url=payload.html_url,
        body=payload.body or "",
    )


def _file_read_output(payload: GitHubContentPayload) -> FileReadOutput:
    if payload.type != "file":
        raise ValueError("github.file.read only supports file responses")
    if payload.encoding != "base64":
        raise ValueError(f"unsupported GitHub content encoding {payload.encoding!r}")
    normalized = "".join(payload.content.splitlines())
    content = base64.b64decode(normalized.encode(), validate=True).decode()
    return FileReadOutput(
        path=payload.path,
        name=payload.name,
        sha=payload.sha,
        content=content,
    )
