import inspect
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Literal

from ..domain.messages import ContentItem, InputMessage

_RUNTIME_FACADE_PACKAGES = ("yb.tasks", "yb.tasks.cron")

SessionMode = Literal["conversation", "actor"]
REAL_TIME_CONTEXT_MARKER = "[yuubot-real-time-context]"
_REAL_TIME_CONTEXT_SEPARATOR = "\n---\n"


def developer_prompt(persona: str, workspace: Path, package_paths: list[str], *, has_python: bool) -> str:
    sections = [
        "# Persona\n" + (persona.strip() or "You are a yuubot actor."),
        "# System Instructions\n" + _system_instructions(has_python),
        "# Workspace Instructions\n" + _workspace_instructions(workspace),
    ]
    if has_python:
        sections.append("# Tool Suggestions\n" + _tool_suggestions())
    integration_paths = list(package_paths)
    if has_python:
        integration_paths.extend(_RUNTIME_FACADE_PACKAGES)
    integration_docs = _integration_docs(integration_paths)
    if integration_docs:
        sections.append("# Integration SDKs\n" + integration_docs)
    sections.append("# Skills\n" + _skills(workspace))
    sections.append("# AGENTS.md Context\n" + _agents_context(workspace))
    sections.append("# Real-Time Data\n" + _real_time_data())
    return "\n\n".join(sections)


def _system_instructions(has_python: bool) -> str:
    lines = [
        "Session mode semantics are documented in Real-Time Data. Context may be lost between turns; rely on persisted conversation history and workspace files when you need durable memory.",
        "Do not expose secrets, raw integration credentials, or daemon implementation details to users.",
    ]
    if has_python:
        lines.append("execute_python is reset after each user turn. A developer notice will be added when a previous Python session is no longer available.")
    return "\n".join(lines)


def _workspace_instructions(workspace: Path) -> str:
    return "\n".join(
        [
            f"Workspace path: {workspace}",
            "- `artifacts/`: user-visible outputs.",
            "- `uploads/`: uploaded files grouped by MIME type.",
            "- `projects/`: actor-managed project files.",
            "- `notes/`: durable actor notes.",
            "- `scripts/`: helper scripts.",
            "- `.agents/skills/`: skill files you may inspect with the read tool.",
        ]
    )


def _tool_suggestions() -> str:
    return "\n".join(
        [
            "Prefer execute_python for multi-step local work, data shaping, and integration facade calls.",
            "execute_python runs an IPython interactive session with native top-level await. Use it like a notebook cell.",
            "Examples: `await yext.web.search(...)`, `await yext.web.read(...)`, `yext.github.repo().issues.list_recent(...)`, and `yb.office.pdf.to_markdown(...)`.",
            "For long-running shell work, use `await yb.tasks.submit(name, shell, intro)` instead of blocking shell inside execute_python.",
            "submit is fire-and-forget: it registers the task with Runtime and returns a Task handle; execution continues after the tool call ends.",
            "When the task finishes, yuubot appends a developer message and automatically continues this conversation.",
            "Use `await yb.tasks.find(...)`, `await yb.tasks.list_tasks(...)`, and `await task.output()` / `await task.cancel()` for query and control.",
            "Do not call daemon HTTP endpoints such as `/api/tasks`, `/api/inbound`, or admin/public APIs directly; use the yb.tasks facade.",
            "For durable schedules, use `await yb.tasks.cron.add(...)` with an explicit IANA timezone. One-shot `at` accepts a local ISO datetime or a short relative delay such as `+1m`.",
            "For daily or standalone scheduled actor work, use cron action `{\"kind\":\"actor_message\",\"text\":\"...\"}`; it enters the actor's default inbound loop as a user message.",
            "For scheduled results that must continue this exact conversation, use cron action `{\"kind\":\"conversation_callback\",\"text\":\"...\"}`; yuubot appends it as a developer notice and continues the owner conversation.",
            "Use `await yb.tasks.cron.list_jobs(...)`, `find(...)`, `pause(...)`, `resume(...)`, and `delete(...)` to manage cron jobs. Do not call `/api/cron-jobs` directly.",
            "For interactive admin pages, write HTML/CSS/JS under the workspace (for example `projects/.../form.html`).",
            "When an admin opens the page in the management UI, page JavaScript may call admin KV and inbound endpoints with AdminAuth:",
            "- `GET` / `PUT` / `DELETE` `/api/actors/{actor_id}/kv/{key}` (`{key}` is URL-encoded; supports `ETag` / `If-Match`)",
            "- `POST` `/api/actors/{actor_id}/inbound` (`text` plus optional `conversation_id`)",
            "Recommended submit flow: persist draft state to KV, then POST inbound with structured JSON `text` containing `submitted_at`, `source_page`, `purpose` or `context`, optional `kv_key`, and `payload`.",
            "Do not loopback-call admin HTTP from execute_python; dynamic pages are browser-driven.",
            "After `uv add` or `uv remove`, call the `restart_kernel` tool before expecting new imports in execute_python.",
        ]
    )


