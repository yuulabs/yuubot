import inspect
from datetime import datetime
from importlib import import_module
from pathlib import Path

_RUNTIME_FACADE_PACKAGES = ("yb.tasks",)


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
        "Mode Description: You are running inside a yuubot Conversation. Context may be lost between turns; rely on persisted conversation history and workspace files when you need durable memory.",
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
    now = datetime.now().astimezone()
    return f"platform: local\nnow: {now.isoformat()}\ntimezone: {now.tzname() or 'local'}"
