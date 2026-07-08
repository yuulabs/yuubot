import inspect
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Literal

from ..domain.messages import ContentItem, InputMessage
from ..integrations.registry import Integration
from ..runtime.skills import SkillSummary
from .prompt_docs import ADMIN_PAGES

_RUNTIME_FACADE_PACKAGES = ("yb.tasks", "yb.tasks.cron", "yb.mcps", "yb.skills")

SessionMode = Literal["conversation", "actor"]
REAL_TIME_CONTEXT_MARKER = "[yuubot-real-time-context]"
_REAL_TIME_CONTEXT_SEPARATOR = "\n---\n"


def developer_prompt(
    persona: str,
    workspace: Path,
    integrations: list[Integration],
    *,
    actor_id: str = "",
    has_python: bool,
    global_skills: list[SkillSummary] | None = None,
) -> str:
    sections = [
        "# Persona\n" + (persona.strip() or "You are a yuubot actor."),
        "# System Instructions\n" + _system_instructions(has_python),
        "# Workspace Instructions\n" + _workspace_instructions(workspace, actor_id=actor_id),
    ]
    if has_python:
        sections.append("# Tool Suggestions\n" + _tool_suggestions())
    extra_packages = _RUNTIME_FACADE_PACKAGES if has_python else ()
    integration_docs = _integration_docs(integrations, extra_packages)
    if integration_docs:
        sections.append("# Integration SDKs\n" + integration_docs)
    sections.append("# Skills\n" + _skills(workspace, global_skills or []))
    sections.append("# AGENTS.md Context\n" + _agents_context(workspace))
    sections.append("# Real-Time Data\n" + _real_time_data())
    return "\n\n".join(sections)


def _system_instructions(has_python: bool) -> str:
    lines = [
        "Session mode semantics are documented in Real-Time Data. Context may be lost between turns; rely on persisted conversation history and workspace files when you need durable memory.",
        "Do not expose secrets, raw integration credentials, or daemon implementation details to users.",
    ]
    if has_python:
        lines.append("execute_python resets after each user turn; a developer notice appears when a prior session is gone.")
    return "\n".join(lines)


def _workspace_instructions(workspace: Path, *, actor_id: str = "") -> str:
    lines = [f"Workspace path: {workspace}"]
    if actor_id:
        lines.append(f"Actor id: {actor_id}")
    lines.extend(
        [
            "- `artifacts/`: user-visible outputs.",
            "- `uploads/`: uploaded files grouped by MIME type.",
            "- `projects/`: actor-managed project files.",
            "- `notes/`: durable actor notes.",
            "- `scripts/`: helper scripts.",
            "- `.agents/skills/`: skill files you may inspect with the read tool.",
        ]
    )
    return "\n".join(lines)


def _tool_suggestions() -> str:
    return "\n".join(
        [
            "Prefer execute_python for multi-step local work, data shaping, and integration facade calls. It runs an IPython session with native top-level await.",
            "Examples: `await yext.web.search(...)`, `await yext.web.read(...)`, `yext.github.repo().issues.list_recent(...)`, and `yb.office.pdf.to_markdown(...)`.",
            "Use the `bash` tool for commands that may prompt, block, or need stdin. It runs in a PTY, streams output, detaches when idle, and returns a task id for `task.output()`, `task.write(text)`, and `task.cancel()`.",
            "Register background shell work with `await yb.tasks.submit(name, shell, intro, delivery=...)`. `manual` — poll with `task.output()` / `task.status()` yourself (`ttl_s <= 3600`). `conversation` — completion wakes this chat. `actor` — completion goes to the actor mailbox.",
            "Task output is an expiring offload buffer. For long jobs, write resumable workspace scripts that persist their own state and artifacts.",
            "MCP data sources: use `yb.mcps` (see Integration SDKs). Durable schedules: use `yb.tasks.cron`.",
            ADMIN_PAGES,
            "After `uv add` or `uv remove`, call the `restart_kernel` tool before expecting new imports in execute_python.",
        ]
    )


def _integration_docs(integrations: list[Integration], extra_packages: tuple[str, ...]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for integration in integrations:
        prompt_doc = getattr(integration, "prompt_doc", None)
        if callable(prompt_doc):
            doc = prompt_doc()
        else:
            doc = inspect.getdoc(import_module(integration.package_path)) or ""
        if doc:
            parts.append(f"{integration.package_path}:\n{doc}")
        seen.add(integration.package_path)
    for package_path in extra_packages:
        if package_path in seen:
            continue
        doc = inspect.getdoc(import_module(package_path)) or ""
        if doc:
            parts.append(f"{package_path}:\n{doc}")
    return "\n\n".join(parts)


def _skills(workspace: Path, global_skills: list[SkillSummary]) -> str:
    skills_dir = workspace / ".agents" / "skills"
    entries: list[str] = []
    for skill in global_skills:
        description = skill.description or "No description provided."
        entries.append(f"- {skill.name} ({skill.id}): {description}. Inspect full instructions via {skill.inspect_hint}.")
    for skill_path in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill_path.read_text(encoding="utf-8")
        entries.append(f"- {skill_path.parent.name}: {_skill_description(text)}. Inspect full instructions with the read tool at `{skill_path}`.")
    if not entries:
        return "No workspace skills are currently installed."
    return "The following skills are summaries only; inspect full instructions on demand:\n" + "\n".join(entries)


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