def _integration_docs(package_paths: list[str]) -> str:
    parts: list[str] = []
    for package_path in package_paths:
        doc = inspect.getdoc(import_module(package_path)) or ""
        if doc:
            parts.append(f"{package_path}:\n{doc}")
    return "\n\n".join(parts)


def _skills(workspace: Path) -> str:
    skills_dir = workspace / ".agents" / "skills"
    entries: list[str] = []
    for skill in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill.read_text(encoding="utf-8")
        entries.append(f"- {skill.parent.name}: {_skill_description(text)}")
    if not entries:
        return "No workspace skills are currently installed."
    return "The following skills can be inspected with the read tool:\n" + "\n".join(entries)


def _skill_description(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return "No description provided."


def _agents_context(workspace: Path) -> str:
    agents = workspace / "AGENTS.md"
    if not agents.exists():
        return "No AGENTS.md is present."
    return agents.read_text(encoding="utf-8")


def _real_time_data() -> str:
    tz = datetime.now().astimezone().tzname() or "local"
    return "\n".join(
        [
            "platform: local",
            f"timezone: {tz}",
            "",
            "## Session modes",
            "Conversation (User): The user message is from a real person in this chat. Reply in this conversation; the user sees your responses here.",
            "Actor: The user message may come from any source (webhook, schedule, inbound API, etc.). For each outbound action, decide whether it belongs in this Conversation (visible in this thread) or to Actor (your future self: workspace notes, cron actor_message, inbound without binding to this conversation, KV, and similar durable channels).",
            "",
            "Per-turn `mode` and `now` are appended to each incoming user message; do not expect them in this section.",
        ]
    )


def real_time_turn_context(*, mode: SessionMode) -> str:
    now = datetime.now().astimezone()
    return "\n".join(
        [
            REAL_TIME_CONTEXT_MARKER,
            f"mode: {mode}",
            f"now: {now.isoformat()}",
        ]
    )


def augment_user_message(message: InputMessage, *, mode: SessionMode) -> InputMessage:
    prefix = real_time_turn_context(mode=mode) + _REAL_TIME_CONTEXT_SEPARATOR
    content: list[ContentItem] = []
    for item in message.content:
        if item.kind == "text" and item.text and not content:
            content.append(ContentItem(kind="text", text=prefix + item.text, meta=item.meta))
            continue
        content.append(item)
    if not content:
        content.append(ContentItem(kind="text", text=prefix.rstrip()))
    return InputMessage(role=message.role, name=message.name, content=content)


def user_visible_text(message: InputMessage) -> str:
    parts: list[str] = []
    for item in message.content:
        if item.kind != "text" or not item.text:
            continue
        text = item.text
        if text.startswith(REAL_TIME_CONTEXT_MARKER):
            _, _, remainder = text.partition(_REAL_TIME_CONTEXT_SEPARATOR)
            text = remainder
        parts.append(text)
    return "\n\n".join(parts)
